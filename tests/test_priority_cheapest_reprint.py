import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.services.priority_service import PriorityService
from src.services.pricing_service import PricingService
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown


@pytest.mark.asyncio
async def test_priority_selects_cheapest_non_zero_reprint():
    """Verify PriorityService always picks the cheapest reprint with price > 0.0,
    rather than a random/arbitrary deck printing or a 0.0 EUR printing."""
    now = datetime.now()

    # Deck asking for Sol Ring with an arbitrary expensive foil printing
    deck_card = MagicMock()
    deck_card.id = "deck-card-1"
    deck_card.cardName = "Sol Ring"
    deck_card.cardScryfallId = "sol-ring-expensive-foil"
    deck_card.quantity = 1
    deck_card.assignedQuantity = 0
    deck_card.imageUri = "https://images.example.com/foil.jpg"
    deck_card.manaCost = "{1}"
    deck_card.typeLine = "Artifact"

    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "My Commander Deck"
    deck.cards = [deck_card]

    # Database printings for Sol Ring
    catalog = MagicMock()
    catalog.normalizedName = "sol ring"
    catalog.name = "Sol Ring"

    p_zero = MagicMock()
    p_zero.id = "sol-ring-promo-zero"
    p_zero.catalog = catalog
    p_zero.priceEur = 0.0
    p_zero.priceCardmarketTrend = 0.0
    p_zero.priceUsd = 0.0
    p_zero.imageUri = "https://images.example.com/promo.jpg"
    p_zero.imageUriLarge = None
    p_zero.imageUriSmall = None

    p_expensive = MagicMock()
    p_expensive.id = "sol-ring-expensive-foil"
    p_expensive.catalog = catalog
    p_expensive.priceEur = 65.0
    p_expensive.priceCardmarketTrend = 65.0
    p_expensive.priceUsd = 70.0
    p_expensive.imageUri = "https://images.example.com/foil.jpg"
    p_expensive.imageUriLarge = None
    p_expensive.imageUriSmall = None

    p_cheapest = MagicMock()
    p_cheapest.id = "sol-ring-cheapest-reprint"
    p_cheapest.catalog = catalog
    p_cheapest.priceEur = 1.15
    p_cheapest.priceCardmarketTrend = 1.15
    p_cheapest.priceUsd = 1.50
    p_cheapest.imageUri = "https://images.example.com/cheapest.jpg"
    p_cheapest.imageUriLarge = None
    p_cheapest.imageUriSmall = None

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])  # Not owned -> deficit = 1
    mock_db.cardprinting.find_many = AsyncMock(return_value=[p_zero, p_expensive, p_cheapest])
    mock_db.query_raw = AsyncMock(side_effect=RuntimeError("sql unavailable in unit tests"))

    fake_quote = CardPriceQuote(
        scryfallId="sol-ring-cheapest-reprint",
        cardName="Sol Ring",
        provider="cardmarket",
        currency="EUR",
        currencySymbol="€",
        unitPrice=UnitPriceBreakdown(trend=1.15, min=1.0, max=2.0),
        quantity=1,
        subtotal=1.15,
        lastUpdated=now,
    )

    fake_price_summary = MagicMock()
    fake_price_summary.quotes = {
        "sol-ring-cheapest-reprint": fake_quote,
        "sol ring": fake_quote,
    }

    with patch("src.services.priority_service.db", mock_db), \
         patch("src.services.pricing_service.db", mock_db), \
         patch("src.services.priority_service.PricingService.get_price_summary", new_callable=AsyncMock, return_value=fake_price_summary):

        res = await PriorityService.get_priorities(user_id="user-1")

        assert len(res.items) == 1
        item = res.items[0]

        # Must NOT be the arbitrary expensive foil
        assert item.cardScryfallId != "sol-ring-expensive-foil"
        # Must NOT be the 0.0 EUR promo
        assert item.cardScryfallId != "sol-ring-promo-zero"
        # MUST be the cheapest reprint
        assert item.cardScryfallId == "sol-ring-cheapest-reprint"
        assert item.price == 1.15
        assert item.imageUri == "https://images.example.com/cheapest.jpg"
        assert item.deficit == 1
        assert item.totalDeficitCost == 1.15


