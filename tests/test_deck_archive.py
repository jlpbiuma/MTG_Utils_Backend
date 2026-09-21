import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.schemas.deck import DeckUpdate, DeckSummaryResponse, DeckDetailResponse
from src.services.deck_service import DeckService
from src.services.priority_service import PriorityService
from src.services.simulated_collection_service import SimulatedCollectionService
from src.services.collection_service import CollectionService
from src.core.db import db


@pytest.mark.asyncio
async def test_deck_summary_and_detail_have_is_archived():
    summary = DeckSummaryResponse(
        id="d1",
        userId="u1",
        name="Test Deck",
        format="Commander",
        createdAt="2026-01-01T00:00:00Z",
        updatedAt="2026-01-01T00:00:00Z",
        totalCards=60,
        uniqueCards=60,
        ownedCards=30,
        missingCards=30,
        completionPercentage=50.0,
    )
    assert summary.isArchived is False

    summary_archived = DeckSummaryResponse(
        id="d1",
        userId="u1",
        name="Test Deck",
        format="Commander",
        isArchived=True,
        createdAt="2026-01-01T00:00:00Z",
        updatedAt="2026-01-01T00:00:00Z",
        totalCards=60,
        uniqueCards=60,
        ownedCards=30,
        missingCards=30,
        completionPercentage=50.0,
    )
    assert summary_archived.isArchived is True


@pytest.mark.asyncio
async def test_update_deck_archived_flag():
    mock_deck_obj = MagicMock()
    mock_deck_obj.id = "deck-1"
    mock_deck_obj.userId = "user-1"
    mock_deck_obj.name = "My Deck"
    mock_deck_obj.format = "Commander"
    mock_deck_obj.description = None
    mock_deck_obj.tags = ""
    mock_deck_obj.commander = None
    mock_deck_obj.commanderScryfallId = None
    mock_deck_obj.commanderImageUri = None
    mock_deck_obj.cards = []
    mock_deck_obj.createdAt = "2026-01-01T00:00:00Z"
    mock_deck_obj.updatedAt = "2026-01-01T00:00:00Z"
    mock_deck_obj.isArchived = True

    mock_deck_actions = MagicMock()
    mock_deck_actions.find_unique = AsyncMock(return_value=mock_deck_obj)
    mock_deck_actions.update = AsyncMock()

    with patch.object(db, "deck", mock_deck_actions), \
         patch.object(DeckService, "get_deck_detail", new_callable=AsyncMock) as mock_detail:

        mock_detail.return_value = MagicMock(isArchived=True)

        res = await DeckService.update_deck("deck-1", "user-1", DeckUpdate(isArchived=True))

        mock_deck_actions.update.assert_awaited_once_with(
            where={"id": "deck-1"},
            data={"isArchived": True},
        )
        assert res.isArchived is True


@pytest.mark.asyncio
async def test_priority_service_excludes_archived_decks():
    mock_deck_actions = MagicMock()
    mock_deck_actions.find_many = AsyncMock(return_value=[])

    mock_col_actions = MagicMock()
    mock_col_actions.find_many = AsyncMock(return_value=[])

    with patch.object(db, "deck", mock_deck_actions), \
         patch.object(db, "collectioncard", mock_col_actions):

        await PriorityService.get_priorities("user-1")

        mock_deck_actions.find_many.assert_awaited_once_with(
            where={"userId": "user-1", "isArchived": False},
            include={"cards": True},
        )


@pytest.mark.asyncio
async def test_simulated_collection_service_excludes_archived_decks():
    mock_deck_actions = MagicMock()
    mock_deck_actions.find_many = AsyncMock(return_value=[])

    mock_col_actions = MagicMock()
    mock_col_actions.find_many = AsyncMock(return_value=[])

    mock_sim_actions = MagicMock()
    mock_sim_actions.find_many = AsyncMock(return_value=[])

    with patch.object(db, "deck", mock_deck_actions), \
         patch.object(db, "collectioncard", mock_col_actions), \
         patch.object(db, "simulatedcard", mock_sim_actions):

        await SimulatedCollectionService._run_analysis("user-1", cards_input=[], name="Test Sim")

        mock_deck_actions.find_many.assert_awaited_once_with(
            where={"userId": "user-1", "isArchived": False},
            include={"cards": True},
        )
