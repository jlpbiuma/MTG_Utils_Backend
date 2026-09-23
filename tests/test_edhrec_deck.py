from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown
from src.services.edhrec_deck_service import get_recommended_deck, select_recommended_cards

NOW = datetime.now(timezone.utc)


def commander(**kwargs):
    values = dict(id="cmd", name="Lathril", slug="lathril", status="synced", colorIdentity="BG",
                  createdAt=NOW, updatedAt=NOW, creatureCount=2, instantCount=0, sorceryCount=0,
                  artifactCount=0, enchantmentCount=0, planeswalkerCount=0, nonbasicLandCount=0,
                  battleCount=0, basicLandCount=0, canonicalCardNames=[], cardsJson={"creatures": [
                      {"name": "Popular Elf", "inclusionPct": 99},
                      {"name": "Other Elf", "inclusionPct": 80},
                      {"name": "Owned Elf", "inclusionPct": 1},
                  ]})
    return NS(**(values | kwargs))


def physical(**kwargs):
    return NS(**(dict(cardName="Owned Elf", cardScryfallId="owned-print", quantity=5,
                     isFoil=False, imageUri=None, setCode="set") | kwargs))


def database(cmd=None, collection=None, printings=None):
    db = MagicMock()
    db.edhreccommander.find_unique = AsyncMock(return_value=cmd or commander())
    db.collectioncard.find_many = AsyncMock(return_value=collection or [])
    db.cardcatalog.find_many = AsyncMock(return_value=[])
    db.cardprinting.find_many = AsyncMock(return_value=printings or [])
    db.deckcard.find_many = AsyncMock(return_value=[])
    db.wantcard.find_many = AsyncMock(return_value=[])
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("foil, expected", [(False, 12.5), (True, 35.0)])
async def test_preview_prices_only_used_copies_of_owned_printing_and_finish(foil, expected):
    db = database(collection=[physical(isFoil=foil)], printings=[NS(
        id="owned-print", priceEur=10.0, priceCardmarketTrend=12.5,
        priceEurFoil=35.0, pricesUpdatedAt=NOW, imageUri=None,
    )])
    missing = CardPriceQuote(cardName="Popular Elf", scryfallId="missing-print",
                             unitPrice=UnitPriceBreakdown(trend=3), lastUpdated=NOW)
    with patch("src.services.edhrec_deck_service.db", db), patch(
        "src.services.edhrec_deck_service.PricingService.get_latest_quotes_batch",
        new_callable=AsyncMock, return_value={"popular elf": missing},
    ) as prices:
        detail = await get_recommended_deck("lathril", "user-a")
    assert [c.cardName for c in detail.cards] == ["Popular Elf", "Other Elf", "Owned Elf", "Lathril"]
    assert detail.cards[2].ownedInCollection == 5
    assert detail.cards[2].quantity == 1
    assert detail.ownedValue == expected
    assert detail.missingValue == 3
    assert detail.totalValue == expected + 3
    assert detail.completionPercentage == 50
    assert detail.unpricedCards == 1
    db.collectioncard.find_many.assert_awaited_once_with(where={"userId": "user-a"})
    # Owned copies must never use a cheaper reprint's price.
    prices.assert_awaited_once_with([{"name": "Popular Elf"}, {"name": "Other Elf"}, {"name": "Lathril"}], "cardmarket", "EUR")
    db.deck.create.assert_not_called()
    db.deckcard.create.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_prices_are_reported_and_short_lists_keep_required_slots():
    db = database(cmd=commander(creatureCount=4), collection=[physical()])
    with patch("src.services.edhrec_deck_service.db", db), patch(
        "src.services.edhrec_deck_service.PricingService.get_latest_quotes_batch",
        new_callable=AsyncMock, return_value={},
    ):
        detail = await get_recommended_deck("lathril", "user-a")
    assert detail.unpricedCards == 4
    assert detail.unfilledSlots == 1
    assert detail.ownedValue == 0
    assert detail.totalCards == 4
    assert detail.completionPercentage == 25


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "error", "not_found"])
async def test_unsynced_recommendations_are_unavailable(status):
    db = database(cmd=commander(status=status))
    with patch("src.services.edhrec_deck_service.db", db):
        assert await get_recommended_deck("lathril", "user-a") is None
    db.collectioncard.find_many.assert_not_called()


