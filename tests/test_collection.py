import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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
