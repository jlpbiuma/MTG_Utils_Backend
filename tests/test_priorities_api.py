import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.services.priority_service import PriorityService
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown
from src.schemas.priorities import GoldenWantsResponse


def create_mock_deck_card(id: str, name: str, scryfall_id: str, qty: int = 1, assigned: int = 0, type_line: str = "Creature"):
    c = MagicMock()
    c.id = id
    c.cardName = name
    c.cardScryfallId = scryfall_id
    c.quantity = qty
    c.assignedQuantity = assigned
    c.imageUri = f"https://images.example.com/{scryfall_id}.jpg"
    c.manaCost = "{2}"
    c.typeLine = type_line
    c.isSideboard = False
    c.isCommander = False
    return c


@pytest.mark.asyncio
async def test_priorities_deck_stats_and_completion_accuracy():
    """Verify PriorityService sets deckTotalCards and deckMissingCards accurately,
    and does not report missing cards for a deck if the user already owns them in collection."""
    # Deck 1: Commander deck with 10 cards for testing (2 basics, 8 non-basics)
    b1 = create_mock_deck_card("b1", "Island", "isl-1", qty=2, type_line="Basic Land — Island")
    c1 = create_mock_deck_card("c1", "Brainstorm", "bs-1", qty=1, assigned=0, type_line="Instant")
    c2 = create_mock_deck_card("c2", "Ponder", "pd-1", qty=1, assigned=0, type_line="Sorcery")
    c3 = create_mock_deck_card("c3", "Counterspell", "cs-1", qty=1, assigned=0, type_line="Instant")

    deck1 = MagicMock()
    deck1.id = "deck-1"
    deck1.userId = "user-1"
    deck1.name = "Blue Deck"
    deck1.commander = None
    deck1.cards = [b1, c1, c2, c3]  # total = 5 cards

    # User collection owns Island (basics are always owned) + Ponder (qty=1).
    # Brainstorm and Counterspell are not owned.
    col_ponder = MagicMock()
    col_ponder.cardName = "Ponder"
    col_ponder.quantity = 1

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck1])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[col_ponder])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.query_raw = AsyncMock(return_value=[])

    with patch("src.services.priority_service.db", mock_db), \
         patch("src.services.pricing_service.db", mock_db), \
         patch("src.services.priority_service.PricingService.get_price_summary", new_callable=AsyncMock, return_value=MagicMock(quotes={})):

        res = await PriorityService.get_priorities(user_id="user-1")
        assert res.globalDeckCount == 1
        assert res.globalCompletionBefore == 60.0

        # Total cards in deck1 = 2 (Island) + 1 (Brainstorm) + 1 (Ponder) + 1 (Counterspell) = 5
        # Owned = 2 (Island) + 1 (Ponder) = 3
        # Missing = 5 - 3 = 2
        # Deck completion = 3 / 5 * 100 = 60.0%
        for item in res.items:
            for d in item.decks:
                if d.deckId == "deck-1":
                    assert d.deckTotalCards == 5
                    assert d.deckMissingCards == 2
                    assert d.completionPercentage == 60.0

        # Ponder is owned in collection, so its missingQuantity in deck-1 should be 0
        ponder_item = next((item for item in res.items if item.cardName == "Ponder"), None)
        if ponder_item:
            d_info = next(d for d in ponder_item.decks if d.deckId == "deck-1")
            assert d_info.missingQuantity == 0


@pytest.mark.asyncio
async def test_priorities_complete_decks_sorting():
    """Verify sort='complete_decks' orders cards for decks closest to 100% first, then cheaper first."""
    # Deck Near: 95% complete, needs Card Near (cheap)
    c_near = create_mock_deck_card("cn", "Cheap Finisher", "cf-1", qty=1)
    deck_near = MagicMock()
    deck_near.id = "d-near"
    deck_near.userId = "user-1"
    deck_near.name = "Almost Done"
    deck_near.commander = None
    deck_near.cards = [c_near]

    # Deck Far: 20% complete, needs Card Far
    c_far = create_mock_deck_card("cf", "Far Card", "fc-1", qty=1)
    deck_far = MagicMock()
    deck_far.id = "d-far"
    deck_far.userId = "user-1"
    deck_far.name = "Barely Started"
    deck_far.commander = None
    deck_far.cards = [c_far]

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck_near, deck_far])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.query_raw = AsyncMock(return_value=[])

    # Mock completions: deck_near = 95.0, deck_far = 20.0
    with patch("src.services.priority_service.db", mock_db), \
         patch("src.services.pricing_service.db", mock_db), \
         patch("src.services.priority_service.completion_percentages_by_deck", return_value={"d-near": 95.0, "d-far": 20.0}), \
         patch("src.services.priority_service.PricingService.get_price_summary", new_callable=AsyncMock, return_value=MagicMock(quotes={})):

        res = await PriorityService.get_priorities(user_id="user-1", sort="complete_decks")

        assert len(res.items) == 2
        # Card for 95% deck must appear before card for 20% deck
        assert res.items[0].cardName == "Cheap Finisher"
        assert res.items[1].cardName == "Far Card"


