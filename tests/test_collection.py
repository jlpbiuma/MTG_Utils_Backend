import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from src.schemas.collection import CollectionCardCreate
from src.services.collection_service import CollectionService

def test_collection_create_schema():
    c = CollectionCardCreate(cardScryfallId="scry-1", cardName="Sol Ring", quantity=2)
    assert c.cardName == "Sol Ring"
    assert c.quantity == 2

@pytest.mark.asyncio
async def test_get_collection_stats():
    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    with patch("src.services.collection_service.db", mock_db):
        stats = await CollectionService.get_stats("user-123")
        assert stats.totalCards == 0
        assert stats.uniqueCards == 0
        assert stats.completionPercentage == 100.0

@pytest.mark.asyncio
async def test_get_user_collection_prefers_local_minio_images():
    mock_db = MagicMock()
    collection_card = SimpleNamespace(
        id="col-1",
        userId="user-1",
        cardScryfallId="printing-1",
        cardName="Sol Ring",
        quantity=1,
        setCode="C21",
        collectorNumber="263",
        manaCost="{1}",
        typeLine="Artifact",
        imageUri="https://cards.scryfall.io/normal/front/1/2/123.jpg",
        updatedAt="2026-01-01T00:00:00Z",
    )
    printing = SimpleNamespace(id="printing-1", imageUri="http://localhost:8080/images/sol-ring.webp")
    mock_db.collectioncard.find_many = AsyncMock(return_value=[collection_card])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[printing])

    with (
        patch("src.services.collection_service.db", mock_db),
        patch("src.services.image_resolver.db", mock_db),
    ):
        result = await CollectionService.get_user_collection("user-1")

    assert result[0].imageUri == "http://localhost:8080/images/sol-ring.webp"
    mock_db.cardprinting.find_many.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_user_collection_never_falls_back_to_scryfall_uri():
    mock_db = MagicMock()
    collection_card = SimpleNamespace(
        id="col-1",
        userId="user-1",
        cardScryfallId="printing-2",
        cardName="Sol Ring",
        quantity=1,
        setCode=None,
        collectorNumber=None,
        manaCost=None,
        typeLine=None,
        imageUri="https://cards.scryfall.io/normal/front/9/9/999.jpg",
        updatedAt="2026-01-01T00:00:00Z",
    )
    mock_db.collectioncard.find_many = AsyncMock(return_value=[collection_card])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])

    with (
        patch("src.services.collection_service.db", mock_db),
        patch("src.services.image_resolver.db", mock_db),
    ):
        result = await CollectionService.get_user_collection("user-1")

    assert result[0].imageUri is None


@pytest.mark.asyncio
async def test_get_user_collection_pagination_parameters():
    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    with patch("src.services.collection_service.db", mock_db):
        await CollectionService.get_user_collection("user-1", query="Lotus", limit=9, offset=18)

    mock_db.collectioncard.find_many.assert_awaited_once_with(
        where={"userId": "user-1", "cardName": {"contains": "Lotus", "mode": "insensitive"}},
        order=[{"cardName": "asc"}, {"id": "asc"}],
        take=9,
        skip=18,
    )

