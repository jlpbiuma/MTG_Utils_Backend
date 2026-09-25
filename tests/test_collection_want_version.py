"""Tests for collection / wants version (preferred art) updates."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.collection_service import CollectionService
from src.services.want_service import WantService


@pytest.mark.asyncio
async def test_collection_update_card_version_updates_scryfall_and_image():
    existing = MagicMock()
    existing.id = "col-1"
    existing.userId = "user-1"
    existing.cardScryfallId = "old-id"
    existing.cardName = "Sol Ring"
    existing.quantity = 2
    existing.isFoil = False
    existing.setCode = "c21"
    existing.collectorNumber = "1"
    existing.manaCost = "{1}"
    existing.typeLine = "Artifact"
    existing.imageUri = "https://example.com/old.jpg"
    existing.updatedAt = datetime.now()

    updated = MagicMock()
    updated.id = "col-1"
    updated.userId = "user-1"
    updated.cardScryfallId = "new-id"
    updated.cardName = "Sol Ring"
    updated.quantity = 2
    updated.isFoil = False
    updated.setCode = "c21"
    updated.collectorNumber = "202"
    updated.manaCost = "{1}"
    updated.typeLine = "Artifact"
    updated.imageUri = "https://example.com/new.jpg"
    updated.updatedAt = datetime.now()

    mock_db = MagicMock()
    mock_db.collectioncard.find_unique = AsyncMock(return_value=existing)
    mock_db.collectioncard.find_first = AsyncMock(return_value=None)
    mock_db.collectioncard.update = AsyncMock(return_value=updated)

    with patch("src.services.collection_service.db", mock_db), \
         patch("src.services.collection_service.safe_image_uri", side_effect=lambda u: u):
        res = await CollectionService.update_card_version(
            user_id="user-1",
            card_id="col-1",
            card_scryfall_id="new-id",
            image_uri="https://example.com/new.jpg",
            set_code="c21",
            collector_number="202",
        )

    assert res is not None
    assert res.cardScryfallId == "new-id"
    assert res.imageUri == "https://example.com/new.jpg"
    mock_db.collectioncard.update.assert_awaited()


@pytest.mark.asyncio
async def test_want_update_card_version_merges_on_conflict():
    existing = MagicMock()
    existing.id = "want-1"
    existing.userId = "user-1"
    existing.cardScryfallId = "old-id"
    existing.cardName = "Sol Ring"
    existing.quantity = 1
    existing.setCode = "c21"
    existing.collectorNumber = "1"
    existing.manaCost = "{1}"
    existing.typeLine = "Artifact"
    existing.imageUri = "https://example.com/old.jpg"
    existing.updatedAt = datetime.now()

    conflict = MagicMock()
    conflict.id = "want-2"
    conflict.quantity = 3
    conflict.imageUri = "https://example.com/conflict.jpg"
    conflict.setCode = "c21"
    conflict.collectorNumber = "2"

    merged = MagicMock()
    merged.id = "want-2"
    merged.userId = "user-1"
    merged.cardScryfallId = "new-id"
    merged.cardName = "Sol Ring"
    merged.quantity = 4
    merged.setCode = "c21"
    merged.collectorNumber = "202"
    merged.manaCost = "{1}"
    merged.typeLine = "Artifact"
    merged.imageUri = "https://example.com/new.jpg"
    merged.updatedAt = datetime.now()

    mock_db = MagicMock()
    mock_db.wantcard.find_unique = AsyncMock(return_value=existing)
    mock_db.wantcard.find_first = AsyncMock(return_value=conflict)
    mock_db.wantcard.update = AsyncMock(return_value=merged)
    mock_db.wantcard.delete = AsyncMock()

    with patch("src.services.want_service.db", mock_db), \
         patch("src.services.want_service.safe_image_uri", side_effect=lambda u: u):
        res = await WantService.update_card_version(
            user_id="user-1",
            card_id="want-1",
            card_scryfall_id="new-id",
            image_uri="https://example.com/new.jpg",
            set_code="c21",
            collector_number="202",
        )

    assert res is not None
    assert res.id == "want-2"
    assert res.quantity == 4
    mock_db.wantcard.delete.assert_awaited_with(where={"id": "want-1"})
