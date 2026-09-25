from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.routers.catalog import (
    clear_price_history_cache,
    get_cached_price_history,
    set_cached_price_history,
)
from src.schemas.pricing import CardPriceHistoryResponse, PrintingPriceSeries, PriceHistoryPoint


@pytest.fixture(autouse=True)
def reset_cache():
    clear_price_history_cache()
    yield
    clear_price_history_cache()


@pytest.mark.asyncio
async def test_price_history_cache_hit_and_headers():
    catalog = SimpleNamespace(id="cat-sf", name="Sacred Foundry")
    set_grn = SimpleNamespace(code="grn", name="Guilds of Ravnica", iconSvgUri=None, releasedAt=None)
    p1 = SimpleNamespace(
        id="print-sf-1",
        catalogId="cat-sf",
        collectorNumber="254",
        rarity="rare",
        imageUri="http://img/sf1.jpg",
        imageUriSmall=None,
        priceEur=12.5,
        priceCardmarketTrend=13.0,
        releasedAt=datetime(2018, 10, 5, tzinfo=timezone.utc),
        set=set_grn,
    )
    p2 = SimpleNamespace(
        id="print-sf-2",
        catalogId="cat-sf",
        collectorNumber="245",
        rarity="rare",
        imageUri="http://img/sf2.jpg",
        imageUriSmall=None,
        priceEur=14.0,
        priceCardmarketTrend=14.2,
        releasedAt=datetime(2020, 1, 1, tzinfo=timezone.utc),
        set=set_grn,
    )
    point1 = SimpleNamespace(
        scryfallId="print-sf-1",
        date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        priceCents=1300,
        finish=0,
    )

    db_mock = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=AsyncMock(return_value=catalog)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[p1, p2])),
        cmpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[point1])),
    )

    with patch("src.routers.catalog.db", new=db_mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # First request: Cache MISS
            res1 = await client.get("/api/catalog/cards/cat-sf/price-history?provider=cardmarket&days=30")
            assert res1.status_code == 200
            assert res1.headers.get("X-Cache") == "MISS"
            assert "public, max-age=3600" in res1.headers.get("Cache-Control", "")
            data1 = res1.json()
            assert data1["catalogId"] == "cat-sf"
            assert len(data1["series"]) == 2
            assert db_mock.cardcatalog.find_unique.call_count == 1
            assert db_mock.cardprinting.find_many.call_count == 1

            # Second request with catalogId: Cache HIT
            res2 = await client.get("/api/catalog/cards/cat-sf/price-history?provider=cardmarket&days=30")
            assert res2.status_code == 200
            assert res2.headers.get("X-Cache") == "HIT"
            # DB should NOT be called again
            assert db_mock.cardcatalog.find_unique.call_count == 1
            assert db_mock.cardprinting.find_many.call_count == 1

            # Third request with printingId (print-sf-1): Cache HIT via printing-to-catalog alias map!
            res3 = await client.get("/api/catalog/cards/print-sf-1/price-history?provider=cardmarket&days=30")
            assert res3.status_code == 200
            assert res3.headers.get("X-Cache") == "HIT"
            assert res3.json()["catalogId"] == "cat-sf"
            # DB should STILL NOT be called again
            assert db_mock.cardcatalog.find_unique.call_count == 1
            assert db_mock.cardprinting.find_many.call_count == 1


@pytest.mark.asyncio
async def test_price_history_printing_id_populates_cache_for_all_printings():
    catalog = SimpleNamespace(id="cat-sf", name="Sacred Foundry")
    printing = SimpleNamespace(id="print-sf-2", catalogId="cat-sf")
    set_grn = SimpleNamespace(code="grn", name="Guilds of Ravnica", iconSvgUri=None, releasedAt=None)
    p1 = SimpleNamespace(
        id="print-sf-1",
        catalogId="cat-sf",
        collectorNumber="254",
        rarity="rare",
        imageUri=None,
        imageUriSmall=None,
        releasedAt=None,
        set=set_grn,
    )
    p2 = SimpleNamespace(
        id="print-sf-2",
        catalogId="cat-sf",
        collectorNumber="245",
        rarity="rare",
        imageUri=None,
        imageUriSmall=None,
        releasedAt=None,
        set=set_grn,
    )

    db_mock = SimpleNamespace(
        cardcatalog=SimpleNamespace(
            find_unique=AsyncMock(side_effect=[None, catalog]),
        ),
        cardprinting=SimpleNamespace(
            find_unique=AsyncMock(return_value=printing),
            find_many=AsyncMock(return_value=[p1, p2]),
        ),
        cmpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with patch("src.routers.catalog.db", new=db_mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Request initially using printing ID print-sf-2
            res1 = await client.get("/api/catalog/cards/print-sf-2/price-history")
            assert res1.status_code == 200
            assert res1.headers.get("X-Cache") == "MISS"

            # Subsequent request using catalog ID hits cache
            res2 = await client.get("/api/catalog/cards/cat-sf/price-history")
            assert res2.status_code == 200
            assert res2.headers.get("X-Cache") == "HIT"

            # Subsequent request using OTHER printing ID (print-sf-1) ALSO hits cache
            res3 = await client.get("/api/catalog/cards/print-sf-1/price-history")
            assert res3.status_code == 200
            assert res3.headers.get("X-Cache") == "HIT"


@pytest.mark.asyncio
async def test_price_history_major_expansions_and_reprints():
    catalog = SimpleNamespace(id="cat-sf", name="Sacred Foundry")
    set_grn = SimpleNamespace(code="grn", name="Guilds of Ravnica", iconSvgUri="http://img/grn.svg", releasedAt=datetime(2018, 10, 5, tzinfo=timezone.utc))
    p1 = SimpleNamespace(
        id="print-sf-1",
        catalogId="cat-sf",
        collectorNumber="254",
        rarity="rare",
        imageUri=None,
        imageUriSmall=None,
        releasedAt=datetime(2018, 10, 5, tzinfo=timezone.utc),
        priceEur=12.5,
        priceCardmarketTrend=13.0,
        set=set_grn,
    )

    major_sets_mock = [
        {"code": "grn", "name": "Guilds of Ravnica", "setType": "expansion", "releasedAt": "2018-10-05T00:00:00+00:00", "iconSvgUri": "http://img/grn.svg"},
        {"code": "rna", "name": "Ravnica Allegiance", "setType": "expansion", "releasedAt": "2019-01-25T00:00:00+00:00", "iconSvgUri": "http://img/rna.svg"},
        {"code": "war", "name": "War of the Spark", "setType": "expansion", "releasedAt": "2019-05-03T00:00:00+00:00", "iconSvgUri": "http://img/war.svg"},
    ]

    db_mock = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=AsyncMock(return_value=catalog)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[p1])),
        cmpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[])),
        query_raw=AsyncMock(return_value=major_sets_mock),
    )

    with patch("src.routers.catalog.db", new=db_mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/catalog/cards/cat-sf/price-history?days=3650")
            assert res.status_code == 200
            data = res.json()
            expansions = data["expansions"]
            assert len(expansions) == 3

            # grn has this card printed
            grn_exp = next(e for e in expansions if e["setCode"] == "grn")
            assert grn_exp["hasPrinting"] is True
            assert grn_exp["printingId"] == "print-sf-1"
            assert grn_exp["trendPrice"] == 13.0

            # rna and war are major MTG expansion launches where this card was not printed
            rna_exp = next(e for e in expansions if e["setCode"] == "rna")
            assert rna_exp["hasPrinting"] is False
            assert rna_exp["printingId"] is None

            war_exp = next(e for e in expansions if e["setCode"] == "war")
            assert war_exp["hasPrinting"] is False
