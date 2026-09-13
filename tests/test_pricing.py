from src.services.pricing_service import PricingService

def test_extract_quote_cardmarket():
    scry_data = {
        "id": "card-1",
        "name": "Sol Ring",
        "prices": {"eur": "1.50", "eur_foil": "4.00"},
        "purchase_uris": {"cardmarket": "https://cardmarket.com/sol-ring"},
    }
    quote = PricingService._extract_quote(scry_data, "Sol Ring", "cardmarket", "EUR")
    assert quote.trendPrice == 1.50
    assert quote.minPrice == 1.50
    assert quote.maxPrice == 4.00
    assert quote.currency == "EUR"
    assert quote.productUrl == "https://cardmarket.com/sol-ring"

def test_extract_quote_mtggoldfish():
    scry_data = {
        "id": "card-2",
        "name": "Arcane Signet",
        "prices": {"usd": "1.25", "usd_foil": "3.50"},
    }
    quote = PricingService._extract_quote(scry_data, "Arcane Signet", "mtggoldfish", "USD")
    assert quote.trendPrice == 1.25
    assert quote.currency == "USD"
    assert "Arcane+Signet" in (quote.productUrl or "")

def test_extract_quote_missing_data():
    quote = PricingService._extract_quote(None, "Unknown Card", "cardmarket", "EUR")
    assert quote.trendPrice == 0.0
    assert quote.minPrice == 0.0
    assert quote.maxPrice == 0.0

def test_resolve_card_quantities():
    # 1. Explicit ownedQuantity provided
    owned, missing = PricingService._resolve_card_quantities({"ownedQuantity": 3}, 4)
    assert owned == 3
    assert missing == 1

    # 2. ownedQuantity clamped to qty
    owned, missing = PricingService._resolve_card_quantities({"ownedQuantity": 10}, 4)
    assert owned == 4
    assert missing == 0

    # 3. Explicit missingQuantity provided
    owned, missing = PricingService._resolve_card_quantities({"missingQuantity": 2}, 5)
    assert owned == 3
    assert missing == 2

    # 4. Fallback to isMissing = True
    owned, missing = PricingService._resolve_card_quantities({"isMissing": True}, 4)
    assert owned == 0
    assert missing == 4

    # 5. Fallback to isMissing = False
    owned, missing = PricingService._resolve_card_quantities({"isMissing": False}, 4)
    assert owned == 4
    assert missing == 0

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown, PricingRequest
from src.routers.pricing import get_prices

@pytest.mark.asyncio
async def test_get_price_summary_partial_ownership_and_exact_subtraction():
    cards = [
        {"name": "Lightning Bolt", "scryfallId": "bolt-1", "quantity": 4, "ownedQuantity": 3, "missingQuantity": 1},
        {"name": "Mana Crypt", "scryfallId": "crypt-1", "quantity": 1, "ownedQuantity": 0, "missingQuantity": 1},
        {"name": "Sol Ring", "scryfallId": "ring-1", "quantity": 2, "ownedQuantity": 2, "missingQuantity": 0},
    ]

    quotes_map = {
        "bolt-1": CardPriceQuote(
            scryfallId="bolt-1", cardName="Lightning Bolt", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=2.50, min=2.00, max=3.00),
            quantity=1, subtotal=2.50, lastUpdated=datetime.now()
        ),
        "crypt-1": CardPriceQuote(
            scryfallId="crypt-1", cardName="Mana Crypt", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=180.00, min=170.00, max=200.00),
            quantity=1, subtotal=180.00, lastUpdated=datetime.now()
        ),
        "ring-1": CardPriceQuote(
            scryfallId="ring-1", cardName="Sol Ring", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=1.50, min=1.00, max=2.00),
            quantity=1, subtotal=1.50, lastUpdated=datetime.now()
        ),
    }

    async def mock_latest_quote(card, provider, currency):
        return quotes_map.get(card.get("scryfallId"))

    with patch.object(PricingService, "_latest_provider_quote", side_effect=mock_latest_quote):
        summary = await PricingService.get_price_summary(cards, provider="cardmarket", bypass_cache=True)

    # Bolt: 4 * 2.50 = 10.00 (owned 3 * 2.50 = 7.50, missing 1 * 2.50 = 2.50)
    # Crypt: 1 * 180.00 = 180.00 (owned 0, missing 180.00)
    # Ring: 2 * 1.50 = 3.00 (owned 2 * 1.50 = 3.00, missing 0)
    # Total: 193.00
    # Missing: 2.50 + 180.00 = 182.50
    # Owned: 7.50 + 3.00 = 10.50
    assert summary.totalCards == 7
    assert summary.totalNetValue == 193.00
    assert summary.totalMissingValue == 182.50
    assert summary.totalOwnedValue == 10.50
    # Exact subtraction holds
    assert round(summary.totalNetValue - summary.totalMissingValue, 2) == summary.totalOwnedValue

