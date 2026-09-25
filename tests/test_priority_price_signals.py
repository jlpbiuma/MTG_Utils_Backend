from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from src.services.priority_price_signals import price_signal, enrich_price_signals

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def history(**changes):
    return {"days": 40, "minimum": 2, "baseline": 4, "latest": NOW, **changes}


def test_falling_and_historical_low_are_independent():
    assert price_signal(3, history(), NOW) == {
        "historicalLow": 2, "atHistoricalLow": False, "change30dPercent": -25,
    }
    assert price_signal(2, history(), NOW)["atHistoricalLow"] is True
    assert price_signal(1.5, history(), NOW)["atHistoricalLow"] is True
    assert price_signal(5, history(), NOW)["change30dPercent"] == 25


@pytest.mark.parametrize("record", [None, history(days=1), history(latest=NOW - timedelta(days=8))])
def test_insufficient_or_stale_history_is_not_an_opportunity(record):
    assert price_signal(1, record, NOW) is None


def test_missing_month_baseline_does_not_invent_a_decline():
    assert price_signal(2, history(baseline=None), NOW)["change30dPercent"] is None
    assert price_signal(0, history(), NOW) is None


@pytest.mark.asyncio
async def test_batch_matches_exact_printings_and_passes_provider_currency():
    items = [SimpleNamespace(cardScryfallId=sid, price=2, historicalLow=None) for sid in ["cheap", "other"]]
    row = {"id": "cheap", "series": [{"date": "2026-09-01", "price": 4}, {"date": "2026-09-24", "price": 2}], **history(latest=datetime.now(timezone.utc))}
    query = AsyncMock(return_value=[row])
    with patch("src.services.priority_price_signals.db", SimpleNamespace(query_raw=query)):
        await enrich_price_signals(items, "cardmarket", "EUR")
    assert [point.price for point in items[0].priceHistory] == [4, 2]
    assert items[0].priceHistory[0].date == "2026-09-01"
    assert items[1].priceHistory == []
    assert items[0].atHistoricalLow is True
    assert items[1].historicalLow is None
    assert query.await_count == 1
    assert query.call_args.args[-3:] == ("cardmarket", "EUR", 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("window_days", [7, 90, 365])
async def test_period_is_parameterized_and_keeps_legacy_30_day_change(window_days):
    item = SimpleNamespace(cardScryfallId="card", price=2)
    row = {"id": "card", **history(latest=datetime.now(timezone.utc), baseline=4), "baseline30": 1}
    query = AsyncMock(return_value=[row])
    with patch("src.services.priority_price_signals.db", SimpleNamespace(query_raw=query)):
        await enrich_price_signals([item], "cardmarket", "EUR", window_days)
    assert query.call_args.args[-1] == window_days
    assert item.priceChangePercent == -50
    assert item.change30dPercent == 100
