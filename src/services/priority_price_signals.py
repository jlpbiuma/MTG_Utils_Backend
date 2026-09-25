"""Price opportunities for exact printing/provider quotes, using recorded history."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.core.db import db
from src.schemas.priorities import PriorityPricePoint


def price_signal(current_price, history, now=None):
    now = now or datetime.now(timezone.utc)
    if current_price <= 0 or not history or history["days"] < 2:
        return None
    latest = history["latest"]
    if isinstance(latest, str):
        latest = datetime.fromisoformat(latest.replace("Z", "+00:00"))
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    if latest < now - timedelta(days=7):
        return None
    minimum = float(history["minimum"])
    baseline = history.get("baseline")
    change = round((current_price / float(baseline) - 1) * 100, 2) if baseline else None
    return {
        "historicalLow": min(minimum, current_price),
        "atHistoricalLow": Decimal(str(current_price)).quantize(Decimal("0.01")) <= Decimal(str(minimum)).quantize(Decimal("0.01")),
        "change30dPercent": change,
    }


async def enrich_price_signals(items, provider, currency, window_days=30):
    if not items:
        return
    rows = await db.query_raw('''
        WITH ids AS (
            SELECT jsonb_array_elements_text($1::jsonb) AS id
        ), cm AS (
            SELECT h.scryfall_id AS id, h.date::timestamptz AS at,
                   h.price_cents / 100.0 AS price
            FROM cm_price_history h JOIN ids ON ids.id = h.scryfall_id
            WHERE $2 = 'cardmarket' AND $3 = 'EUR' AND h.finish = 0
              AND h.price_cents > 0 AND h.date <= CURRENT_DATE
        ), history AS (
            SELECT * FROM cm
            UNION ALL
            SELECT h.card_printing_id, h.recorded_at, h.trend_price
            FROM card_price_history h JOIN ids ON ids.id = h.card_printing_id
            WHERE h.provider = $2 AND h.currency = $3 AND h.trend_price > 0
              AND h.recorded_at <= NOW()
              AND NOT EXISTS (SELECT 1 FROM cm WHERE cm.id = h.card_printing_id)
        ), daily AS (
            SELECT DISTINCT ON (id, at::date) id, at::date AS day, price
            FROM history
            WHERE at >= CURRENT_DATE - (($4::int - 1) * INTERVAL '1 day')
            ORDER BY id, at::date, at DESC
        )
        SELECT id, MIN(price) AS minimum, COUNT(DISTINCT at::date)::int AS days,
               MAX(at) AS latest,
               (ARRAY_AGG(price ORDER BY at DESC) FILTER (
                   WHERE at <= NOW() - ($4::int * INTERVAL '1 day')
                     AND at >= NOW() - (($4::int + 30) * INTERVAL '1 day')))[1] AS baseline
               , (ARRAY_AGG(price ORDER BY at DESC) FILTER (
                   WHERE at <= NOW() - INTERVAL '30 days'
                     AND at >= NOW() - INTERVAL '60 days'))[1] AS baseline30
               , (SELECT jsonb_agg(jsonb_build_object('date', d.day, 'price', d.price) ORDER BY d.day)
                  FROM daily d WHERE d.id = history.id) AS series
        FROM history GROUP BY id
    ''', json.dumps(list({item.cardScryfallId for item in items})), provider, currency, window_days)
    by_id = {row["id"]: row for row in rows}
    for item in items:
        record = by_id.get(item.cardScryfallId)
        series = record.get("series") if record else None
        if isinstance(series, str):
            series = json.loads(series)
        item.priceHistory = [PriorityPricePoint(**point) for point in (series or [])]
        signal = price_signal(item.price, record)
        if signal:
            item.historicalLow = signal["historicalLow"]
            item.atHistoricalLow = signal["atHistoricalLow"]
            item.priceChangePercent = signal["change30dPercent"]
            legacy = price_signal(item.price, {**record, "baseline": record.get("baseline30")})
            item.change30dPercent = legacy["change30dPercent"]
