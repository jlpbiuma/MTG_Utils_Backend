from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_catalog_cards_expose_necessary_printing_fields():
    printing = SimpleNamespace(
        id="printing-1", catalogId="catalog-1", setId="set-1",
        catalog=SimpleNamespace(name="Test Card", typeLine="Creature — Test", manaCost="{1}{G}"),
        collectorNumber="1", rarity="uncommon", imageUri="http://images.test/card.webp",
        imageUriSmall=None, imageUriLarge=None,
        priceCardmarketTrend=2.0, priceCardmarketMin=1.5, priceCardmarketMax=4.0,
    )
    card_set = SimpleNamespace(id="set-1", code="tst")
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_unique=AsyncMock(return_value=card_set)),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[printing])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[SimpleNamespace(setCode="tst", collectorNumber="1", quantity=2)])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets/tst/cards")
    assert response.status_code == 200
    assert response.json()[0]["catalogId"] == "catalog-1"
    assert response.json()[0]["setCode"] == "tst"
    assert response.json()[0]["priceCardmarketMax"] == 4.0
    assert response.json()[0]["cardName"] == "Test Card"
    assert response.json()[0]["isOwned"] is True
    assert response.json()[0]["ownedQuantity"] == 2
    db.cardprinting.find_many.assert_awaited_once_with(
        where={"setId": "set-1"}, take=10000, order={"collectorNumber": "asc"}, include={"catalog": True}
    )


@pytest.mark.asyncio
async def test_catalog_cards_returns_every_printing_for_large_sets():
    printings = [
        SimpleNamespace(
            id=f"printing-{index}", catalogId=f"catalog-{index}", setId="large-set",
            catalog=SimpleNamespace(name=f"Card {index}", typeLine="Creature", manaCost=None),
            collectorNumber=str(index), rarity="common", imageUri=None,
            priceCardmarketTrend=None, priceCardmarketMin=None, priceCardmarketMax=None,
        )
        for index in range(301)
    ]
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_unique=AsyncMock(return_value=SimpleNamespace(id="large-set"))),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=printings)),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets/lrg/cards")
    assert response.status_code == 200
    assert len(response.json()) == 301
    assert response.json()[-1]["cardName"] == "Card 300"
    db.cardprinting.find_many.assert_awaited_once_with(
        where={"setId": "large-set"}, take=10000, order={"collectorNumber": "asc"}, include={"catalog": True}
    )


@pytest.mark.asyncio
async def test_card_owned_only_in_other_sets_is_missing_here_and_reports_all_owned_versions():
    printing = SimpleNamespace(
        id="marvel-printing", catalogId="captain-marvel-catalog", setId="marvel-set",
        catalog=SimpleNamespace(name="Captain Marvel, Earth's Protector", typeLine="Legendary Creature", manaCost=None),
        collectorNumber="042", rarity="rare", imageUri=None,
        priceCardmarketTrend=None, priceCardmarketMin=None, priceCardmarketMax=None,
    )
    owned_rows = [
        SimpleNamespace(id="collection-lotr", cardScryfallId="lotr-printing", cardName="Captain Marvel, Earth's Protector", setCode="lotr", collectorNumber="115", quantity=2),
        SimpleNamespace(id="collection-mcu", cardScryfallId="mcu-other-printing", cardName="Captain Marvel, Earth's Protector", setCode="mcu", collectorNumber="226", quantity=1),
    ]
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_unique=AsyncMock(return_value=SimpleNamespace(id="marvel-set", code="mcu"))),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[printing])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=owned_rows)),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets/mcu/cards")

    assert response.status_code == 200
    assert response.json()[0]["isOwned"] is False
    assert response.json()[0]["ownedQuantity"] == 0
    assert response.json()[0]["ownedElsewhere"] is True
    assert response.json()[0]["otherPrintings"] == [
        {"setCode": "LOTR", "collectorNumber": "115", "quantity": 2},
        {"setCode": "MCU", "collectorNumber": "226", "quantity": 1},
    ]
    query = db.collectioncard.find_many.call_args.kwargs["where"]
    assert set(query) == {"userId"}


