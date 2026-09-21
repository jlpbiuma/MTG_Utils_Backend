import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.simulated_collection_service import SimulatedCollectionService

@pytest.mark.asyncio
async def test_simulated_collection_analysis_metrics():
    """Verify that simulated collection calculation accurately computes:
    - Total economic value using cheapest positive price
    - Useful copies vs surplus copies
    - Global net completion gain %
    - Candidate decks per card
    - Zero modification to real collection."""

    # Mock user decks
    deck_card_1 = MagicMock()
    deck_card_1.id = "card-1"
    deck_card_1.cardName = "Sol Ring"
    deck_card_1.cardScryfallId = "scry-sol"
    deck_card_1.quantity = 1
    deck_card_1.assignedQuantity = 0
    deck_card_1.typeLine = "Artifact"
    deck_card_1.manaCost = "{1}"
    deck_card_1.imageUri = "https://images.example.com/sol.jpg"

    deck_card_2 = MagicMock()
    deck_card_2.id = "card-2"
    deck_card_2.cardName = "Arcane Signet"
    deck_card_2.cardScryfallId = "scry-signet"
    deck_card_2.quantity = 1
    deck_card_2.assignedQuantity = 0
    deck_card_2.typeLine = "Artifact"
    deck_card_2.manaCost = "{2}"
    deck_card_2.imageUri = "https://images.example.com/signet.jpg"

    deck_1 = MagicMock()
    deck_1.id = "deck-1"
    deck_1.name = "Deck Alpha"
    deck_1.userId = "user-123"
    deck_1.cards = [deck_card_1, deck_card_2]

    deck_2_card = MagicMock()
    deck_2_card.id = "card-3"
    deck_2_card.cardName = "Sol Ring"
    deck_2_card.cardScryfallId = "scry-sol"
    deck_2_card.quantity = 1
    deck_2_card.assignedQuantity = 0
    deck_2_card.typeLine = "Artifact"
    deck_2_card.manaCost = "{1}"
    deck_2_card.imageUri = "https://images.example.com/sol.jpg"

    deck_2 = MagicMock()
    deck_2.id = "deck-2"
    deck_2.name = "Deck Beta"
    deck_2.userId = "user-123"
    deck_2.cards = [deck_2_card]

    # Real collection: user has 1 Sol Ring already in real collection
    real_col_card = MagicMock()
    real_col_card.cardName = "Sol Ring"
    real_col_card.quantity = 1

    # Catalog printings
    cat_sol = MagicMock()
    cat_sol.normalizedName = "sol ring"
    p_sol_zero = MagicMock()
    p_sol_zero.id = "sol-zero"
    p_sol_zero.catalog = cat_sol
    p_sol_zero.priceEur = 0.0
    p_sol_zero.priceCardmarketTrend = 0.0
    p_sol_zero.priceUsd = 0.0
    p_sol_zero.imageUri = "https://images.example.com/sol-zero.jpg"
    p_sol_zero.imageUriLarge = None
    p_sol_zero.imageUriSmall = None

    p_sol_cheapest = MagicMock()
    p_sol_cheapest.id = "sol-cheapest"
    p_sol_cheapest.catalog = cat_sol
    p_sol_cheapest.priceEur = 1.20
    p_sol_cheapest.priceCardmarketTrend = 1.20
    p_sol_cheapest.priceUsd = 1.50
    p_sol_cheapest.imageUri = "https://images.example.com/sol-cheap.jpg"
    p_sol_cheapest.imageUriLarge = None
    p_sol_cheapest.imageUriSmall = None

    cat_signet = MagicMock()
    cat_signet.normalizedName = "arcane signet"
    p_signet = MagicMock()
    p_signet.id = "signet-cheapest"
    p_signet.catalog = cat_signet
    p_signet.priceEur = 0.85
    p_signet.priceCardmarketTrend = 0.85
    p_signet.priceUsd = 1.00
    p_signet.imageUri = "https://images.example.com/signet-cheap.jpg"
    p_signet.imageUriLarge = None
    p_signet.imageUriSmall = None

    cat_forest = MagicMock()
    cat_forest.normalizedName = "forest"
    p_forest = MagicMock()
    p_forest.id = "forest-print"
    p_forest.catalog = cat_forest
    p_forest.priceEur = 0.10
    p_forest.priceCardmarketTrend = 0.10
    p_forest.priceUsd = 0.15
    p_forest.imageUri = None
    p_forest.imageUriLarge = None
    p_forest.imageUriSmall = None

    # Input simulated collection: 2 Sol Ring, 1 Arcane Signet, 1 Forest
    # Sol Ring: Owned real = 1 -> ALREADY OWNED -> 0 useful copies, 2 surplus/sellable copies, 0 candidate decks
    # Forest: Basic land -> 0 useful copies, 1 surplus/sellable copy, 0 candidate decks
    # Arcane Signet: Owned real = 0, needed = 1 -> 1 useful copy, 0 surplus, 1 candidate deck
    # Total non-basics = 3
    # Total useful copies = 1 (Arcane Signet)
    # Global net gain = (1 / 3) * 100 = 33.33%
    # Economic value = (2 * 1.20) + (1 * 0.85) + (1 * 0.10) = 2.40 + 0.85 + 0.10 = 3.35 €
    raw_text = """
    2 Sol Ring
    1 Arcane Signet
    1 Forest
    """

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck_1, deck_2])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[real_col_card])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[p_sol_zero, p_sol_cheapest, p_signet, p_forest])
    mock_db.collectioncard.create = AsyncMock()

    with patch("src.services.simulated_collection_service.db", mock_db):
        result = await SimulatedCollectionService.analyze_raw_text(
            user_id="user-123",
            raw_text=raw_text,
            name="Lote Test",
            provider="cardmarket",
        )

        # Asserts
        assert result.totalCards == 4
        assert result.uniqueCards == 3
        assert result.totalEconomicValue == 3.35
        assert result.economicValueExcludingOwned == 0.95
        assert result.sellableValue == 2.50 # 2 Sol Ring (2.40) + 1 Forest (0.10)
        assert result.sellableCardsCount == 3 # 2 Sol Ring + 1 Forest
        assert result.usefulCardsCount == 1 # Only Arcane Signet
        assert result.alreadyOwnedCardsCount == 2 # 2 Sol Ring
        assert result.globalNetGain == 33.33
        assert result.benefitedDecksCount == 1 # Only Deck Alpha benefited by Arcane Signet

        # Verify Sol Ring (already owned in real collection -> does NOT contribute to decks)
        sol_item = next(c for c in result.cards if c.cardName == "Sol Ring")
        assert sol_item.quantity == 2
        assert sol_item.unitPrice == 1.20
        assert sol_item.totalPrice == 2.40
        assert sol_item.copiesOwnedReal == 1
        assert sol_item.usefulCopies == 0
        assert sol_item.surplusCopies == 2
        assert sol_item.sellableCopies == 2
        assert sol_item.sellableValue == 2.40
        assert sol_item.netCompletionGain == 0.0
        assert sol_item.candidateDeckCount == 0
        assert sol_item.candidateDecks == []

        # Verify Forest (basic land -> does NOT contribute to decks)
        forest_item = next(c for c in result.cards if c.cardName == "Forest")
        assert forest_item.quantity == 1
        assert forest_item.usefulCopies == 0
        assert forest_item.surplusCopies == 1
        assert forest_item.sellableCopies == 1
        assert forest_item.netCompletionGain == 0.0
        assert forest_item.candidateDeckCount == 0
        assert forest_item.candidateDecks == []

        # Verify Arcane Signet (new non-basic card -> contributes to deck)
        signet_item = next(c for c in result.cards if c.cardName == "Arcane Signet")
        assert signet_item.quantity == 1
        assert signet_item.unitPrice == 0.85
        assert signet_item.totalPrice == 0.85
        assert signet_item.copiesOwnedReal == 0
        assert signet_item.copiesNeededTotal == 1
        assert signet_item.usefulCopies == 1
        assert signet_item.surplusCopies == 0
        assert signet_item.netCompletionGain == 33.33
        assert signet_item.candidateDeckCount == 1
        assert len(signet_item.candidateDecks) == 1
        assert signet_item.candidateDecks[0].deckId == "deck-1"

        # CRITICAL: Verify real collection is NEVER touched
        assert mock_db.collectioncard.create.call_count == 0