def test_selection_respects_quotas_deduplicates_and_excludes_basics():
    selected, required = select_recommended_cards(commander(
        creatureCount=1, artifactCount=1, nonbasicLandCount=1,
        cardsJson={"creatures": [{"name": "Hybrid"}, {"name": "Hybrid"}],
                   "artifacts": [{"name": "Hybrid"}, {"name": "Rock"}],
                   "lands": [{"name": "Forest"}, {"name": "Command Tower"}],
                   "instants": [{"name": "Spell"}]},
    ), {"hybrid", "rock", "forest"})
    assert [n for n, _, _ in selected] == ["hybrid", "rock", "command tower"]
    assert required == 3


@pytest.mark.asyncio
async def test_detail_route_404():
    with patch("src.routers.edhrec.build_recommended_deck", new_callable=AsyncMock, return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/api/edhrec/commanders/missing/deck")
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_vivi_lists_all_creatures_and_merges_top_synergy_flags():
    cards = [{"name": f"Creature {i}", "inclusionPct": 90 - i, "synergy": i} for i in range(8)]
    cmd = commander(name="Vivi Ornitier", creatureCount=2, cardsJson={
        "creatures": cards,
        "topcards": [cards[6], cards[6]],
        "highsynergycards": [cards[6], cards[7]],
        "instants": [{"name": "Zero quota instant"}],
    })
    db = database(cmd=cmd, collection=[physical(cardName="Creature 6")])
    with patch("src.services.edhrec_deck_service.db", db), patch(
        "src.services.edhrec_deck_service.PricingService.get_latest_quotes_batch",
        new_callable=AsyncMock, return_value={},
    ):
        detail = await get_recommended_deck("vivi-ornitier", "user-a")
    creatures = [c for c in detail.cards if c.edhrecCategory == "creatures"]
    assert len(creatures) == 8  # never truncate alternatives to the quota
    assert detail.typeQuotas["creatures"] == 2
    assert detail.typeQuotas["instants"] == 0
    assert len(detail.cards) == 10
    featured = next(c for c in creatures if c.cardName == "Creature 6")
    assert featured.isTopCard and featured.isHighSynergy
    assert featured.inclusionPct == 84
    assert featured.synergy == 6
    assert featured.ownedInCollection == 5
    assert detail.ownedCards == 1
    assert detail.totalCards == 2


@pytest.mark.asyncio
async def test_list_values_coverage_and_detail_agree_with_one_price_batch():
    from src.services.edhrec_service import EdhrecService
    cmd = commander(normalizedName="lathril", isTop100=True, rank=1, numDecks=100,
                    landCount=0, cardsJson={
        "creatures": [{"name": "Owned Elf"}, {"name": "Popular Elf"}, {"name": "Extra Elf"}],
        "topcards": [{"name": "OWNED ELF"}, {"name": "Owned Elf"}, {"name": "Popular Elf"}],
        "highsynergycards": [{"name": "Owned Elf"}, {"name": "Extra Elf"}, {"name": "Third Elf"}],
    })
    db = database(cmd=cmd, collection=[physical()], printings=[NS(
        id="owned-print", priceEur=10, priceCardmarketTrend=12.5,
        priceEurFoil=35, pricesUpdatedAt=NOW,
    )])
    db.deck.find_many = AsyncMock(return_value=[])
    db.deckcard.find_many = AsyncMock(return_value=[])
    db.edhreccommander.find_many = AsyncMock(return_value=[cmd])
    quote = CardPriceQuote(cardName="Extra Elf", unitPrice=UnitPriceBreakdown(trend=3), lastUpdated=NOW)
    with patch("src.services.edhrec_service.db", db), patch("src.services.edhrec_deck_service.db", db), patch(
        "src.services.edhrec_deck_service.PricingService.get_latest_quotes_batch",
        new_callable=AsyncMock, return_value={"extra elf": quote},
    ) as prices:
        listing = await EdhrecService.get_commander_recommendations_for_user("user-a")
        assert prices.await_count == 1
        summary = listing.commanders[0]
        assert summary.ownedValue == 12.5
        assert summary.missingValue == 3
        assert summary.topCardsCoverage.owned == 1
        assert summary.topCardsCoverage.total == 2
        assert summary.topCardsCoverage.percentage == 50
        assert summary.highSynergyCoverage.percentage == 33.3
        detail = await get_recommended_deck("lathril", "user-a")
    assert detail.ownedValue == summary.ownedValue
    assert detail.missingValue == summary.missingValue
    assert detail.completionPercentage == summary.completionPercentage


def test_absent_special_groups_have_no_percentage():
    from src.services.edhrec_deck_service import group_coverage
    coverage = group_coverage(commander(), "topcards", {"owned elf"})
    assert coverage.total == 0
    assert coverage.percentage is None
