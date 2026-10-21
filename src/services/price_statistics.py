"""All-time historical statistics, batched by printing/finish without filling gaps."""
from collections import defaultdict
from src.schemas.price_statistics import PriceHistoryStatistics

# One actual observation per printing/finish/day. MTGJSON takes precedence over
# the legacy series on overlapping dates; legacy history does not identify foil.
DAILY_HISTORY = """
WITH requested AS (
    SELECT * FROM UNNEST($1::text[], $2::int[], $3::int[]) AS r(printing_id, finish, quantity)
), cm_prices AS (
    SELECT r.printing_id, r.finish, h.date AS day, h.price_cents / 100.0 AS price
    FROM requested r JOIN cm_price_history h
      ON h.scryfall_id = r.printing_id AND h.finish = r.finish
    WHERE h.price_cents >= 0
), legacy_prices AS (
    SELECT DISTINCT ON (r.printing_id, h.recorded_at::date)
        r.printing_id, r.finish, h.recorded_at::date AS day, h.trend_price AS price
    FROM requested r JOIN card_price_history h ON h.card_printing_id = r.printing_id
    WHERE r.finish = 0 AND h.provider = 'cardmarket' AND h.currency = 'EUR'
      AND h.trend_price >= 0
      AND NOT EXISTS (SELECT 1 FROM cm_price_history cm
        WHERE cm.scryfall_id = r.printing_id AND cm.finish = r.finish
          AND cm.date = h.recorded_at::date AND cm.price_cents >= 0)
    ORDER BY r.printing_id, h.recorded_at::date, h.recorded_at DESC, h.id
), daily_prices AS (
    SELECT * FROM cm_prices UNION ALL SELECT * FROM legacy_prices
)
"""
SUMMARY = """
COUNT(*)::int AS observations, AVG(price)::float AS mean,
STDDEV_POP(price)::float AS "standardDeviation",
MIN(day)::text AS "firstObservedOn", MAX(day)::text AS "lastObservedOn"
"""


async def get_history_statistics(client, items, *, include_total=False):
    """items: (printing_id, is_foil, quantity). Total uses common dates and covariance."""
    quantities = defaultdict(int)
    for printing_id, is_foil, quantity in items:
        if printing_id and quantity > 0:
            quantities[(printing_id, int(is_foil))] += quantity
    empty = PriceHistoryStatistics()
    if not quantities or not callable(getattr(client, 'query_raw', None)):
        return {}, empty
    ids = [key[0] for key in quantities]
    finishes = [key[1] for key in quantities]
    amounts = list(quantities.values())
    rows = await client.query_raw(DAILY_HISTORY + "SELECT printing_id, finish, " + SUMMARY +
                                  " FROM daily_prices GROUP BY printing_id, finish", ids, finishes, amounts)
    statistics = {(row['printing_id'], bool(row['finish'])): PriceHistoryStatistics(
        **{key: value for key, value in row.items() if key not in ('printing_id', 'finish')}
    ) for row in rows if 'printing_id' in row}
    total = empty
    if include_total and len(statistics) == len(quantities):
        rows = await client.query_raw(DAILY_HISTORY + """, totals AS (
            SELECT h.day, SUM(h.price * r.quantity) AS price
            FROM daily_prices h JOIN requested r USING (printing_id, finish)
            GROUP BY h.day HAVING COUNT(*) = (SELECT COUNT(*) FROM requested)
        ) SELECT """ + SUMMARY + " FROM totals", ids, finishes, amounts)
        if rows:
            total = PriceHistoryStatistics(**rows[0])
    return statistics, total


async def enrich_purchase_history(client, purchase):
    items = [(card.historyPrintingId, card.isFoil, card.quantity) for card in purchase.cards]
    statistics, total = await get_history_statistics(client, items, include_total=True)
    for card in purchase.cards:
        card.historyStatistics = statistics.get((card.historyPrintingId, card.isFoil), PriceHistoryStatistics())
    purchase.historyCoveredCopies = sum(card.quantity for card in purchase.cards if card.historyStatistics.observations)
    purchase.missingHistoryCopies = sum(card.quantity for card in purchase.cards if not card.historyStatistics.observations)
    # Missing identities must not yield a misleading partial portfolio statistic.
    purchase.historyStatistics = total if not purchase.missingHistoryCopies else PriceHistoryStatistics()
