from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Optional


def _aware(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def calculate_price_trend(
    history: Iterable[Any],
    *,
    current_price: Optional[float],
    current_at: Optional[datetime],
    now: Optional[datetime] = None,
    window_days: int = 30,
) -> tuple[Optional[float], Optional[float]]:
    """Return signed Cardmarket price and percentage changes over the recent window.

    Uses the latest observation at or before the window start as baseline. If
    the printing has no older observation, its first observation in the window
    becomes the baseline, matching the catalog's existing price-mover behavior.
    """
    now = _aware(now or datetime.now(timezone.utc))
    window_start = now - timedelta(days=window_days)
    lookback_start = window_start - timedelta(days=window_days)
    points: list[tuple[datetime, float]] = []
    for point in history:
        observed_at = getattr(point, "date", None) or getattr(point, "recordedAt", None)
        if observed_at is None:
            continue
        observed_at = _aware(observed_at)
        if not lookback_start <= observed_at <= now:
            continue
        cents = getattr(point, "priceCents", None)
        raw_price = float(cents) / 100 if cents is not None else getattr(point, "trendPrice", None)
        if raw_price is not None:
            points.append((observed_at, float(raw_price)))

    if not points:
        return None, None
    points.sort(key=lambda point: point[0])
    before_window = [point for point in points if point[0] <= window_start]
    baseline_at, baseline_price = before_window[-1] if before_window else points[0]
    latest_at, latest_price = points[-1]

    if current_price is not None and current_at is not None and _aware(current_at) >= latest_at:
        latest_price = float(current_price)
    if baseline_price <= 0:
        return None, None

    change = latest_price - baseline_price
    return round(change, 4), round(change / baseline_price * 100, 4)


def calculate_price_change_since(
    history: Iterable[Any],
    *,
    acquired_at: date | datetime,
    current_price: Optional[float],
    current_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> tuple[Optional[float], Optional[float]]:
    """Compare the current quote to the last recorded price at acquisition."""
    checkpoint = _aware(acquired_at)
    now = _aware(now or datetime.now(timezone.utc))
    points: list[tuple[datetime, float]] = []
    for point in history:
        observed_at = getattr(point, "date", None) or getattr(point, "recordedAt", None)
        if observed_at is None:
            continue
        observed_at = _aware(observed_at)
        cents = getattr(point, "priceCents", None)
        value = float(cents) / 100 if cents is not None else getattr(point, "trendPrice", None)
        if checkpoint - timedelta(days=3) <= observed_at <= now and value is not None:
            points.append((observed_at, float(value)))
    if not points:
        return None, None
    points.sort(key=lambda item: item[0])
    baseline = next((price for at, price in reversed(points) if at <= checkpoint), None)
    if baseline is None or baseline <= 0:
        return None, None
    latest_at, latest_price = points[-1]
    if current_price is not None and current_at is not None and _aware(current_at) >= latest_at:
        latest_price = float(current_price)
    change = latest_price - baseline
    return round(change, 4), round(change / baseline * 100, 4)
