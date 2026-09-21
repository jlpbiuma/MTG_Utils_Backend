from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.price_movers_service import compute_mover_for_points


def _dt(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _point(printing_id: str, price: float, days_ago: int):
    return SimpleNamespace(
        cardPrintingId=printing_id,
        trendPrice=price,
        recordedAt=_dt(days_ago),
        provider="cardmarket",
        currency="EUR",
    )


def _printing(printing_id: str = "p1", name: str = "Sol Ring", trend: float = 5.0):
    return SimpleNamespace(
        id=printing_id,
        catalogId="cat-1",
        collectorNumber="1",
        imageUri="http://img/card.jpg",
        imageUriSmall=None,
        imageUriLarge=None,
        priceCardmarketTrend=trend,
        pricesUpdatedAt=_dt(0),
        catalog=SimpleNamespace(name=name),
        set=SimpleNamespace(code="c21"),
    )


def test_compute_mover_gainer_within_window():
    window_start = _dt(30)
    points = [_point("p1", 2.0, 25), _point("p1", 4.0, 2)]
    item = compute_mover_for_points(
        points,
        window_start=window_start,
        printing=_printing(trend=4.0),
        provider="cardmarket",
        currency="EUR",
        symbol="€",
    )
    assert item is not None
    assert item.changePct == 100.0
    assert item.changeAbs == 2.0
    assert item.cardName == "Sol Ring"
    assert item.setCode == "c21"


def test_compute_mover_uses_pre_window_baseline():
    window_start = _dt(30)
    points = [_point("p1", 10.0, 40), _point("p1", 8.0, 5)]
    item = compute_mover_for_points(
        points,
        window_start=window_start,
        printing=_printing(trend=8.0),
        provider="cardmarket",
        currency="EUR",
        symbol="€",
    )
    assert item is not None
    assert item.baselinePrice == 10.0
    assert item.currentPrice == 8.0
    assert item.changePct == -20.0


def test_compute_mover_skips_flat_single_point():
    window_start = _dt(30)
    points = [_point("p1", 3.0, 10)]
    printing = _printing(trend=3.0)
    printing.pricesUpdatedAt = points[0].recordedAt
    item = compute_mover_for_points(
        points,
        window_start=window_start,
        printing=printing,
        provider="cardmarket",
        currency="EUR",
        symbol="€",
    )
    assert item is None


@pytest.mark.asyncio
async def test_movers_endpoint_returns_gainers_and_losers():
    now = datetime.now(timezone.utc)
    gainer_hist = [
        SimpleNamespace(
            cardPrintingId="up-1",
            trendPrice=1.0,
            recordedAt=now - timedelta(days=20),
            provider="cardmarket",
            currency="EUR",
        ),
        SimpleNamespace(
            cardPrintingId="up-1",
            trendPrice=2.0,
            recordedAt=now - timedelta(days=2),
            provider="cardmarket",
            currency="EUR",
        ),
    ]
    loser_hist = [
        SimpleNamespace(
            cardPrintingId="down-1",
            trendPrice=5.0,
            recordedAt=now - timedelta(days=20),
            provider="cardmarket",
            currency="EUR",
        ),
        SimpleNamespace(
            cardPrintingId="down-1",
            trendPrice=4.0,
            recordedAt=now - timedelta(days=2),
            provider="cardmarket",
            currency="EUR",
        ),
    ]
    printings = [
        _printing("up-1", "Gainer", 2.0),
        _printing("down-1", "Loser", 4.0),
    ]
    db = SimpleNamespace(
        cardpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=gainer_hist + loser_hist)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=printings)),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )
    with patch("src.services.price_movers_service.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/pricing/movers?provider=cardmarket&windowDays=30&limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "cardmarket"
    assert body["windowDays"] == 30
    assert body["scope"] == "global"
    assert body["gainers"][0]["cardName"] == "Gainer"
    assert body["gainers"][0]["changePct"] == 100.0
    assert body["losers"][0]["cardName"] == "Loser"
    assert body["losers"][0]["changePct"] == -20.0


@pytest.mark.asyncio
async def test_movers_collection_scope_filters_printings():
    now = datetime.now(timezone.utc)
    histories = [
        SimpleNamespace(
            cardPrintingId="owned-1",
            trendPrice=1.0,
            recordedAt=now - timedelta(days=20),
            provider="cardmarket",
            currency="EUR",
        ),
        SimpleNamespace(
            cardPrintingId="owned-1",
            trendPrice=1.5,
            recordedAt=now - timedelta(days=1),
            provider="cardmarket",
            currency="EUR",
        ),
    ]
    db = SimpleNamespace(
        collectioncard=SimpleNamespace(
            find_many=AsyncMock(return_value=[SimpleNamespace(cardScryfallId="owned-1")])
        ),
        cardpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=histories)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[_printing("owned-1", "Owned", 1.5)])),
    )
    with patch("src.services.price_movers_service.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/pricing/movers?scope=collection")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "collection"
    assert len(body["gainers"]) == 1
    assert body["gainers"][0]["printingId"] == "owned-1"
    call_where = db.cardpricehistory.find_many.await_args.kwargs["where"]
    assert call_where["cardPrintingId"]["in"] == ["owned-1"]