@pytest.mark.asyncio
async def test_reality_fracture_commander_special_art_matches_index_and_detail_completion():
    card_set = SimpleNamespace(id="rfc-set", code="rfc", name="Reality Fracture Commander", setType="commander", cardCount=1, releasedAt=None, iconSvgUri=None)
    printings = [
        SimpleNamespace(id="rfc-base", catalogId="rfc-catalog", setId="rfc-set", collectorNumber="101", rarity="rare", imageUri=None, catalog=SimpleNamespace(name="Reality Fracture Hero", typeLine="Creature", manaCost=None)),
        SimpleNamespace(id="rfc-special-art", catalogId="rfc-catalog", setId="rfc-set", collectorNumber="101★", rarity="rare", imageUri=None, catalog=SimpleNamespace(name="Reality Fracture Hero", typeLine="Creature", manaCost=None)),
    ]
    owned_row = SimpleNamespace(
        id="owned-rfc-base", cardScryfallId="rfc-base", cardName="Reality Fracture Hero",
        setCode="rfc", collectorNumber="101", quantity=1,
    )
    db = SimpleNamespace(
        cardset=SimpleNamespace(
            find_unique=AsyncMock(return_value=card_set),
            find_many=AsyncMock(return_value=[card_set]),
        ),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=printings)),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[owned_row])),
        query_raw=AsyncMock(return_value=[{
            "code": "rfc", "cardCount": 2, "ownedCount": 1,
            "totalValueEur": 12.0, "ownedValueEur": 7.0, "missingValueEur": 5.0,
        }]),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listing = await client.get("/api/catalog/sets?limit=2000")
            detail = await client.get("/api/catalog/sets/rfc/cards")

    assert listing.status_code == detail.status_code == 200
    summary = listing.json()[0]
    detail_cards = detail.json()
    detail_owned = sum(1 for card in detail_cards if card["isOwned"])
    detail_total = len({card["id"] for card in detail_cards})
    detail_percentage = round(detail_owned * 100 / detail_total)
    assert (summary["ownedCount"], summary["cardCount"], summary["completionPercentage"]) == (
        detail_owned, detail_total, detail_percentage,
    ) == (1, 2, 50)
    base, special_art = detail_cards
    assert base["isOwned"] is True
    assert special_art["isOwned"] is False
    assert special_art["ownedElsewhere"] is True
    assert special_art["otherPrintings"] == [{"setCode": "RFC", "collectorNumber": "101", "quantity": 1}]
    listing_sql = db.query_raw.call_args.args[0]
    assert "lower(c.set_code) = lower(s.code)" in listing_sql
    assert "lower(c.collector_number) = lower(p.collector_number)" in listing_sql


@pytest.mark.asyncio
async def test_catalog_cards_include_absolute_and_percentage_price_trends():
    now = datetime.now(timezone.utc)
    printing = SimpleNamespace(
        id="printing-trend", catalogId="catalog-trend", setId="set-trend",
        catalog=SimpleNamespace(name="Trend Card", typeLine="Creature", manaCost=None),
        collectorNumber="1", rarity="rare", imageUri=None, imageUriSmall=None, imageUriLarge=None,
        priceCardmarketTrend=3.0, pricesUpdatedAt=now - timedelta(days=1),
    )
    points = [
        SimpleNamespace(scryfallId="printing-trend", finish=0, date=(now - timedelta(days=32)).date(), priceCents=200),
        SimpleNamespace(scryfallId="printing-trend", finish=0, date=(now - timedelta(days=2)).date(), priceCents=250),
    ]
    acquired_at = now - timedelta(days=32)
    owned_row = SimpleNamespace(
        id="owned-card", cardScryfallId="printing-trend", cardName="Trend Card",
        setCode="tst", collectorNumber="1", quantity=2, acquiredAt=acquired_at,
    )
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_unique=AsyncMock(return_value=SimpleNamespace(id="set-trend"))),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[printing])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[owned_row])),
        cmpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=points)),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets/tst/cards")
    assert response.status_code == 200
    assert response.json()[0]["priceTrendAbsoluteChange"] == 0.5
    assert response.json()[0]["priceTrendPercentageChange"] == 20.0
    assert response.json()[0]["collectionCardId"] == "owned-card"
    assert response.json()[0]["acquisitionTrendAbsoluteChange"] == 1.0
    assert response.json()[0]["acquisitionTrendPercentageChange"] == 50.0
    db.cmpricehistory.find_many.assert_awaited_once()
    history_filter = db.cmpricehistory.find_many.call_args.kwargs["where"]["date"]["gte"]
    assert isinstance(history_filter, datetime)
    assert history_filter.tzinfo is not None


