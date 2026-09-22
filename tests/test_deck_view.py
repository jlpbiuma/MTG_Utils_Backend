from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.auth import get_current_user_id
from src.routers.decks import router
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown
from src.services.deck_view_service import DeckViewService


def card(name="Sol Ring", **changes):
    values = dict(id="c1", deckId="d1", cardScryfallId="s1", cardName=name,
                  quantity=4, assignedQuantity=1, isSideboard=False, isCommander=False,
                  manaCost="{1}", typeLine=None, imageUri=None, setCode="MH3")
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.fixture
def data():
    deck = SimpleNamespace(id="d1", userId="u1", name="Deck", format="Commander",
        description=None, commander=None, commanderScryfallId=None, commanderImageUri=None,
        createdAt=datetime.now(timezone.utc), updatedAt=datetime.now(timezone.utc), cards=[card()])
    database = MagicMock()
    database.deck.find_first = AsyncMock(return_value=deck)
    database.query_raw = AsyncMock(side_effect=[
        [{"name": "sol ring", "quantity": 3}],
        [{"name": "sol ring", "deckId": "d2", "deckName": "Other", "quantity": 1}],
    ])
    database.deckcard.update = AsyncMock()
    quote = CardPriceQuote(cardName="Sol Ring", scryfallId="s1",
                          unitPrice=UnitPriceBreakdown(trend=2), lastUpdated=deck.updatedAt)
    with patch("src.services.deck_view_service.db", database), \
         patch("src.services.deck_view_service.resolve_minio_image_uris", AsyncMock(return_value={})) as images, \
         patch("src.services.deck_view_service.PricingService.get_latest_quotes_batch", AsyncMock(return_value={"s1": quote})) as prices, \
         patch("src.services.pricing_service.PricingService.get_price_summary", AsyncMock(side_effect=AssertionError("No remote pricing"))) as remote_prices, \
         patch("src.services.scryfall_service.ScryfallService.get_or_resolve_catalog_card", AsyncMock(side_effect=AssertionError("No enrichment"))) as enrich:
        yield deck, database, images, prices, remote_prices, enrich


@pytest.mark.asyncio
async def test_view_is_local_minimal_and_preserves_ownership(data):
    deck, db, images, prices, remote, enrich = data
    deck.cards += [card("Island", id="land", quantity=10, assignedQuantity=0, cardScryfallId="land"),
                   card("Reserve", id="side", quantity=2, assignedQuantity=0, cardScryfallId="side", isSideboard=True)]
    view = await DeckViewService.get_view("d1", "u1")
    assert view.totalCards == 14  # Sideboard excluded, basic lands always owned.
    assert view.ownedCards == 13
    assert view.missingCards == 1
    assert view.cards[0].ownedInCollection == 3
    assert view.cards[0].availableToAssign == 1
    assert view.cards[0].assignedInOtherDecks[0].deckId == "d2"
    assert view.cards[1].missingCount == 0
    assert view.cards[2].missingCount == 2
    assert view.priceSummary.totalNetValue == 8
    assert view.priceSummary.totalOwnedValue == 6
    assert view.priceSummary.totalMissingValue == 2
    assert set(view.priceSummary.quotes) == {"s1"}
    assert "requestedInDecks" not in view.cards[0].model_dump()
    assert "canBeCommander" not in view.cards[0].model_dump()
    db.deck.find_first.assert_awaited_once_with(where={"id": "d1", "userId": "u1"}, include={"cards": True})
    assert db.query_raw.await_count == 2
    assert db.query_raw.call_args_list[0].args[1] == "u1"
    assert db.query_raw.call_args_list[1].args[1:3] == ("u1", "d1")
    prices.assert_awaited_once()
    images.assert_awaited_once()
    remote.assert_not_awaited()
    enrich.assert_not_awaited()
    db.deckcard.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_owner_does_not_read_collection_or_prices(data):
    _, db, images, prices, _, _ = data
    db.deck.find_first.return_value = None
    assert await DeckViewService.get_view("d1", "another-user") is None
    db.deck.find_first.assert_awaited_once_with(where={"id": "d1", "userId": "another-user"}, include={"cards": True})
    db.query_raw.assert_not_awaited()
    prices.assert_not_awaited()
    images.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_deck_avoids_ownership_queries(data):
    deck, db, _, _, _, _ = data
    deck.cards = []
    view = await DeckViewService.get_view("d1", "u1")
    assert view.cards == []
    assert view.completionPercentage == 0
    db.query_raw.assert_not_awaited()


@pytest.mark.asyncio
async def test_commander_identity_is_read_from_local_catalog(data):
    deck, db, _, prices, _, enrich = data
    deck.cards[0].isCommander = True
    db.query_raw.side_effect = [[{"name": "sol ring", "quantity": 3}], [], [{"identity": ["R", "U"]}]]
    view = await DeckViewService.get_view("d1", "u1", "mtggoldfish")
    assert view.commanderColorIdentity == ["U", "R"]
    assert view.priceSummary.currency == "USD"
    assert prices.call_args.args[1:] == ("mtggoldfish", "USD")
    enrich.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_contract_auth_scope_and_invalid_provider(data):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/d1/view")
        assert response.status_code == 200
        assert response.json()["priceSummary"]["totalNetValue"] == 8
        assert "requestedInDecks" not in response.json()["cards"][0]
        assert (await client.get("/api/decks/d1/view?provider=invalid")).status_code == 422
        data[1].deck.find_first.return_value = None
        assert (await client.get("/api/decks/d1/view")).status_code == 404
