from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.auth import get_current_user_id
from src.main import app


@pytest.mark.asyncio
async def test_move_to_collection_endpoint_preserves_quantity_and_user_scope():
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    created = {
        "id": "collection-1",
        "userId": "user-1",
        "cardScryfallId": "printing-1",
        "cardName": "Sol Ring",
        "quantity": 3,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    service = AsyncMock(return_value=created)
    try:
        with patch("src.routers.collection.CollectionService.add_or_increment_card", service):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/collection/add-or-increment",
                    json={"cardScryfallId": "printing-1", "cardName": "Sol Ring", "quantity": 3},
                )
        assert response.status_code == 200
        assert response.json()["quantity"] == 3
        assert service.await_args.args[0] == "user-1"
        assert service.await_args.args[1].cardScryfallId == "printing-1"
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)


@pytest.mark.asyncio
async def test_delete_want_endpoint_returns_not_found_for_a_missing_card():
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    service = AsyncMock(return_value=False)
    try:
        with patch("src.routers.wants.WantService.delete_card", service):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/api/wants/want-missing")
        assert response.status_code == 404
        assert service.await_args.args == ("user-1", "want-missing")
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
