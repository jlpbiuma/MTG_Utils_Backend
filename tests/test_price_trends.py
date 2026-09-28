from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.services.price_trends import calculate_price_trend, calculate_price_change_since


def test_calculate_price_trend_uses_last_baseline_and_current_quote():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    history = [
        SimpleNamespace(date=(now - timedelta(days=40)).date(), priceCents=150),
        SimpleNamespace(date=(now - timedelta(days=32)).date(), priceCents=200),
        SimpleNamespace(date=(now - timedelta(days=2)).date(), priceCents=250),
    ]

    assert calculate_price_trend(
        history,
        current_price=3.0,
        current_at=now - timedelta(days=1),
        now=now,
    ) == (1.0, 50.0)


def test_calculate_price_trend_uses_first_observation_for_new_printings():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    history = [
        SimpleNamespace(recordedAt=now - timedelta(days=10), trendPrice=4.0),
        SimpleNamespace(recordedAt=now - timedelta(days=1), trendPrice=3.0),
    ]

    assert calculate_price_trend(history, current_price=None, current_at=None, now=now) == (-1.0, -25.0)


def test_calculate_price_trend_returns_no_value_without_history_or_positive_baseline():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)

    assert calculate_price_trend([], current_price=2.0, current_at=now, now=now) == (None, None)
    assert calculate_price_trend(
        [SimpleNamespace(date=(now - timedelta(days=32)).date(), priceCents=0)],
        current_price=2.0,
        current_at=now,
        now=now,
    ) == (None, None)


def test_calculate_price_change_since_uses_last_quote_at_acquisition_date():
    acquired_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    history = [
        SimpleNamespace(date=(acquired_at - timedelta(days=1)).date(), priceCents=200),
        SimpleNamespace(date=acquired_at.date(), priceCents=250),
        SimpleNamespace(date=(now - timedelta(days=1)).date(), priceCents=300),
    ]
    assert calculate_price_change_since(
        history, acquired_at=acquired_at, current_price=3.5,
        current_at=now, now=now,
    ) == (1.0, 40.0)


def test_calculate_price_change_since_requires_a_price_on_or_before_checkpoint():
    acquired_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    history = [SimpleNamespace(date=(acquired_at + timedelta(days=1)).date(), priceCents=250)]
    assert calculate_price_change_since(
        history, acquired_at=acquired_at, current_price=3.0,
        current_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    ) == (None, None)
