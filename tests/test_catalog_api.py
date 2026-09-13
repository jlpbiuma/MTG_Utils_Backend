from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_catalog_cards_expose_necessary_printing_fields():
    printing = SimpleNamespace(
        id="printing-1", catalogId="catalog-1", setId="set-1",
        collectorNumber="1", rarity="uncommon", imageUri="http://images.test/card.webp",
        imageUriSmall=None, imageUriLarge=None,
        priceCardmarketTrend=2.0, priceCardmarketMin=1.5, priceCardmarketMax=4.0,
        priceCardtraderTrend=1.96, priceCardtraderMin=1.67, priceCardtraderMax=2.65,
    )
    card_set = SimpleNamespace(id="set-1", code="tst")
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_unique=AsyncMock(return_value=card_set)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[printing])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets/tst/cards")
    assert response.status_code == 200
    assert response.json()[0]["catalogId"] == "catalog-1"
    assert response.json()[0]["setCode"] == "tst"
    assert response.json()[0]["priceCardmarketMax"] == 4.0
    assert response.json()[0]["priceCardtraderMin"] == 1.67
    db.cardprinting.find_many.assert_awaited_once_with(
        where={"setId": "set-1"}, take=250, order={"collectorNumber": "asc"}
    )


@pytest.mark.asyncio
async def test_price_history_can_be_filtered_by_provider():
    printing = SimpleNamespace(id="printing-1")
    point = SimpleNamespace(provider="cardtrader", currency="EUR", trendPrice=4.9, minPrice=4.16, maxPrice=6.62, recordedAt="2026-09-08T00:00:00Z")
    db = SimpleNamespace(
        cardprinting=SimpleNamespace(find_unique=AsyncMock(return_value=printing)),
        cardpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[point])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/printings/printing-1/prices?provider=cardtrader")
    assert response.status_code == 200
    assert response.json()[0]["provider"] == "cardtrader"