@pytest.mark.asyncio
async def test_simulated_collection_persistence_and_deletion():
    """Verify that saving creates SimulatedCollection & SimulatedCard models
    and deletion removes them, without touching user_collections."""

    deck = MagicMock()
    deck.id = "d1"
    deck.name = "Deck 1"
    deck.userId = "user-123"
    deck.cards = []

    created_coll = MagicMock()
    created_coll.id = "sim-coll-999"
    created_coll.name = "Mi Lote Guardado"
    created_coll.description = "Lote de prueba"
    created_coll.userId = "user-123"

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.simulatedcollection.create = AsyncMock(return_value=created_coll)
    mock_db.simulatedcard.create = AsyncMock()
    mock_db.simulatedcollection.find_unique = AsyncMock(return_value=created_coll)
    mock_db.simulatedcollection.delete = AsyncMock()
    mock_db.collectioncard.create = AsyncMock()

    with patch("src.services.simulated_collection_service.db", mock_db):
        res = await SimulatedCollectionService.create_simulated_collection(
            user_id="user-123",
            name="Mi Lote Guardado",
            description="Lote de prueba",
            raw_text="4 Lightning Bolt",
            provider="cardmarket",
        )

        assert res.id == "sim-coll-999"
        assert mock_db.simulatedcollection.create.call_count == 1
        assert mock_db.simulatedcard.create.call_count == 1
        assert mock_db.collectioncard.create.call_count == 0

        # Test deletion
        del_res = await SimulatedCollectionService.delete_simulated_collection("user-123", "sim-coll-999")
        assert del_res is True
        assert mock_db.simulatedcollection.delete.call_count == 1


