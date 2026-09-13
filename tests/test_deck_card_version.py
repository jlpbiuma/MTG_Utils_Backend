import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.deck_service import DeckService


@pytest.mark.asyncio
async def test_update_card_version_regular_card():
    mock_deck = MagicMock(id="deck-1", userId="user-1", commander="Other Commander", commanderScryfallId="cmd-1")
    mock_card = MagicMock(
        id="card-1",
        deckId="deck-1",
        cardScryfallId="old-scryfall-id",
        cardName="Sol Ring",
        quantity=1,
        assignedQuantity=0,
        isSideboard=False,
        isCommander=False,
        deck=mock_deck,
        imageUri="http://localhost:8080/old.webp",
    )

    mock_db = MagicMock()
    mock_db.deckcard.find_unique = AsyncMock(return_value=mock_card)
    mock_db.deckcard.find_first = AsyncMock(return_value=None)
    mock_db.deckcard.update = AsyncMock()
    mock_db.deck.update = AsyncMock()

    with patch("src.services.deck_service.db", mock_db):
        ok = await DeckService.update_card_version(
            card_id="card-1",
            user_id="user-1",
            card_scryfall_id="new-scryfall-id",
            image_uri="http://localhost:8080/new.webp",
            set_code="tmc",
        )

    assert ok is True
    mock_db.deckcard.update.assert_awaited_once_with(
        where={"id": "card-1"},
        data={
            "cardScryfallId": "new-scryfall-id",
            "imageUri": "http://localhost:8080/new.webp",
            "setCode": "tmc",
        },
    )
    # Not commander, so deck commander is not touched
    mock_db.deck.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_card_version_commander():
    mock_deck = MagicMock(
        id="deck-1",
        userId="user-1",
        commander="Cloud, Ex-SOLDIER",
        commanderScryfallId="afin-50-id",
        commanderImageUri="http://localhost:8080/afin50.webp",
    )
    mock_card = MagicMock(
        id="card-cmd-1",
        deckId="deck-1",
        cardScryfallId="afin-50-id",
        cardName="Cloud, Ex-SOLDIER",
        quantity=1,
        assignedQuantity=1,
        isSideboard=False,
        isCommander=True,
        deck=mock_deck,
        imageUri="http://localhost:8080/afin50.webp",
    )

    mock_db = MagicMock()
    mock_db.deckcard.find_unique = AsyncMock(return_value=mock_card)
    mock_db.deckcard.find_first = AsyncMock(return_value=None)
    mock_db.deckcard.update = AsyncMock()
    mock_db.deck.update = AsyncMock()

    with patch("src.services.deck_service.db", mock_db):
        ok = await DeckService.update_card_version(
            card_id="card-cmd-1",
            user_id="user-1",
            card_scryfall_id="fic-2-id",
            image_uri="http://localhost:8080/fic2.webp",
            set_code="fic",
        )

    assert ok is True
    mock_db.deckcard.update.assert_awaited_once_with(
        where={"id": "card-cmd-1"},
        data={
            "cardScryfallId": "fic-2-id",
            "imageUri": "http://localhost:8080/fic2.webp",
            "setCode": "fic",
        },
    )
    mock_db.deck.update.assert_awaited_once_with(
        where={"id": "deck-1"},
        data={
            "commanderScryfallId": "fic-2-id",
            "commanderImageUri": "http://localhost:8080/fic2.webp",
        },
    )


@pytest.mark.asyncio
async def test_update_card_version_conflict_merges():
    mock_deck = MagicMock(id="deck-1", userId="user-1", commander=None)
    mock_card = MagicMock(
        id="card-1",
        deckId="deck-1",
        cardScryfallId="old-id",
        cardName="Swamp",
        quantity=3,
        assignedQuantity=1,
        isSideboard=False,
        isCommander=False,
        deck=mock_deck,
    )
    existing_conflict = MagicMock(
        id="card-conflict",
        deckId="deck-1",
        cardScryfallId="target-id",
        quantity=2,
        assignedQuantity=1,
        imageUri="http://localhost:8080/existing.webp",
        setCode="m10",
    )

    mock_db = MagicMock()
    mock_db.deckcard.find_unique = AsyncMock(return_value=mock_card)
    mock_db.deckcard.find_first = AsyncMock(return_value=existing_conflict)
    mock_db.deckcard.update = AsyncMock()
    mock_db.deckcard.delete = AsyncMock()

    with patch("src.services.deck_service.db", mock_db):
        ok = await DeckService.update_card_version(
            card_id="card-1",
            user_id="user-1",
            card_scryfall_id="target-id",
            image_uri="http://localhost:8080/new.webp",
            set_code="tmc",
        )

    assert ok is True
    # Merges into conflict card and deletes card-1
    mock_db.deckcard.update.assert_awaited_once_with(
        where={"id": "card-conflict"},
        data={
            "quantity": 5,
            "assignedQuantity": 2,
            "imageUri": "http://localhost:8080/new.webp",
            "setCode": "tmc",
        },
    )
    mock_db.deckcard.delete.assert_awaited_once_with(where={"id": "card-1"})


@pytest.mark.asyncio
async def test_update_card_version_unauthorized():
    mock_deck = MagicMock(id="deck-1", userId="other-user")
    mock_card = MagicMock(id="card-1", deck=mock_deck)

    mock_db = MagicMock()
    mock_db.deckcard.find_unique = AsyncMock(return_value=mock_card)

    with patch("src.services.deck_service.db", mock_db):
        ok = await DeckService.update_card_version(
            card_id="card-1",
            user_id="user-1",
            card_scryfall_id="any-id",
        )

    assert ok is False