@pytest.mark.asyncio
async def test_golden_wants_complete_decks_accuracy():
    """Verify Golden Wants optimizer 'complete_decks' strategy:
    1. A deck missing 2 cards costing 3€ with budget 50€ completes to 100% and is added to completedDecks.
    2. A deck missing 80 cards with only 3 cards in candidate units DOES NOT reach 100% and is NOT in completedDecks.
    3. Gained percentage is strictly bounded and calculated honestly."""
    now = datetime.now()

    # Deck 1: Near completion (total = 100, owned = 98, missing = 2)
    b_deck1 = [create_mock_deck_card(f"b1_{i}", "Plains", "pl-1", qty=1, type_line="Basic Land — Plains") for i in range(35)]
    owned_deck1 = [create_mock_deck_card(f"o1_{i}", f"OwnedCard_{i}", f"oc-{i}", qty=1) for i in range(63)]
    miss_deck1_a = create_mock_deck_card("m1_a", "Finisher Alpha", "fa-1", qty=1)
    miss_deck1_b = create_mock_deck_card("m1_b", "Finisher Beta", "fb-1", qty=1)

    deck1 = MagicMock()
    deck1.id = "deck-finishable"
    deck1.userId = "user-1"
    deck1.name = "Finishable Deck"
    deck1.commander = None
    deck1.cards = b_deck1 + owned_deck1 + [miss_deck1_a, miss_deck1_b]  # 100 cards

    # Deck 2: Far from completion (total = 100, owned = 20, missing = 80)
    b_deck2 = [create_mock_deck_card(f"b2_{i}", "Swamp", "sw-1", qty=1, type_line="Basic Land — Swamp") for i in range(20)]
    miss_deck2_x = create_mock_deck_card("m2_x", "Eldrazi Titan", "et-1", qty=1)
    miss_deck2_y = create_mock_deck_card("m2_y", "Eldrazi Monument", "em-1", qty=1)
    # The remaining 78 cards are also missing
    other_miss = [create_mock_deck_card(f"m2_other_{i}", f"OtherMiss_{i}", f"om-{i}", qty=1) for i in range(78)]

    deck2 = MagicMock()
    deck2.id = "deck-unfinishable"
    deck2.userId = "user-1"
    deck2.name = "Eldrazis Cascade"
    deck2.commander = None
    deck2.cards = b_deck2 + [miss_deck2_x, miss_deck2_y] + other_miss  # 100 cards

    # User collection has the owned cards
    col_cards = [MagicMock(cardName=c.cardName, quantity=1) for c in owned_deck1]

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck1, deck2])
    mock_db.collectioncard.find_many = AsyncMock(return_value=col_cards)
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.query_raw = AsyncMock(return_value=[])

    def fake_quote(scryfall_id, name, price):
        return CardPriceQuote(
            scryfallId=scryfall_id,
            cardName=name,
            provider="cardmarket",
            currency="EUR",
            currencySymbol="€",
            unitPrice=UnitPriceBreakdown(trend=price, min=price, max=price),
            quantity=1,
            subtotal=price,
            lastUpdated=now,
        )

    quotes = {
        "fa-1": fake_quote("fa-1", "Finisher Alpha", 1.00),
        "fb-1": fake_quote("fb-1", "Finisher Beta", 2.00),
        "et-1": fake_quote("et-1", "Eldrazi Titan", 5.00),
        "em-1": fake_quote("em-1", "Eldrazi Monument", 4.00),
    }
    fake_summary = MagicMock(quotes=quotes)

    with patch("src.services.priority_service.db", mock_db), \
         patch("src.services.pricing_service.db", mock_db), \
         patch("src.services.priority_service.PricingService.get_price_summary", new_callable=AsyncMock, return_value=fake_summary):

        # Budget = 3.50 € (Enough for Finisher Alpha (1€) + Finisher Beta (2€) = 3€, leaving 0.50€ for at most 2 cards of Deck 2)
        result: GoldenWantsResponse = await PriorityService.get_golden_wants(
            user_id="user-1",
            budget=3.50,
            strategy="complete_decks",
        )

        assert isinstance(result, GoldenWantsResponse)
        # Deck 1 should be the ONLY completed deck!
        completed_deck_ids = [d.deckId for d in result.completedDecks]
        assert "deck-finishable" in completed_deck_ids
        assert "deck-unfinishable" not in completed_deck_ids
        assert len(result.completedDecks) == 1

        # Check projected progress for Finishable Deck
        p1 = next(p for p in result.projectedProgress if p.deckId == "deck-finishable")
        assert p1.before == 98.0
        assert p1.after == 100.0
        assert p1.cardsFulfilled == 2
        assert p1.totalMissingInitially == 2
        assert p1.gainedPercentage == 2.0

        # Check projected progress for Eldrazis Cascade (unfinishable)
        p2 = next(p for p in result.projectedProgress if p.deckId == "deck-unfinishable")
        assert p2.before == 20.0
        assert p2.after < 100.0  # Must NEVER be 100.0%
        assert p2.totalMissingInitially == 80  # True missing cards count (not 2 or 4)
        assert p2.cardsFulfilled <= 2  # At most what fit in leftover budget
        assert p2.after == round(20.0 + p2.cardsFulfilled * 1.0, 1)


@pytest.mark.asyncio
async def test_router_golden_wants_route():
    """Verify the /api/priorities/golden-wants endpoint works via router."""
    from src.routers.priorities import get_golden_wants

    fake_response = GoldenWantsResponse(
        cart=[],
        totalCost=0.0,
        budgetRemaining=50.0,
        completedDecks=[],
        projectedProgress=[],
        totalCardsToBuy=0,
    )

    with patch.object(PriorityService, "get_golden_wants", new_callable=AsyncMock, return_value=fake_response) as mock_solver:
        res = await get_golden_wants(budget=30.0, strategy="complete_decks", provider="cardmarket", user_id="user-123")
        assert res == fake_response
        mock_solver.assert_awaited_once_with(user_id="user-123", budget=30.0, strategy="complete_decks", provider="cardmarket")