@pytest.mark.asyncio
async def test_cardprinting_without_manacost_attribute_does_not_raise_attribute_error():
    """Verify that when CardPrinting does not have manaCost/typeLine (as in real Prisma schema),
    SimulatedCollectionService cleanly reads from catalog/set and does not crash with AttributeError."""

    class StrictCardPrinting:
        """Simulates Prisma CardPrinting model which has no manaCost or typeLine attribute."""
        def __init__(self, printing_id, price, catalog, collector_number="001"):
            self.id = printing_id
            self.priceEur = price
            self.priceCardmarketTrend = price
            self.priceUsd = price
            self.imageUri = "https://images.example.com/print.jpg"
            self.imageUriLarge = None
            self.imageUriSmall = None
            self.collectorNumber = collector_number
            self.catalog = catalog
            self.set = None

    class StrictCardCatalog:
        def __init__(self, name, mana_cost, type_line):
            self.name = name
            self.normalizedName = name.lower()
            self.manaCost = mana_cost
            self.typeLine = type_line
            self.setCode = "dmu"
            self.collectorNumber = "001"
            self.imageUri = "https://images.example.com/catalog.jpg"

    catalog = StrictCardCatalog("Lightning Bolt", "{R}", "Instant")
    printing = StrictCardPrinting("bolt-id", 0.75, catalog)

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[printing])

    with patch("src.services.simulated_collection_service.db", mock_db):
        res = await SimulatedCollectionService.analyze_raw_text(
            user_id="user-123",
            raw_text="1 Lightning Bolt",
            name="Test Bolt",
            provider="cardmarket",
        )

        assert res.totalCards == 1
        assert len(res.cards) == 1
        card = res.cards[0]
        assert card.cardName == "Lightning Bolt"
        assert card.manaCost == "{R}"
        assert card.typeLine == "Instant"
        assert card.unitPrice == 0.75
        assert card.cardScryfallId == "bolt-id"


@pytest.mark.asyncio
async def test_simulated_collection_spanish_basics_and_owned_exclusion():
    """Verify that Spanish basic lands ('Bosques', 'Bosque') and already owned cards
    ('Sol Ring') never contribute to deck completion, useful copies, or candidate decks."""
    deck_card = MagicMock()
    deck_card.id = "c-deck"
    deck_card.cardName = "Bosque"
    deck_card.quantity = 5
    deck_card.assignedQuantity = 0
    deck_card.typeLine = "Tierra básica — Bosque"

    deck_sol = MagicMock()
    deck_sol.id = "c-sol"
    deck_sol.cardName = "Sol Ring"
    deck_sol.quantity = 1
    deck_sol.assignedQuantity = 0
    deck_sol.typeLine = "Artifact"

    deck = MagicMock()
    deck.id = "deck-green"
    deck.name = "Mono Green"
    deck.cards = [deck_card, deck_sol]

    # Real collection has Sol Ring
    owned_sol = MagicMock()
    owned_sol.cardName = "Sol Ring"
    owned_sol.quantity = 1

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[owned_sol])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])

    with patch("src.services.simulated_collection_service.db", mock_db):
        res = await SimulatedCollectionService.analyze_raw_text(
            user_id="user-123",
            raw_text="""
            10 Bosques
            1 Sol Ring
            """,
            name="Spanish Test",
            provider="cardmarket",
        )

        assert res.totalCards == 11
        assert res.usefulCardsCount == 0
        assert res.globalNetGain == 0.0
        assert res.benefitedDecksCount == 0

        # Bosques check
        bosques = next(c for c in res.cards if "bosque" in c.cardName.lower())
        assert bosques.usefulCopies == 0
        assert bosques.candidateDeckCount == 0
        assert bosques.candidateDecks == []
        assert bosques.netCompletionGain == 0.0

        # Sol Ring check
        sol = next(c for c in res.cards if c.cardName == "Sol Ring")
        assert sol.copiesOwnedReal == 1
        assert sol.usefulCopies == 0
        assert sol.candidateDeckCount == 0
        assert sol.candidateDecks == []
        assert sol.netCompletionGain == 0.0


