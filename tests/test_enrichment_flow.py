from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schemas.collection import CollectionCardCreate
from src.schemas.deck import DeckCardCreate, DeckCreate
from src.services.collection_service import CollectionService
from src.services.deck_service import DeckService
from src.services.scryfall_service import ScryfallService


def catalog_record():
    return SimpleNamespace(
        id="local-sol-ring",
        name="Sol Ring",
        normalizedName="sol ring",
        manaCost="{1}",
        typeLine="Artifact",
        imageUri="https://cards.example/sol-ring.jpg",
        setCode="C21",
        collectorNumber="263",
    )


@pytest.mark.asyncio
async def test_worker_resolution_uses_catalog_before_scryfall():
    mock_db = MagicMock()
    mock_db.cardcatalog.find_unique = AsyncMock(return_value=catalog_record())

    with patch("src.services.scryfall_service.db", mock_db), patch.object(
        ScryfallService, "get_card_named", new_callable=AsyncMock
    ) as get_card_named:
        card = await ScryfallService.get_or_resolve_catalog_card("Sol Ring")

    assert card["id"] == "local-sol-ring"
    get_card_named.assert_not_awaited()
    mock_db.cardcatalog.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_worker_resolution_falls_back_to_scryfall_and_caches_missing_card():
    created = catalog_record()
    created.id = "remote-sol-ring"
    mock_db = MagicMock()
    mock_db.cardcatalog.find_unique = AsyncMock(return_value=None)
    mock_db.cardcatalog.upsert = AsyncMock(return_value=created)
    remote_card = {
        "id": "remote-sol-ring",
        "name": "Sol Ring",
        "mana_cost": "{1}",
        "type_line": "Artifact",
        "set": "C21",
        "collector_number": "263",
        "image_uris": {"normal": "https://cards.example/sol-ring.jpg"},
    }

    with patch("src.services.scryfall_service.db", mock_db), patch.object(
        ScryfallService, "get_card_named", new=AsyncMock(return_value=remote_card)
    ) as get_card_named:
        card = await ScryfallService.get_or_resolve_catalog_card("Sol Ring")

    assert card["id"] == "remote-sol-ring"
    get_card_named.assert_awaited_once_with("Sol Ring", exact=False)
    mock_db.cardcatalog.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_adding_collection_card_only_reads_catalog_and_prioritizes_worker():
    now = datetime.now(timezone.utc)
    created = SimpleNamespace(
        id="collection-1", userId="user-1", cardScryfallId="pending:New Card",
        cardName="New Card", quantity=1, setCode=None, collectorNumber=None,
        manaCost=None, typeLine=None, imageUri=None, updatedAt=now,
    )
    mock_db = MagicMock()
    mock_db.collectioncard.find_unique = AsyncMock(return_value=None)
    mock_db.collectioncard.create = AsyncMock(return_value=created)

    with patch("src.services.collection_service.db", mock_db), patch(
        "src.services.collection_service.ScryfallService.get_catalog_card", new=AsyncMock(return_value=None)
    ) as get_catalog, patch(
        "src.services.collection_service.trigger_async_priority_enrichment"
    ) as trigger:
        await CollectionService.add_or_increment_card(
            "user-1", CollectionCardCreate(cardScryfallId="pending:New Card", cardName="New Card")
        )

    get_catalog.assert_awaited_once_with("New Card")
    trigger.assert_called_once_with(["New Card"])


@pytest.mark.asyncio
async def test_adding_deck_card_only_reads_catalog_and_prioritizes_worker():
    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=SimpleNamespace(id="deck-1", userId="user-1"))
    mock_db.deckcard.find_first = AsyncMock(return_value=None)
    mock_db.deckcard.create = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), patch(
        "src.services.deck_service.ScryfallService.get_catalog_card", new=AsyncMock(return_value=None)
    ) as get_catalog, patch(
        "src.services.deck_service.trigger_async_priority_enrichment"
    ) as trigger:
        result = await DeckService.add_card_to_deck(
            "deck-1", "user-1", DeckCardCreate(cardScryfallId="pending:New Card", cardName="New Card")
        )

    assert result is True
    get_catalog.assert_awaited_once_with("New Card")
    trigger.assert_called_once_with(["New Card"])
    assert mock_db.deckcard.create.call_args.kwargs["data"]["cardScryfallId"] == "pending:New Card"


@pytest.mark.asyncio
async def test_creating_deck_with_unknown_commander_queues_worker_without_remote_lookup():
    now = datetime.now(timezone.utc)
    deck = SimpleNamespace(
        id="deck-1", userId="user-1", name="New deck", format="Commander",
        description=None, commander="New Commander", commanderScryfallId=None,
        commanderImageUri=None, createdAt=now, updatedAt=now,
    )
    mock_db = MagicMock()
    mock_db.deck.create = AsyncMock(return_value=deck)
    mock_db.deckcard.create = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), patch(
        "src.services.deck_service.ScryfallService.get_catalog_card", new=AsyncMock(return_value=None)
    ) as get_catalog, patch(
        "src.services.deck_service.trigger_async_priority_enrichment"
    ) as trigger:
        await DeckService.create_deck("user-1", DeckCreate(name="New deck", commander="New Commander"))

    get_catalog.assert_awaited_once_with("New Commander")
    trigger.assert_called_once_with(["New Commander"])
    assert mock_db.deckcard.create.call_args.kwargs["data"]["cardScryfallId"] == "pending:New Commander"
