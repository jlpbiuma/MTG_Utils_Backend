from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.auth import get_current_user_id
from src.routers.collection import router
from src.schemas.collection import CollectionCardResponse
from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown


@pytest.mark.asyncio
async def test_collection_view_prices_each_printing_once_and_keeps_foil_quantities():
    now = datetime.now(timezone.utc)
    cards = [CollectionCardResponse(
        id=f"c{i}", userId="u1", cardScryfallId="s1", cardName="Sol Ring",
        quantity=qty, isFoil=foil, updatedAt=now,
    ) for i, (qty, foil) in enumerate([(3, False), (1, True)])]
    quote = CardPriceQuote(cardName="Sol Ring", scryfallId="s1", unitPrice=UnitPriceBreakdown(trend=2), lastUpdated=now)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    with patch("src.services.collection_view_service.CollectionService.get_user_collection", AsyncMock(return_value=cards)) as collection, \
         patch("src.services.collection_view_service.PricingService.get_latest_quotes_batch", AsyncMock(return_value={"s1": quote})) as prices, \
         patch("src.services.pricing_service.PricingService.get_price_summary", AsyncMock(side_effect=AssertionError("No remote prices"))) as remote:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/collection/view")
        assert response.status_code == 200
        result = response.json()
        assert [c["quantity"] for c in result["cards"]] == [3, 1]
        assert [c["isFoil"] for c in result["cards"]] == [False, True]
        assert result["priceSummary"]["totalNetValue"] == 8
        assert result["priceSummary"]["totalCards"] == 4
        assert set(result["priceSummary"]["quotes"]) == {"s1"}
        collection.assert_awaited_once_with("u1")
        prices.assert_awaited_once_with([{"name": "Sol Ring", "scryfallId": "s1"}], "cardmarket", "EUR")
        remote.assert_not_awaited()
