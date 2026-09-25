"""Regression: art series / digital printings must never win cheapest or pass exclusion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pricing_service import PricingService
from src.services.priority_service import PriorityService
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown
from datetime import datetime


def test_is_excluded_printing_blocks_art_memorabilia_arena_alchemy():
    art_set = SimpleNamespace(code="afin", setType="memorabilia", isDigital=False)
    art_printing = SimpleNamespace(
        collectorNumber="1",
        set=art_set,
        catalog=SimpleNamespace(name="Cloud // Cloud", typeLine="Card // Card", setCode="afin"),
    )
    assert PricingService._is_excluded_printing(art_printing) is True

    arena_set = SimpleNamespace(code="aa1", setType="box", isDigital=False)
    arena_printing = SimpleNamespace(
        collectorNumber="10",
        set=arena_set,
        catalog=SimpleNamespace(name="Sol Ring", typeLine="Artifact", setCode="aa1"),
    )
    assert PricingService._is_excluded_printing(arena_printing) is True

    alchemy_set = SimpleNamespace(code="y22", setType="alchemy", isDigital=False)
    alchemy_printing = SimpleNamespace(
        collectorNumber="1",
        set=alchemy_set,
        catalog=SimpleNamespace(name="Card", typeLine="Creature", setCode="y22"),
    )
    assert PricingService._is_excluded_printing(alchemy_printing) is True

    digital_set = SimpleNamespace(code="mh3", setType="expansion", isDigital=True)
    digital_printing = SimpleNamespace(
        collectorNumber="1",
        set=digital_set,
        catalog=SimpleNamespace(name="Card", typeLine="Creature", setCode="mh3"),
    )
    assert PricingService._is_excluded_printing(digital_printing) is True

    a_collector = SimpleNamespace(
        collectorNumber="A-246",
        set=SimpleNamespace(code="ltr", setType="expansion", isDigital=False),
        catalog=SimpleNamespace(name="The One Ring", typeLine="Legendary Artifact", setCode="ltr"),
    )
    assert PricingService._is_excluded_printing(a_collector) is True

    playable = SimpleNamespace(
        collectorNumber="2",
        set=SimpleNamespace(code="fic", setType="commander", isDigital=False),
        catalog=SimpleNamespace(
            name="Cloud, Ex-SOLDIER",
            typeLine="Legendary Creature — Human Soldier Mercenary",
            setCode="fic",
        ),
    )
    assert PricingService._is_excluded_printing(playable) is False


@pytest.mark.asyncio
async def test_cheapest_printings_orm_fallback_skips_art_series():
    """ORM fallback must never select an art-series printing as cheapest."""
    catalog = MagicMock()
    catalog.normalizedName = "cloud, ex-soldier"
    catalog.name = "Cloud, Ex-SOLDIER"
    catalog.typeLine = "Legendary Creature — Human Soldier Mercenary"
    catalog.setCode = "fic"

    art_catalog = MagicMock()
    art_catalog.normalizedName = "cloud, ex-soldier"
    art_catalog.name = "Cloud, Ex-SOLDIER // Cloud, Ex-SOLDIER"
    art_catalog.typeLine = "Card // Card"
    art_catalog.setCode = "afin"

    p_art = MagicMock()
    p_art.id = "cloud-art-cheap"
    p_art.catalog = art_catalog
    p_art.collectorNumber = "1"
    p_art.set = SimpleNamespace(code="afin", setType="memorabilia", isDigital=False)
    p_art.priceEur = 0.10
    p_art.priceCardmarketTrend = 0.10
    p_art.updatedAt = datetime.now()

    p_playable = MagicMock()
    p_playable.id = "cloud-playable"
    p_playable.catalog = catalog
    p_playable.collectorNumber = "2"
    p_playable.set = SimpleNamespace(code="fic", setType="commander", isDigital=False)
    p_playable.priceEur = 5.0
    p_playable.priceCardmarketTrend = 5.0
    p_playable.updatedAt = datetime.now()

    mock_db = MagicMock()
    mock_db.query_raw = AsyncMock(side_effect=RuntimeError("sql unavailable"))
    mock_db.cardprinting.find_many = AsyncMock(return_value=[p_art, p_playable])

    with patch("src.services.pricing_service.db", mock_db):
        by_norm = await PricingService._cheapest_printings_by_norm(["cloud, ex-soldier"])

    assert "cloud, ex-soldier" in by_norm
    assert by_norm["cloud, ex-soldier"].id == "cloud-playable"
    assert by_norm["cloud, ex-soldier"].id != "cloud-art-cheap"


@pytest.mark.asyncio
async def test_priority_never_selects_art_series_as_cheapest():
    """Priorities must pick the playable reprint, not a cheaper art-series printing."""
    now = datetime.now()

    deck_card = MagicMock()
    deck_card.id = "deck-card-1"
    deck_card.cardName = "Cloud, Ex-SOLDIER"
    deck_card.cardScryfallId = "cloud-playable-expensive"
    deck_card.quantity = 1
    deck_card.assignedQuantity = 0
    deck_card.imageUri = "https://images.example.com/playable.jpg"
    deck_card.manaCost = "{2}{W}{U}"
    deck_card.typeLine = "Legendary Creature — Human Soldier Mercenary"

    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "Cloud Deck"
    deck.cards = [deck_card]

    catalog = MagicMock()
    catalog.normalizedName = "cloud, ex-soldier"
    catalog.name = "Cloud, Ex-SOLDIER"
    catalog.typeLine = "Legendary Creature — Human Soldier Mercenary"
    catalog.setCode = "fic"

    art_catalog = MagicMock()
    art_catalog.normalizedName = "cloud, ex-soldier"
    art_catalog.name = "Cloud, Ex-SOLDIER // Cloud, Ex-SOLDIER"
    art_catalog.typeLine = "Card // Card"
    art_catalog.setCode = "afin"

    p_art = MagicMock()
    p_art.id = "cloud-art-cheap"
    p_art.catalog = art_catalog
    p_art.collectorNumber = "1"
    p_art.set = SimpleNamespace(code="afin", setType="memorabilia", isDigital=False)
    p_art.priceEur = 0.15
    p_art.priceCardmarketTrend = 0.15
    p_art.imageUri = "https://images.example.com/art.jpg"
    p_art.imageUriLarge = None
    p_art.imageUriSmall = None
    p_art.updatedAt = now

    p_playable = MagicMock()
    p_playable.id = "cloud-playable"
    p_playable.catalog = catalog
    p_playable.collectorNumber = "2"
    p_playable.set = SimpleNamespace(code="fic", setType="commander", isDigital=False)
    p_playable.priceEur = 4.50
    p_playable.priceCardmarketTrend = 4.50
    p_playable.imageUri = "https://images.example.com/playable.jpg"
    p_playable.imageUriLarge = None
    p_playable.imageUriSmall = None
    p_playable.updatedAt = now

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[p_art, p_playable])
    mock_db.query_raw = AsyncMock(side_effect=RuntimeError("sql unavailable in unit tests"))

    fake_quote = CardPriceQuote(
        scryfallId="cloud-playable",
        cardName="Cloud, Ex-SOLDIER",
        provider="cardmarket",
        currency="EUR",
        currencySymbol="€",
        unitPrice=UnitPriceBreakdown(trend=4.50, min=4.0, max=5.0),
        quantity=1,
        subtotal=4.50,
        lastUpdated=now,
    )
    fake_price_summary = MagicMock()
    fake_price_summary.quotes = {
        "cloud-playable": fake_quote,
        "cloud, ex-soldier": fake_quote,
    }

    with patch("src.services.priority_service.db", mock_db), \
         patch("src.services.pricing_service.db", mock_db), \
         patch(
             "src.services.priority_service.PricingService.get_price_summary",
             new_callable=AsyncMock,
             return_value=fake_price_summary,
         ):
        res = await PriorityService.get_priorities(user_id="user-1")

    assert len(res.items) == 1
    item = res.items[0]
    assert item.cardScryfallId == "cloud-playable"
    assert item.cardScryfallId != "cloud-art-cheap"
    assert item.imageUri == "https://images.example.com/playable.jpg"
    assert item.price == 4.50
