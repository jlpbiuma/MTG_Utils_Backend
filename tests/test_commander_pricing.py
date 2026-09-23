import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown
from src.services.pricing_service import PricingService
from src.services.deck_service import DeckService
from src.services.deck_view_service import DeckViewService

@pytest.mark.asyncio
async def test_pricing_fallback_for_null_price_printing():
    """Verify that when a card's default scryfallId has null prices, PricingService falls back to physical printing."""
    # Mock printing with None prices (like Alchemy/digital print of Norman Osborn)
    null_print = MagicMock()
    null_print.id = "alchemy-norman-id"
    null_print.priceCardmarketTrend = None
    null_print.priceCardmarketMin = None
    null_print.priceCardmarketMax = None
    null_print.priceEur = None
    null_print.pricesUpdatedAt = datetime.now()
    null_print.catalog = MagicMock()
    null_print.catalog.normalizedName = "norman osborn"

    # Mock catalog physical printing that has a price (e.g. 9.37 €)
    phys_print = MagicMock()
    phys_print.id = "physical-norman-id"
    phys_print.priceCardmarketTrend = 9.37
    phys_print.priceCardmarketMin = 8.50
    phys_print.priceCardmarketMax = 12.00
    phys_print.priceEur = 9.37
    phys_print.pricesUpdatedAt = datetime.now()
    phys_print.catalog = MagicMock()
    phys_print.catalog.normalizedName = "norman osborn"

    async def mock_find_many(where=None, include=None, order=None):
        if where and "id" in where:
            return [null_print]
        if where and "catalog" in where:
            return [null_print, phys_print]
        return []

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(side_effect=mock_find_many)
    mock_db.cardpricehistory.find_many = AsyncMock(return_value=[])

    cards = [{"name": "Norman Osborn", "scryfallId": "alchemy-norman-id"}]

    with patch("src.services.pricing_service.db", mock_db):
        quotes = await PricingService.get_latest_quotes_batch(cards, "cardmarket", "EUR", bypass_cache=True)

        assert "alchemy-norman-id" in quotes or "norman osborn" in quotes
        quote = quotes.get("alchemy-norman-id") or quotes.get("norman osborn")
        assert quote is not None
        assert quote.unitPrice.trend == 9.37

@pytest.mark.asyncio
async def test_commander_included_in_deck_detail_when_not_in_deck_cards():
    """Verify that if deck.commander is set but not in deck.cards, it is synthesized and included in deck totals."""
    deck = MagicMock()
    deck.id = "deck-norman-1"
    deck.userId = "user-1"
    deck.name = "Norman Deck"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Norman Osborn"
    deck.commanderScryfallId = "norman-scry-1"
    deck.commanderImageUri = "https://img.test/norman.jpg"
    deck.tags = None
    deck.cards = []  # Empty cards!

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=deck)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.edhreccommander.find_unique = AsyncMock(return_value=None)

    quote = CardPriceQuote(
        cardName="Norman Osborn",
        provider="cardmarket",
        currency="EUR",
        currencySymbol="€",
        quantity=1,
        unitPrice=UnitPriceBreakdown(trend=9.37, min=8.50, max=12.00),
        subtotal=9.37,
        lastUpdated=datetime.now(),
    )
    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.PricingService.get_latest_quotes_batch", AsyncMock(return_value={"norman-scry-1": quote})), \
         patch("src.services.deck_service.resolve_minio_image_uris", AsyncMock(return_value={})):
        detail = await DeckService.get_deck_detail("deck-norman-1", "user-1")

        assert detail is not None
        assert detail.totalCards == 1
        assert detail.missingCards == 1
        assert detail.totalValue == 9.37
        assert detail.missingValue == 9.37
        assert len(detail.cards) == 1
        assert detail.cards[0].cardName == "Norman Osborn"
        assert detail.cards[0].isCommander is True

@pytest.mark.asyncio
async def test_commander_included_in_deck_view_service():
    """Verify DeckViewService synthesizes missing commander and includes in totals and quotes."""
    deck = MagicMock()
    deck.id = "deck-norman-2"
    deck.userId = "user-1"
    deck.name = "Norman Deck 2"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Norman Osborn"
    deck.commanderScryfallId = "norman-scry-1"
    deck.commanderImageUri = "https://img.test/norman.jpg"
    deck.createdAt = datetime.now()
    deck.updatedAt = datetime.now()
    deck.cards = []

    mock_db = MagicMock()
    mock_db.deck.find_first = AsyncMock(return_value=deck)
    mock_db.query_raw = AsyncMock(return_value=[])

    quote = CardPriceQuote(
        cardName="Norman Osborn",
        provider="cardmarket",
        currency="EUR",
        currencySymbol="€",
        quantity=1,
        unitPrice=UnitPriceBreakdown(trend=9.37, min=8.50, max=12.00),
        subtotal=9.37,
        lastUpdated=datetime.now(),
    )
    with patch("src.services.deck_view_service.db", mock_db), \
         patch("src.services.deck_view_service.PricingService.get_latest_quotes_batch", AsyncMock(return_value={"norman-scry-1": quote})), \
         patch("src.services.deck_view_service.resolve_minio_image_uris", AsyncMock(return_value={})):
        view = await DeckViewService.get_view("deck-norman-2", "user-1", "cardmarket")

        assert view is not None
        assert view.totalCards == 1
        assert view.missingCards == 1
        assert view.priceSummary.totalNetValue == 9.37
        assert view.priceSummary.totalMissingValue == 9.37
        assert len(view.cards) == 1
        assert view.cards[0].cardName == "Norman Osborn"
        assert view.cards[0].isCommander is True