@pytest.mark.asyncio
async def test_pricing_batch_prefers_exact_printing_when_priced():
    """When the requested scryfallId has a positive price, do not override to cheapest."""
    catalog = MagicMock()
    catalog.normalizedName = "demonic tutor"

    p_expensive = MagicMock()
    p_expensive.id = "dt-expensive"
    p_expensive.catalog = catalog
    p_expensive.collectorNumber = "1"
    p_expensive.set = None
    p_expensive.priceEur = 120.0
    p_expensive.priceCardmarketTrend = 120.0
    p_expensive.priceCardmarketMin = 100.0
    p_expensive.priceCardmarketMax = 150.0
    p_expensive.pricesUpdatedAt = datetime.now()

    p_cheap = MagicMock()
    p_cheap.id = "dt-cheap"
    p_cheap.catalog = catalog
    p_cheap.collectorNumber = "2"
    p_cheap.set = None
    p_cheap.priceEur = 28.50
    p_cheap.priceCardmarketTrend = 28.50
    p_cheap.priceCardmarketMin = 25.0
    p_cheap.priceCardmarketMax = 35.0
    p_cheap.pricesUpdatedAt = datetime.now()

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(side_effect=[
        [p_expensive],
        [p_expensive, p_cheap],
    ])
    mock_db.cardpricehistory.find_many = AsyncMock(return_value=[])
    mock_db.query_raw = AsyncMock(side_effect=RuntimeError("sql unavailable in unit tests"))

    from src.services.pricing_service import clear_price_cache
    clear_price_cache()

    with patch("src.services.pricing_service.db", mock_db):
        quotes = await PricingService.get_latest_quotes_batch(
            cards=[{"name": "Demonic Tutor", "scryfallId": "dt-expensive", "quantity": 1}],
            provider="cardmarket",
            currency="EUR",
        )

        quote = quotes.get("dt-expensive") or quotes.get("demonic tutor")
        assert quote is not None
        assert quote.scryfallId == "dt-expensive"
        assert quote.unitPrice.trend == 120.0


@pytest.mark.asyncio
async def test_pricing_batch_falls_back_to_cheapest_when_exact_unpriced():
    """If the exact printing has no usable price, fall back to cheapest playable reprint."""
    catalog = MagicMock()
    catalog.normalizedName = "demonic tutor"

    p_zero = MagicMock()
    p_zero.id = "dt-zero"
    p_zero.catalog = catalog
    p_zero.collectorNumber = "1"
    p_zero.set = None
    p_zero.priceEur = 0.0
    p_zero.priceCardmarketTrend = 0.0
    p_zero.priceCardmarketMin = 0.0
    p_zero.priceCardmarketMax = 0.0
    p_zero.pricesUpdatedAt = datetime.now()

    p_cheap = MagicMock()
    p_cheap.id = "dt-cheap"
    p_cheap.catalog = catalog
    p_cheap.collectorNumber = "2"
    p_cheap.set = None
    p_cheap.priceEur = 28.50
    p_cheap.priceCardmarketTrend = 28.50
    p_cheap.priceCardmarketMin = 25.0
    p_cheap.priceCardmarketMax = 35.0
    p_cheap.pricesUpdatedAt = datetime.now()

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(side_effect=[
        [p_zero],
        [p_zero, p_cheap],
    ])
    mock_db.cardpricehistory.find_many = AsyncMock(return_value=[])
    mock_db.query_raw = AsyncMock(side_effect=RuntimeError("sql unavailable in unit tests"))

    from src.services.pricing_service import clear_price_cache
    clear_price_cache()

    with patch("src.services.pricing_service.db", mock_db):
        quotes = await PricingService.get_latest_quotes_batch(
            cards=[{"name": "Demonic Tutor", "scryfallId": "dt-zero", "quantity": 1}],
            provider="cardmarket",
            currency="EUR",
        )

        quote = quotes.get("dt-zero") or quotes.get("demonic tutor")
        assert quote is not None
        assert quote.scryfallId == "dt-cheap"
        assert quote.unitPrice.trend == 28.50