@pytest.mark.asyncio
async def test_deck_pricing_delivers_owned_value_from_collection():
    mock_deck = MagicMock()
    mock_deck.id = "deck-123"
    mock_deck.userId = "user-123"

    card1 = MagicMock()
    card1.cardName = "Counterspell"
    card1.cardScryfallId = "cs-1"
    card1.quantity = 4
    card1.assignedQuantity = 0  # not assigned directly to deck

    card2 = MagicMock()
    card2.cardName = "Force of Will"
    card2.cardScryfallId = "fow-1"
    card2.quantity = 1
    card2.assignedQuantity = 1

    mock_deck.cards = [card1, card2]

    # User collection has 3 Counterspell
    col_card = MagicMock()
    col_card.cardName = "Counterspell"
    col_card.quantity = 3

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=mock_deck)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[col_card])

    quotes_map = {
        "cs-1": CardPriceQuote(
            scryfallId="cs-1", cardName="Counterspell", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=1.00), quantity=1, subtotal=1.00, lastUpdated=datetime.now()
        ),
        "fow-1": CardPriceQuote(
            scryfallId="fow-1", cardName="Force of Will", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=70.00), quantity=1, subtotal=70.00, lastUpdated=datetime.now()
        ),
    }

    async def mock_latest_quote(card, provider, currency):
        return quotes_map.get(card.get("scryfallId"))

    with patch("src.routers.pricing.db", mock_db), \
         patch.object(PricingService, "_latest_provider_quote", side_effect=mock_latest_quote):
        req = PricingRequest(deckId="deck-123", provider="cardmarket", forceRefresh=True)
        summary = await get_prices(req, user_id="user-123")

    # Counterspell: qty 4, owned 3 (from collection), missing 1 -> total 4.00, owned 3.00, missing 1.00
    # Force of Will: qty 1, owned 1 (from assignedQuantity), missing 0 -> total 70.00, owned 70.00, missing 0.00
    # Total: 74.00, Owned: 73.00, Missing: 1.00
    assert summary.totalCards == 5
    assert summary.totalNetValue == 74.00
    assert summary.totalOwnedValue == 73.00
    assert summary.totalMissingValue == 1.00
    assert round(summary.totalNetValue - summary.totalMissingValue, 2) == summary.totalOwnedValue

@pytest.mark.asyncio
async def test_deck_pricing_excludes_basic_lands_from_missing_value():
    mock_deck = MagicMock()
    mock_deck.id = "deck-123"
    mock_deck.userId = "user-123"

    island = MagicMock()
    island.cardName = "Island"
    island.typeLine = "Basic Land — Island"
    island.cardScryfallId = "isl-1"
    island.quantity = 4
    island.assignedQuantity = 0

    bolter = MagicMock()
    bolter.cardName = "Counterspell"
    bolter.typeLine = "Instant"
    bolter.cardScryfallId = "cs-1"
    bolter.quantity = 4
    bolter.assignedQuantity = 0

    mock_deck.cards = [island, bolter]

    # User owns 1 Counterspell and no Islands
    col_card = MagicMock()
    col_card.cardName = "Counterspell"
    col_card.quantity = 1

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=mock_deck)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[col_card])

    quotes_map = {
        "isl-1": CardPriceQuote(
            scryfallId="isl-1", cardName="Island", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=0.20), quantity=1, subtotal=0.20, lastUpdated=datetime.now()
        ),
        "cs-1": CardPriceQuote(
            scryfallId="cs-1", cardName="Counterspell", provider="cardmarket",
            unitPrice=UnitPriceBreakdown(trend=1.00), quantity=1, subtotal=1.00, lastUpdated=datetime.now()
        ),
    }

    async def mock_latest_quote(card, provider, currency):
        return quotes_map.get(card.get("scryfallId"))

    with patch("src.routers.pricing.db", mock_db), \
         patch.object(PricingService, "_latest_provider_quote", side_effect=mock_latest_quote):
        req = PricingRequest(deckId="deck-123", provider="cardmarket", forceRefresh=True)
        summary = await get_prices(req, user_id="user-123")

    # Basic lands count toward deck value and owned value but NEVER toward missing value.
    # Island: qty 4, owned 4, missing 0 (basic) -> 0.80 / 0.80 / 0.00
    # Counterspell: qty 4, owned 1, missing 3 -> 4.00 / 1.00 / 3.00
    # Total: 4.80, Owned: 1.80, Missing: 3.00
    assert summary.totalCards == 8
    assert summary.totalNetValue == 4.80
    assert summary.totalOwnedValue == 1.80
    assert summary.totalMissingValue == 3.00
    assert round(summary.totalNetValue - summary.totalMissingValue, 2) == summary.totalOwnedValue