@pytest.mark.asyncio
async def test_card_price_history_multi_printing():
    catalog = SimpleNamespace(id="cat-1", name="Sol Ring")
    set_a = SimpleNamespace(code="c21")
    set_b = SimpleNamespace(code="cmr")
    p1 = SimpleNamespace(
        id="print-a",
        catalogId="cat-1",
        collectorNumber="1",
        imageUri="a.jpg",
        imageUriSmall=None,
        set=set_a,
    )
    p2 = SimpleNamespace(
        id="print-b",
        catalogId="cat-1",
        collectorNumber="2",
        imageUri="b.jpg",
        imageUriSmall=None,
        set=set_b,
    )
    points = [
        SimpleNamespace(
            cardPrintingId="print-a",
            provider="cardmarket",
            currency="EUR",
            trendPrice=2.0,
            minPrice=1.5,
            maxPrice=3.0,
            recordedAt="2026-09-01T00:00:00Z",
        ),
        SimpleNamespace(
            cardPrintingId="print-b",
            provider="cardmarket",
            currency="EUR",
            trendPrice=3.0,
            minPrice=2.0,
            maxPrice=4.0,
            recordedAt="2026-09-05T00:00:00Z",
        ),
    ]
    db = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=AsyncMock(return_value=catalog)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[p1, p2])),
        cardpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=points)),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/cards/cat-1/price-history?provider=cardmarket&days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["catalogId"] == "cat-1"
    assert body["cardName"] == "Sol Ring"
    assert len(body["series"]) == 2
    assert body["series"][0]["setCode"] == "c21"
    assert body["series"][0]["points"][0]["trendPrice"] == 2.0
    assert body["series"][1]["setCode"] == "cmr"


@pytest.mark.asyncio
async def test_card_price_history_resolves_printing_id():
    catalog = SimpleNamespace(id="cat-1", name="Sol Ring")
    printing = SimpleNamespace(id="print-a", catalogId="cat-1")
    set_a = SimpleNamespace(code="c21")
    p1 = SimpleNamespace(
        id="print-a",
        catalogId="cat-1",
        collectorNumber="1",
        imageUri=None,
        imageUriSmall=None,
        set=set_a,
    )
    db = SimpleNamespace(
        cardcatalog=SimpleNamespace(
            find_unique=AsyncMock(side_effect=[None, catalog]),
        ),
        cardprinting=SimpleNamespace(
            find_unique=AsyncMock(return_value=printing),
            find_many=AsyncMock(return_value=[p1]),
        ),
        cardpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/cards/print-a/price-history")
    assert response.status_code == 200
    assert response.json()["catalogId"] == "cat-1"
    assert response.json()["series"][0]["printingId"] == "print-a"
