import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.schemas.deck import DeckCreate, DeckUpdate
from src.services.deck_service import DeckService

def test_deck_schemas():
    create = DeckCreate(name="Niv Mizzet", format="Commander", commander="Niv-Mizzet, Parun")
    assert create.name == "Niv Mizzet"
    assert create.format == "Commander"
    assert create.commander == "Niv-Mizzet, Parun"

    update = DeckUpdate(name="Niv Updated")
    assert update.name == "Niv Updated"
    assert update.commander is None

@pytest.mark.asyncio
async def test_get_user_decks_empty():
    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    with patch("src.services.deck_service.db", mock_db):
        decks = await DeckService.get_user_decks("user-123")
        assert len(decks) == 0
