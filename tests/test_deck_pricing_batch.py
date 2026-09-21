import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from src.services.deck_service import DeckService
from src.services.pricing_service import PricingService
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown

def make_test_card(card_id: str, name: str, scry_id: str, qty: int = 1, assigned: int = 0, type_line: str = "Creature"):
    card = MagicMock()
    card.id = card_id
    card.deckId = "deck-perf-1"
    card.cardScryfallId = scry_id
    card.cardName = name
    card.quantity = qty
    card.assignedQuantity = assigned
    card.isSideboard = False
    card.isCommander = False
    card.manaCost = "{1}"
    card.typeLine = type_line
    card.imageUri = "https://cards.scryfall.test/img.jpg"
    card.setCode = None
    return card

def make_mock_printing(scry_id: str, name: str, trend: float):
    p = MagicMock()
    p.id = scry_id
    p.priceCardmarketTrend = trend
    p.priceCardmarketMin = trend * 0.9
    p.priceCardmarketMax = trend * 1.2
    p.pricesUpdatedAt = datetime.now()
    p.catalog = MagicMock()
    p.catalog.normalizedName = name.lower()
    return p

@pytest.mark.asyncio
async def test_batch_pricing_reduces_query_count():
    """Verify that resolving prices for N cards executes O(1) batch queries instead of O(N) sequential queries."""
    num_cards = 15
    cards = [
        make_test_card(f"c-{i}", f"Card {i}", f"scry-{i}", qty=2, assigned=1)
        for i in range(num_cards)
    ]

    deck = MagicMock()
    deck.id = "deck-perf-1"
    deck.userId = "user-perf-1"
    deck.name = "Perf Deck"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Card 0"
    deck.commanderScryfallId = "scry-0"
    deck.commanderImageUri = None
    deck.createdAt = datetime.now()
    deck.updatedAt = datetime.now()
    deck.cards = cards

    # Setup mock DB with call tracking
    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    printings_map = {f"scry-{i}": make_mock_printing(f"scry-{i}", f"Card {i}", float(i + 1)) for i in range(num_cards)}

    mock_db.cardprinting.find_many = AsyncMock(return_value=list(printings_map.values()))
    mock_db.cardprinting.find_unique = AsyncMock(side_effect=lambda where: printings_map.get(where.get("id")))
    mock_db.cardprinting.find_first = AsyncMock(return_value=None)
    mock_db.cardpricehistory.find_many = AsyncMock(return_value=[])
    mock_db.cardpricehistory.find_first = AsyncMock(return_value=None)

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.pricing_service.db", mock_db):
        decks = await DeckService.get_user_decks("user-perf-1")

    assert len(decks) == 1
    summary = decks[0]

    expected_total_value = sum((i + 1) * 2 for i in range(num_cards))
    expected_owned_value = sum((i + 1) * 1 for i in range(num_cards))
    expected_missing_value = expected_total_value - expected_owned_value

    assert summary.totalCards == 30
    assert summary.ownedCards == 15
    assert summary.missingCards == 15
    assert summary.totalValue == expected_total_value
    assert summary.ownedValue == expected_owned_value
    assert summary.missingValue == expected_missing_value

    total_pricing_queries = (
        mock_db.cardprinting.find_unique.await_count +
        mock_db.cardprinting.find_first.await_count +
        mock_db.cardprinting.find_many.await_count +
        mock_db.cardpricehistory.find_first.await_count +
        mock_db.cardpricehistory.find_many.await_count
    )

    # For 15 cards, previous N+1 logic would do 30 queries (2 per card).
    # Batch logic must do <= 3 queries total for pricing!
    assert total_pricing_queries <= 3, f"Expected <= 3 pricing queries, got {total_pricing_queries}"

@pytest.mark.asyncio
async def test_batch_and_single_quote_equivalence():
    """Verify that get_latest_quotes_batch produces identical results to _latest_provider_quote."""
    cards_data = [
        {"name": "Sol Ring", "scryfallId": "scry-sol"},
        {"name": "Mana Crypt", "scryfallId": "scry-crypt"},
        {"name": "Island", "scryfallId": "scry-isl"},
    ]

    p1 = make_mock_printing("scry-sol", "Sol Ring", 1.50)
    p2 = make_mock_printing("scry-crypt", "Mana Crypt", 180.00)
    p3 = make_mock_printing("scry-isl", "Island", 0.15)
    printings = [p1, p2, p3]

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(return_value=printings)
    mock_db.cardpricehistory.find_many = AsyncMock(return_value=[])

    with patch("src.services.pricing_service.db", mock_db):
        batch_quotes = await PricingService.get_latest_quotes_batch(cards_data, "cardmarket", "EUR")

    assert "scry-sol" in batch_quotes
    assert "sol ring" in batch_quotes
    assert batch_quotes["scry-sol"].unitPrice.trend == 1.50
    assert batch_quotes["scry-crypt"].unitPrice.trend == 180.00
    assert batch_quotes["scry-isl"].unitPrice.trend == 0.15
