from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

import pytest

from src.schemas.deck import DeckRequirement
from src.schemas.wants import WantCardCreate, WantCardResponse
from src.services.want_service import WantAlreadyOwnedError, WantService


def _want_row(**overrides):
    base = dict(
        id="want-1",
        userId="user-1",
        cardScryfallId="scry-1",
        cardName="Faeburrow Elder",
        quantity=1,
        setCode="C20",
        collectorNumber="1",
        manaCost="{1}{G}{W}",
        typeLine="Creature — Treefolk Druid",
        imageUri=None,
        updatedAt=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_add_or_increment_want_creates_new_entry():
    created = _want_row()
    fake_db = SimpleNamespace(
        wantcard=SimpleNamespace(
            find_unique=AsyncMock(return_value=None),
            create=AsyncMock(return_value=created),
        ),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with (
        patch("src.services.want_service.db", new=fake_db),
        patch(
            "src.services.want_service.ScryfallService.get_catalog_card",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.services.want_service.trigger_async_priority_enrichment",
        ),
    ):
        result = await WantService.add_or_increment(
            "user-1",
            WantCardCreate(
                cardScryfallId="scry-1",
                cardName="Faeburrow Elder",
                quantity=1,
            ),
        )

    assert result.cardName == "Faeburrow Elder"
    assert result.quantity == 1
    fake_db.wantcard.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_or_increment_want_increments_existing():
    existing = _want_row(quantity=1)
    updated = _want_row(quantity=2)
    fake_db = SimpleNamespace(
        wantcard=SimpleNamespace(
            find_unique=AsyncMock(return_value=existing),
            update=AsyncMock(return_value=updated),
        ),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with (
        patch("src.services.want_service.db", new=fake_db),
        patch("src.services.want_service.trigger_async_priority_enrichment"),
    ):
        result = await WantService.add_or_increment(
            "user-1",
            WantCardCreate(
                cardScryfallId="scry-1",
                cardName="Faeburrow Elder",
                quantity=1,
            ),
        )

    assert result.quantity == 2
    fake_db.wantcard.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_or_increment_refuses_a_card_already_in_the_collection():
    fake_db = SimpleNamespace(
        wantcard=SimpleNamespace(
            find_unique=AsyncMock(),
            create=AsyncMock(),
        ),
        collectioncard=SimpleNamespace(
            find_many=AsyncMock(
                return_value=[SimpleNamespace(cardName="Faeburrow Elder", quantity=1)]
            )
        ),
    )

    with patch("src.services.want_service.db", new=fake_db):
        with pytest.raises(WantAlreadyOwnedError):
            await WantService.add_or_increment(
                "user-1",
                WantCardCreate(
                    cardScryfallId="other-printing",
                    cardName="faeburrow elder",
                    quantity=1,
                ),
            )

    fake_db.wantcard.create.assert_not_called()


@pytest.mark.asyncio
async def test_list_wants_includes_requested_in_decks_by_normalized_name():
    want = _want_row()
    other_deck = SimpleNamespace(id="deck-2", name="Tidus Voltron")
    user_deck_cards = [
        SimpleNamespace(
            cardName="Faeburrow Elder",
            quantity=1,
            deckId="deck-2",
            deck=other_deck,
        )
    ]

    fake_db = SimpleNamespace(
        wantcard=SimpleNamespace(find_many=AsyncMock(return_value=[want])),
        deckcard=SimpleNamespace(find_many=AsyncMock(return_value=user_deck_cards)),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with (
        patch("src.services.want_service.db", new=fake_db),
        patch(
            "src.services.want_service.resolve_minio_image_uris",
            new=AsyncMock(return_value={}),
        ),
    ):
        result = await WantService.get_user_wants("user-1")

    assert len(result) == 1
    assert result[0].requestedInDecksCount == 1
    assert result[0].requestedInDecks[0].deckName == "Tidus Voltron"


@pytest.mark.asyncio
async def test_ungrouped_wants_query_returns_cards_in_the_listing():
    item = WantCardResponse(
        id="want-1",
        userId="user-1",
        cardScryfallId="scry-1",
        cardName="Faeburrow Elder",
        quantity=1,
        updatedAt=datetime.now(timezone.utc),
        requestedInDecks=[
            DeckRequirement(deckId="deck-2", deckName="Tidus Voltron", quantity=1)
        ],
        requestedInDecksCount=1,
    )

    with (
        patch.object(WantService, "get_user_wants", new=AsyncMock(return_value=[item])),
        patch(
            "src.services.want_service.PricingService.get_price_summary",
            new=AsyncMock(side_effect=RuntimeError("prices down")),
        ),
    ):
        result = await WantService.get_user_wants_query("user-1", grouped=False)

    assert result.grouped is False
    assert [card.cardName for card in result.cards] == ["Faeburrow Elder"]
    assert result.cards[0].requestedInDecksCount == 1
    assert result.sections == []


def _want_response(name: str, count: int) -> WantCardResponse:
    return WantCardResponse(
        id=name,
        userId="user-1",
        cardScryfallId=name,
        cardName=name,
        quantity=1,
        updatedAt=datetime.now(timezone.utc),
        requestedInDecks=[
            DeckRequirement(deckId=f"d-{i}", deckName=f"Deck {i}", quantity=1)
            for i in range(count)
        ],
        requestedInDecksCount=count,
    )


@pytest.mark.asyncio
async def test_wants_query_sorts_by_requested_decks_desc():
    items = [
        _want_response("Sol Ring", 2),
        _want_response("Arcane Signet", 7),
        _want_response("Counterspell", 0),
    ]
    with (
        patch.object(WantService, "get_user_wants", new=AsyncMock(return_value=items)),
        patch(
            "src.services.want_service.PricingService.get_price_summary",
            new=AsyncMock(side_effect=RuntimeError("prices down")),
        ),
    ):
        result = await WantService.get_user_wants_query(
            "user-1", sort="requested_decks", direction="desc", grouped=False
        )

    assert [card.cardName for card in result.cards] == [
        "Arcane Signet",
        "Sol Ring",
        "Counterspell",
    ]
