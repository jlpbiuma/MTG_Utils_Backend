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

@pytest.mark.asyncio
async def test_update_deck_name_and_commander():
    mock_deck = MagicMock()
    mock_deck.id = "deck-1"
    mock_deck.userId = "user-1"
    mock_deck.name = "Original Name"
    mock_deck.format = "Commander"
    mock_deck.commander = "Old Commander"
    mock_deck.cards = []

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=mock_deck)
    mock_db.deck.update = AsyncMock()
    mock_db.deckcard.update_many = AsyncMock()
    mock_db.deckcard.find_first = AsyncMock(return_value=None)
    mock_db.deckcard.create = AsyncMock()

    mock_detail = MagicMock()
    mock_detail.name = "New Name"
    mock_detail.commander = "New Commander"

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.DeckService.get_deck_detail", new_callable=AsyncMock) as mock_get_detail, \
         patch("src.services.deck_service.ScryfallService.get_or_resolve_catalog_card", new_callable=AsyncMock) as mock_resolve:
        mock_get_detail.return_value = mock_detail
        mock_resolve.return_value = {
            "id": "scry-new-cmd",
            "imageUri": "https://cards.scryfall.test/new-cmd.jpg",
            "manaCost": "{1}{U}{R}",
            "typeLine": "Legendary Creature — Dragon",
        }

        update_payload = DeckUpdate(
            name="New Name",
            commander="New Commander",
            description="Updated notes"
        )
        res = await DeckService.update_deck("deck-1", "user-1", update_payload)

        assert res is not None
        assert res.name == "New Name"
        assert res.commander == "New Commander"

        # Verify old commanders were unmarked
        mock_db.deckcard.update_many.assert_awaited_once_with(
            where={"deckId": "deck-1"},
            data={"isCommander": False}
        )

        # Verify new commander card was created with isCommander=True
        mock_db.deckcard.create.assert_awaited_once()
        create_kwargs = mock_db.deckcard.create.call_args[1]["data"]
        assert create_kwargs["cardName"] == "New Commander"
        assert create_kwargs["isCommander"] is True
        assert create_kwargs["cardScryfallId"] == "scry-new-cmd"

        # Verify deck record was updated
        mock_db.deck.update.assert_awaited_once()
        update_args = mock_db.deck.update.call_args[1]["data"]
        assert update_args["name"] == "New Name"
        assert update_args["commander"] == "New Commander"
        assert update_args["description"] == "Updated notes"