@pytest.mark.asyncio
async def test_bulk_acquisition_date_only_targets_unset_user_collection_rows():
    db = SimpleNamespace(execute_raw=AsyncMock(return_value=3))
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                "/api/catalog/sets/tst/acquisition-dates", json={"acquiredAt": "2024-06-15"}
            )
    assert response.status_code == 200
    assert response.json() == {"updatedCount": 3}
    sql, user_id, set_code, acquired_at = db.execute_raw.call_args.args
    assert "acquired_at IS NULL" in sql
    assert "SET acquired_at = $3::timestamp" in sql
    assert "c.user_id = $1" in sql
    assert user_id
    assert set_code == "tst"
    assert acquired_at == datetime(2024, 6, 15, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_catalog_sets_include_collection_completion_in_one_collection_query():
    card_set = SimpleNamespace(code="tst", name="Test Set", setType="expansion", cardCount=3, releasedAt=None, iconSvgUri="https://images.test/set-icons/tst.webp")
    owned = [
        SimpleNamespace(setCode="tst", collectorNumber="1", quantity=3),
        SimpleNamespace(setCode="tst", collectorNumber="1", quantity=1),
        SimpleNamespace(setCode="other", collectorNumber="2", quantity=1),
    ]
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_many=AsyncMock(return_value=[card_set])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=owned)),
            query_raw=AsyncMock(return_value=[{"code": "tst", "cardCount": 3, "ownedCount": 1, "totalValueEur": 12.5, "ownedValueEur": 8.0, "missingValueEur": 7.5}]),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets?limit=2000")
    assert response.status_code == 200
    assert response.json()[0]["ownedCount"] == 1
    assert response.json()[0]["cardCount"] == 3
    assert response.json()[0]["completionPercentage"] == 33
    assert response.json()[0]["iconSvgUri"] == "https://images.test/set-icons/tst.webp"
    assert response.json()[0]["totalValueEur"] == 12.5
    assert response.json()[0]["ownedValueEur"] == 8.0
    assert response.json()[0]["missingValueEur"] == 7.5
    db.cardset.find_many.assert_awaited_once_with(take=2000, order={"releasedAt": "desc"})
    db.collectioncard.find_many.assert_awaited_once()
    db.query_raw.assert_awaited_once()
    expansion_query = db.query_raw.call_args.args[0]
    assert 'AS "missingValueEur"' in expansion_query
    assert "lower(c.card_scryfall_id) = lower(p.id)" in expansion_query
    assert "card_catalog" not in expansion_query


@pytest.mark.asyncio
async def test_expansion_progress_uses_actual_printings_instead_of_stale_set_card_count():
    card_set = SimpleNamespace(code="frc", name="Reality Fracture", setType="expansion", cardCount=116, releasedAt=None, iconSvgUri=None)
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_many=AsyncMock(return_value=[card_set])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
        query_raw=AsyncMock(return_value=[{
            "code": "frc", "cardCount": 140, "ownedCount": 85,
            "totalValueEur": 1000.0, "ownedValueEur": 500.0, "missingValueEur": 500.0,
        }]),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets?limit=2000")

    assert response.status_code == 200
    expansion = response.json()[0]
    assert expansion["cardCount"] == 140
    assert expansion["ownedCount"] == 85
    assert expansion["completionPercentage"] == 61
    query = db.query_raw.call_args.args[0]
    assert 'COUNT(DISTINCT p.id)::int AS "cardCount"' in query
    assert 'AS "ownedCount"' in query


@pytest.mark.asyncio
async def test_expansion_value_history_defaults_to_seven_days_and_tracks_owned_value():
    today = datetime.now(timezone.utc).date()
    printing = SimpleNamespace(
        id="p1", catalogId="c1", collectorNumber="1", priceCardmarketTrend=3.0,
        priceEur=2.0, catalog=SimpleNamespace(name="Test Card"),
    )
    db = SimpleNamespace(
        cardset=SimpleNamespace(find_unique=AsyncMock(return_value=SimpleNamespace(id="set1"))),
        cardprinting=SimpleNamespace(find_many=AsyncMock(return_value=[printing])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[
            SimpleNamespace(cardName="Test Card", cardScryfallId="p1", setCode="tst", collectorNumber="1", quantity=2),
            SimpleNamespace(cardName="Test Card", cardScryfallId="p1-other-set", setCode="oth", collectorNumber="45", quantity=10),
        ])),
        cmpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[SimpleNamespace(
            scryfallId="p1", date=today - timedelta(days=1), priceCents=250,
        )])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/sets/tst/value-history")
    assert response.status_code == 200
    body = response.json()
    assert body["windowDays"] == 7
    assert len(body["points"]) == 8
    assert body["points"][-1]["totalValue"] == 3.0
    assert body["points"][-1]["ownedValue"] == 6.0
    assert body["points"][-2]["totalValue"] == 2.5
    db.cmpricehistory.find_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_price_history_can_be_filtered_by_provider():
    printing = SimpleNamespace(id="printing-1")
    point = SimpleNamespace(provider="cardmarket", currency="EUR", trendPrice=4.9, minPrice=4.16, maxPrice=6.62, recordedAt="2026-09-08T00:00:00Z")
    db = SimpleNamespace(
        cardprinting=SimpleNamespace(find_unique=AsyncMock(return_value=printing)),
        cardpricehistory=SimpleNamespace(find_many=AsyncMock(return_value=[point])),
    )
    with patch("src.routers.catalog.db", new=db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/catalog/printings/printing-1/prices?provider=cardmarket")
    assert response.status_code == 200
    assert response.json()[0]["provider"] == "cardmarket"
