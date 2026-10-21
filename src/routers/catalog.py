import logging
import time
from datetime import datetime, timedelta, timezone, time as datetime_time
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from src.core.auth import get_current_user_id
from fastapi import Depends

from src.core.db import db
from src.schemas.pricing import (
    CardPrintingResponse,
    CardSetResponse,
    PriceHistoryPoint,
    PriceProvider,
    CardPriceHistoryResponse,
    PrintingPriceSeries,
    CardExpansionRelease,
    ExpansionValueHistoryResponse,
)
from src.schemas.collection import CollectionAcquisitionDateUpdate
from src.services.pricing_service import PRICE_PROVIDERS
from src.services.price_trends import calculate_price_trend, calculate_price_change_since
from src.services.set_release_dates import corrected_set_release
from src.services.card_utils import normalize_card_name
from src.services.price_statistics import get_history_statistics
from src.services.expansion_completion import is_base_printing, base_printing_sql, BASE_CHECKLIST_LIMITS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])

PRICE_HISTORY_CACHE_TTL = 3600  # 1 hour
PRICE_HISTORY_CACHE_MAX_ENTRIES = 2000

# Cache store: (catalog_id, provider, days) -> (timestamp, CardPriceHistoryResponse)
_price_history_cache: dict[tuple[str, str, Optional[int]], tuple[float, CardPriceHistoryResponse]] = {}
# Fast lookup index: printing_id -> catalog_id
_printing_to_catalog_map: dict[str, str] = {}

_major_expansions_cache: list[dict] = []
_major_expansions_cache_ts: float = 0
MAJOR_EXPANSIONS_CACHE_TTL = 3600  # 1 hour


async def get_major_expansions() -> list[dict]:
    """Retrieve all primary MTG expansion set releases (like in MTGGoldfish) ordered by release date."""
    global _major_expansions_cache, _major_expansions_cache_ts
    now = time.time()
    if _major_expansions_cache and (now - _major_expansions_cache_ts < MAJOR_EXPANSIONS_CACHE_TTL):
        return _major_expansions_cache

    query = """
        WITH eligible_sets AS (
            SELECT code, name, set_type, released_at, card_count, icon_svg_uri,
                   CASE set_type
                       WHEN 'expansion' THEN 1
                       WHEN 'core' THEN 2
                       WHEN 'draft_innovation' THEN 3
                       WHEN 'masters' THEN 4
                       WHEN 'commander' THEN 5
                       ELSE 6
                   END as type_rank
            FROM card_sets
            WHERE is_digital = false
              AND set_type IN ('expansion', 'core', 'masters', 'draft_innovation', 'commander')
              AND released_at IS NOT NULL
              AND card_count >= 30
              AND code != 'plst'
        ),
        ranked AS (
            SELECT code, name, set_type, released_at, card_count, icon_svg_uri,
                   ROW_NUMBER() OVER (
                       PARTITION BY released_at::date
                       ORDER BY type_rank ASC, card_count DESC
                   ) as rn
            FROM eligible_sets
        )
        SELECT code, name, set_type as "setType", released_at as "releasedAt", icon_svg_uri as "iconSvgUri"
        FROM ranked
        WHERE rn = 1
        ORDER BY released_at ASC;
    """
    try:
        if hasattr(db, "query_raw") and callable(getattr(db, "query_raw", None)):
            res = await db.query_raw(query)
            if res:
                _major_expansions_cache = res
                _major_expansions_cache_ts = now
                return res
    except Exception as e:
        logger.warning(f"Error fetching major expansions: {e}")

    return _major_expansions_cache


def get_cached_price_history(
    catalog_id: str, provider: str, days: Optional[int]
) -> Optional[CardPriceHistoryResponse]:
    entry = _price_history_cache.get((catalog_id, provider, days))
    if entry:
        ts, data = entry
        if time.time() - ts < PRICE_HISTORY_CACHE_TTL:
            return data
        _price_history_cache.pop((catalog_id, provider, days), None)
    return None


def set_cached_price_history(
    catalog_id: str, provider: str, days: Optional[int], data: CardPriceHistoryResponse
):
    if len(_price_history_cache) >= PRICE_HISTORY_CACHE_MAX_ENTRIES:
        sorted_keys = sorted(_price_history_cache.keys(), key=lambda k: _price_history_cache[k][0])
        for k in sorted_keys[: len(sorted_keys) // 5 + 1]:
            _price_history_cache.pop(k, None)
    _price_history_cache[(catalog_id, provider, days)] = (time.time(), data)


def clear_price_history_cache():
    global _major_expansions_cache_ts
    _price_history_cache.clear()
    _printing_to_catalog_map.clear()
    _major_expansions_cache_ts = 0


@router.get("/sets", response_model=list[CardSetResponse])
async def list_sets(limit: int = Query(2000, ge=1, le=2000), user_id: str = Depends(get_current_user_id)):
    sets = await db.cardset.find_many(take=limit, order={"releasedAt": "desc"})
    rows = await db.collectioncard.find_many(where={"userId": user_id, "setCode": {"not": None}})
    value_rows = []
    value_by_set: dict[str, tuple[float, float, float]] = {}
    counts_by_set: dict[str, tuple[int, int]] = {}
    available_by_set: dict[str, bool] = {}
    alternatives_by_set: dict[str, int] = {}
    if hasattr(db, "query_raw"):
        try:
            # Resolve exact IDs using their index, then only resolve unmatched rows
            # by set/collector number. A correlated OR would scan the catalog for
            # each inventory row; each row must contribute to exactly one printing.
            base_condition = base_printing_sql()
            value_rows = await db.query_raw(f"""
                WITH scoped_owned AS (
                    SELECT id, card_scryfall_id, set_code, collector_number, quantity, is_foil
                    FROM user_collections WHERE user_id = $1
                ), fallback_owned AS (
                    SELECT DISTINCT ON (c.id)
                        c.id, p.id AS printing_id, c.quantity, c.is_foil
                    FROM scoped_owned c
                    JOIN card_sets cs ON lower(cs.code)=lower(c.set_code)
                    JOIN card_printings p ON p.set_id=cs.id
                        AND lower(p.collector_number)=lower(c.collector_number)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM card_printings exact WHERE exact.id=c.card_scryfall_id
                    )
                    ORDER BY c.id, (p.collector_number=c.collector_number) DESC, p.id
                ), resolved_owned AS (
                    SELECT printing_id, SUM(quantity)::int AS quantity,
                        SUM(quantity) FILTER (WHERE NOT is_foil)::int AS normal_quantity,
                        SUM(quantity) FILTER (WHERE is_foil)::int AS foil_quantity
                    FROM (
                        SELECT p.id AS printing_id, c.quantity, c.is_foil
                        FROM scoped_owned c JOIN card_printings p ON p.id=c.card_scryfall_id
                        UNION ALL
                        SELECT printing_id, quantity, is_foil FROM fallback_owned
                    ) matches
                    GROUP BY printing_id
                ), completion_groups AS (
                    SELECT p.set_id, COALESCE('catalog:' || p.catalog_id, 'printing:' || p.id) AS identity,
                        BOOL_OR({base_condition}) AS in_checklist,
                        BOOL_OR(COALESCE(owned.quantity, 0) > 0) AS is_owned,
                        COUNT(*)::int AS versions
                    FROM card_printings p
                    JOIN card_sets s ON s.id=p.set_id
                    LEFT JOIN resolved_owned owned ON owned.printing_id=p.id
                    GROUP BY p.set_id, COALESCE('catalog:' || p.catalog_id, 'printing:' || p.id)
                ), completion_stats AS (
                    SELECT set_id,
                        COUNT(*) FILTER (WHERE in_checklist)::int AS total,
                        COUNT(*) FILTER (WHERE in_checklist AND is_owned)::int AS owned,
                        COALESCE(SUM(versions - 1) FILTER (WHERE in_checklist), 0)::int AS alternatives
                    FROM completion_groups GROUP BY set_id
                )
                SELECT s.code,
                       COALESCE(stats.total, 0)::int AS "cardCount",
                       COALESCE(stats.alternatives, 0)::int AS "alternativeVersionCount",
                       COUNT(p.id) FILTER(WHERE ({base_condition}) AND COALESCE(p.price_cardmarket_trend,p.price_eur,0)>0)::int AS "pricedCount",
                       COALESCE(stats.owned, 0)::int AS "ownedCount",
                       COALESCE(SUM(COALESCE(p.price_cardmarket_trend, p.price_eur, 0)) FILTER (WHERE {base_condition}), 0)::float AS "totalValueEur",
                       COALESCE(SUM((COALESCE(p.price_cardmarket_trend, p.price_eur, 0) * COALESCE(owned.normal_quantity, 0) + COALESCE(p.price_eur_foil, 0) * COALESCE(owned.foil_quantity, 0))), 0)::float AS "ownedValueEur",
                       COALESCE(SUM(CASE WHEN ({base_condition}) AND COALESCE(owned.quantity, 0) = 0
                           THEN COALESCE(p.price_cardmarket_trend, p.price_eur, 0) ELSE 0 END), 0)::float AS "missingValueEur"
                FROM card_sets s
                LEFT JOIN card_printings p ON p.set_id = s.id
                LEFT JOIN resolved_owned owned ON owned.printing_id = p.id
                LEFT JOIN completion_stats stats ON stats.set_id=s.id
                GROUP BY s.code, stats.total, stats.owned, stats.alternatives
            """, user_id)
            value_by_set = {
                str(row["code"]).lower(): (
                    float(row.get("totalValueEur") or 0),
                    float(row.get("ownedValueEur") or 0),
                    float(row.get("missingValueEur") or 0),
                )
                for row in (value_rows or [])
            }
            alternatives_by_set = {str(row["code"]).lower(): int(row.get("alternativeVersionCount") or 0) for row in (value_rows or [])}
            available_by_set = {str(row["code"]).lower(): row.get("pricedCount", 1) > 0 for row in (value_rows or [])}
            counts_by_set = {
                str(row["code"]).lower(): (int(row.get("cardCount") or 0), int(row.get("ownedCount") or 0))
                for row in (value_rows or [])
            }
        except Exception:
            logger.exception("Unable to aggregate expansion values")
    batch_stats = {}
    try:
        stats = await db.query_raw("""WITH lot_values AS (
          SELECT b.id,codes.code AS set_code,b.currency,b.cost_total+b.shipping AS investment,
            COALESCE(SUM(CASE WHEN (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END)>=0.25
              THEN (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END)*i.quantity ELSE 0 END),0) AS value,
            COUNT(i.id) AS assigned,
            COUNT(i.id) FILTER(WHERE (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END) IS NULL) AS unpriced
          FROM acquisition_batches b
          CROSS JOIN LATERAL (SELECT lower(b.set_code) AS code WHERE b.set_code IS NOT NULL
            UNION SELECT lower(s.code) FROM acquisition_batch_items bi JOIN card_printings bp ON bp.id=bi.printing_id
              JOIN card_sets s ON s.id=bp.set_id WHERE bi.batch_id=b.id) codes
          LEFT JOIN acquisition_batch_items i ON i.batch_id=b.id
          LEFT JOIN card_printings p ON p.id=i.printing_id WHERE b.user_id=$1 GROUP BY b.id,codes.code)
          SELECT set_code AS code,COUNT(*)::int AS n,
            CASE WHEN SUM(investment)>0 AND bool_and(currency='EUR' AND assigned>0 AND unpriced=0)
              THEN ((SUM(value)/SUM(investment)-1)*100)::float END AS roi FROM lot_values GROUP BY set_code""", user_id)
        batch_stats = {row["code"].lower(): row for row in stats}
    except Exception:
        logger.warning("Acquisition batch stats unavailable; apply purchase pipeline migration")
    owned_by_set: dict[str, set[str]] = {}
    for row in rows:
        code = (getattr(row, "setCode", None) or "").lower()
        number = (getattr(row, "collectorNumber", None) or "").casefold()
        if code and number and (getattr(row, "quantity", 0) or 0) > 0 and is_base_printing(code, number):
            owned_by_set.setdefault(code, set()).add(number)
    result = []
    for card_set in sets:
        code = getattr(card_set, "code", "") or ""
        fallback_owned = len(owned_by_set.get(code.lower(), set()))
        count, owned = counts_by_set.get(
            code.lower(),
            (BASE_CHECKLIST_LIMITS.get(code.lower(), max(getattr(card_set, "cardCount", 0) or 0, 0)), fallback_owned),
        )
        total_value, owned_value, missing_value = value_by_set.get(code.lower(), (0.0, 0.0, 0.0))
        result.append({
            "valuesAvailable": available_by_set.get(code.lower(), False),
            "batchCount": batch_stats.get(code.lower(), {}).get("n", 0),
            "roi": batch_stats.get(code.lower(), {}).get("roi"),
            "code": code,
            "name": getattr(card_set, "name", "") or code.upper(),
            "setType": getattr(card_set, "setType", "unknown"),
            "cardCount": count,
            "alternativeVersionCount": alternatives_by_set.get(code.lower(), 0),
            "releasedAt": corrected_set_release(code, getattr(card_set, "releasedAt", None)),
            "iconSvgUri": getattr(card_set, "iconSvgUri", None),
            "ownedCount": owned,
            "completionPercentage": round(min(owned, count) * 100 / count) if count else 0,
            "totalValueEur": round(total_value, 2),
            "ownedValueEur": round(owned_value, 2),
            "missingValueEur": round(missing_value, 2),
        })
    return result


@router.get("/sets/{set_code}/value-history", response_model=ExpansionValueHistoryResponse)
async def set_value_history(
    set_code: str,
    days: int = Query(7, ge=1, le=365),
    user_id: str = Depends(get_current_user_id),
):
    code = set_code.lower()
    set_obj = await db.cardset.find_unique(where={"code": code})
    if not set_obj:
        raise HTTPException(status_code=404, detail="Edición no encontrada")
    printings = await db.cardprinting.find_many(
        where={"setId": set_obj.id}, take=10000, include={"catalog": True}
    )
    owned_rows = await db.collectioncard.find_many(where={"userId": user_id})
    owned_by_identity: dict[str, int] = {}
    for row in owned_rows:
        quantity = getattr(row, "quantity", 0) or 0
        name = getattr(row, "cardName", None)
        card_id = getattr(row, "cardScryfallId", None)
        set_code_owned = getattr(row, "setCode", None)
        collector_number = getattr(row, "collectorNumber", None)
        if name:
            key = f"name:{normalize_card_name(name)}"
            owned_by_identity[key] = owned_by_identity.get(key, 0) + quantity
        if card_id:
            key = f"id:{card_id.casefold()}"
            owned_by_identity[key] = owned_by_identity.get(key, 0) + quantity
        if set_code_owned and collector_number:
            key = f"printing:{set_code_owned.casefold()}:{collector_number.casefold()}"
            owned_by_identity[key] = owned_by_identity.get(key, 0) + quantity
    quantities: dict[str, int] = {}
    for printing in printings:
        catalog = getattr(printing, "catalog", None)
        keys = [f"id:{printing.id.casefold()}"]
        keys.append(f"printing:{code}:{(getattr(printing, 'collectorNumber', '') or '').casefold()}")
        quantities[printing.id] = max((owned_by_identity.get(key, 0) for key in keys), default=0)

    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days)).date()
    history_start = datetime.combine(start_date - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc)
    model = getattr(db, "cmpricehistory", None)
    histories = []
    if model is not None and printings:
        histories = await model.find_many(
            where={"scryfallId": {"in": [p.id for p in printings]}, "finish": 0, "date": {"gte": history_start}},
            order={"date": "asc"},
        )
    by_printing: dict[str, list[tuple[Any, float]]] = defaultdict(list)
    for item in histories:
        by_printing[item.scryfallId].append((getattr(item, "date"), float(item.priceCents) / 100))
    points = []
    current_total = current_owned = 0.0
    for printing in printings:
        latest = getattr(printing, "priceCardmarketTrend", None)
        if latest is None:
            latest = getattr(printing, "priceEur", None)
        latest = float(latest or 0)
        current_total += latest if is_base_printing(code, printing.collectorNumber) else 0
        current_owned += latest * quantities.get(printing.id, 0)
    chart_days = [start_date + timedelta(days=offset) for offset in range(days + 1)]
    day_prices: dict[str, list[float]] = {}
    for printing in printings:
        series = by_printing.get(printing.id, [])
        historical_price = None
        position = 0
        fallback = getattr(printing, "priceCardmarketTrend", None)
        if fallback is None:
            fallback = getattr(printing, "priceEur", None)
        prices = []
        for day in chart_days:
            while position < len(series):
                observed, price = series[position]
                observed_date = observed.date() if isinstance(observed, datetime) else observed
                if observed_date > day:
                    break
                historical_price = price
                position += 1
            prices.append(float(historical_price if historical_price is not None else (fallback or 0)))
        if chart_days[-1] == now.date():
            prices[-1] = float(fallback or prices[-1])
        day_prices[printing.id] = prices
    for offset, day in enumerate(chart_days):
        total = owned = 0.0
        for printing in printings:
            historical_price = day_prices[printing.id][offset]
            total += historical_price if is_base_printing(code, printing.collectorNumber) else 0
            owned += historical_price * quantities.get(printing.id, 0)
        points.append({"date": day.isoformat(), "totalValue": round(total, 2), "ownedValue": round(owned, 2)})
    return {
        "setCode": code, "windowDays": days, "currentTotalValue": round(current_total, 2),
        "currentOwnedValue": round(current_owned, 2), "points": points,
    }


@router.get("/sets/{set_code}/cards", response_model=list[CardPrintingResponse])
async def set_cards(set_code: str, user_id: str = Depends(get_current_user_id)):
    code = set_code.lower()
    set_obj = await db.cardset.find_unique(where={"code": code})
    if not set_obj:
        raise HTTPException(status_code=404, detail="Edición no encontrada")

    printings = await db.cardprinting.find_many(
        where={"setId": set_obj.id},
        take=10000,
        order={"collectorNumber": "asc"},
        include={"catalog": True},
    )
    # Inventory and values stay tied to the exact printing. Completion groups
    # catalog identities within this edition; other editions never cover it.
    owned_rows = await db.collectioncard.find_many(where={"userId": user_id})
    rows_by_identity: dict[str, list[Any]] = defaultdict(list)
    for row in owned_rows:
        card_name = getattr(row, "cardName", None)
        card_id = getattr(row, "cardScryfallId", None)
        owned_set = getattr(row, "setCode", None)
        owned_number = getattr(row, "collectorNumber", None)
        if card_name:
            key = f"name:{normalize_card_name(card_name)}"
            rows_by_identity[key].append(row)
        if card_id:
            key = f"id:{card_id.casefold()}"
            rows_by_identity[key].append(row)
        if owned_set and owned_number:
            key = f"printing:{owned_set.casefold()}:{owned_number.casefold()}"
            rows_by_identity[key].append(row)
    acquisition_by_printing: dict[str, tuple[Optional[str], Optional[datetime]]] = {}
    exact_rows_by_printing: dict[str, list[Any]] = {}
    other_rows_by_printing: dict[str, list[Any]] = {}
    for printing in printings:
        catalog = getattr(printing, "catalog", None)
        identities = [
            (0, f"id:{printing.id.casefold()}"),
            (1, f"id:{(getattr(printing, 'catalogId', None) or '').casefold()}"),
            (2, f"printing:{code}:{(getattr(printing, 'collectorNumber', '') or '').casefold()}"),
        ]
        if getattr(catalog, "name", None):
            identities.append((3, f"name:{normalize_card_name(catalog.name)}"))
        candidates: dict[str, tuple[int, Any]] = {}
        for priority, key in identities:
            for row in rows_by_identity.get(key, []):
                row_id = getattr(row, "id", str(id(row)))
                previous = candidates.get(row_id)
                if previous is None or priority < previous[0]:
                    candidates[row_id] = (priority, row)
        ordered_candidates = sorted(
            candidates.values(), key=lambda item: (item[0], -(getattr(item[1], "quantity", 0) or 0))
        )
        collector_number = (getattr(printing, "collectorNumber", "") or "").casefold()
        exact_rows = [
            row for _, row in ordered_candidates
            if (getattr(row, "cardScryfallId", None) or "").casefold() == printing.id.casefold()
            or (
                (getattr(row, "setCode", None) or "").casefold() == code
                and (getattr(row, "collectorNumber", None) or "").casefold() == collector_number
            )
        ]
        exact_rows_by_printing[printing.id] = exact_rows
        exact_ids = {getattr(row, "id", str(id(row))) for row in exact_rows}
        other_rows_by_printing[printing.id] = [
            row for _, row in ordered_candidates
            if getattr(row, "id", str(id(row))) not in exact_ids
        ]
        acquired_row = next((row for row in exact_rows if getattr(row, "acquiredAt", None)), None)
        if acquired_row is None and ordered_candidates:
            acquired_row = exact_rows[0] if exact_rows else None
        acquisition_by_printing[printing.id] = (
            getattr(acquired_row, "id", None), getattr(acquired_row, "acquiredAt", None)
        )
    history_by_printing: dict[str, list[Any]] = defaultdict(list)
    printing_ids = [printing.id for printing in printings]
    now = datetime.now(timezone.utc)
    # Prisma's Python client serializes this @db.Date field through its
    # DateTime input type, so pass midnight UTC instead of datetime.date.
    acquisition_dates = [acquired for _, acquired in acquisition_by_printing.values() if acquired is not None]
    history_start = min(acquisition_dates) if acquisition_dates else now - timedelta(days=60)
    if history_start.tzinfo is None:
        history_start = history_start.replace(tzinfo=timezone.utc)
    history_start = history_start.replace(hour=0, minute=0, second=0, microsecond=0)
    cm_history_model = getattr(db, "cmpricehistory", None)
    cm_histories = []
    if cm_history_model is not None and printing_ids:
        cm_histories = await cm_history_model.find_many(
            where={"scryfallId": {"in": printing_ids}, "finish": 0, "date": {"gte": history_start}},
            order={"date": "asc"},
        )
        for history in cm_histories:
            history_by_printing[history.scryfallId].append(history)
    missing_history_ids = [printing_id for printing_id in printing_ids if not history_by_printing.get(printing_id)]
    if missing_history_ids:
        legacy_history_model = getattr(db, "cardpricehistory", None)
        if legacy_history_model is not None:
            legacy_histories = await legacy_history_model.find_many(
                where={
                    "cardPrintingId": {"in": missing_history_ids},
                    "provider": "cardmarket",
                    "trendPrice": {"not": None},
                    "recordedAt": {"gte": history_start},
                },
                order={"recordedAt": "asc"},
            )
            for history in legacy_histories:
                history_by_printing[history.cardPrintingId].append(history)
    trend_by_printing = {
        printing.id: calculate_price_trend(
            history_by_printing.get(printing.id, []),
            current_price=getattr(printing, "priceCardmarketTrend", None),
            current_at=getattr(printing, "pricesUpdatedAt", None),
            now=now,
            window_days=7,
        )
        for printing in printings
    }
    result = []
    for printing in printings:
        catalog = getattr(printing, "catalog", None)
        catalog_id = getattr(printing, "catalogId", None)
        name = getattr(catalog, "name", None)
        exact_rows = exact_rows_by_printing.get(printing.id, [])
        owned_quantity = sum(getattr(row, "quantity", 0) or 0 for row in exact_rows)
        other_printing_quantities: dict[tuple[str, str], int] = defaultdict(int)
        for row in other_rows_by_printing.get(printing.id, []):
            other_code = (getattr(row, "setCode", None) or "").upper()
            other_number = getattr(row, "collectorNumber", None) or "?"
            other_printing_quantities[(other_code, other_number)] += getattr(row, "quantity", 0) or 0
        other_printings = [
            {"setCode": other_code, "collectorNumber": other_number, "quantity": quantity}
            for (other_code, other_number), quantity in sorted(other_printing_quantities.items())
        ]
        collection_card_id, acquired_at = acquisition_by_printing.get(printing.id, (None, None))
        acquisition_trend = (None, None)
        if acquired_at is not None:
            acquisition_trend = calculate_price_change_since(
                history_by_printing.get(printing.id, []),
                acquired_at=acquired_at,
                current_price=getattr(printing, "priceCardmarketTrend", None) or getattr(printing, "priceEur", None),
                current_at=getattr(printing, "pricesUpdatedAt", None),
                now=now,
            )
        result.append({
            "id": printing.id,
            "catalogId": catalog_id,
            "setCode": code,
            "collectorNumber": getattr(printing, "collectorNumber", ""),
            "isBasePrinting": is_base_printing(code, getattr(printing, "collectorNumber", "")),
            "rarity": getattr(printing, "rarity", None),
            "imageUri": getattr(printing, "imageUri", None),
            "priceEur": getattr(printing, "priceEur", None),
            "priceCardmarketTrend": getattr(printing, "priceCardmarketTrend", None),
            "priceCardmarketMin": getattr(printing, "priceCardmarketMin", None),
            "priceCardmarketMax": getattr(printing, "priceCardmarketMax", None),
            "priceTrendAbsoluteChange": trend_by_printing[printing.id][0],
            "priceTrendPercentageChange": trend_by_printing[printing.id][1],
            "cardName": name,
            "typeLine": getattr(catalog, "typeLine", None),
            "manaCost": getattr(catalog, "manaCost", None),
            "isOwned": owned_quantity > 0,
            "ownedQuantity": owned_quantity,
            "collectionCardId": collection_card_id if owned_quantity else None,
            "acquiredAt": acquired_at,
            "ownedElsewhere": bool(other_printings),
            "otherPrintings": other_printings,
            "acquisitionTrendAbsoluteChange": acquisition_trend[0],
            "acquisitionTrendPercentageChange": acquisition_trend[1],
        })
    return result


@router.patch("/sets/{set_code}/acquisition-dates")
async def set_missing_acquisition_dates(
    set_code: str,
    data: CollectionAcquisitionDateUpdate,
    user_id: str = Depends(get_current_user_id),
):
    acquired_at = datetime.combine(data.acquiredAt, datetime_time.min, tzinfo=timezone.utc)
    count = await db.execute_raw("""
        UPDATE user_collections c
        SET acquired_at = $3::timestamp
        WHERE c.user_id = $1 AND c.acquired_at IS NULL
          AND (
            lower(c.set_code) = lower($2)
            OR lower(c.card_scryfall_id) IN (
              SELECT lower(p.id) FROM card_printings p JOIN card_sets s ON s.id = p.set_id WHERE lower(s.code) = lower($2)
              UNION SELECT lower(p.catalog_id) FROM card_printings p JOIN card_sets s ON s.id = p.set_id
                    WHERE lower(s.code) = lower($2) AND p.catalog_id IS NOT NULL
            )
            OR lower(regexp_replace(btrim(split_part(c.card_name, '/', 1)), '[[:space:]]+', ' ', 'g')) IN (
              SELECT lower(regexp_replace(btrim(split_part(cc.name, '/', 1)), '[[:space:]]+', ' ', 'g'))
              FROM card_printings p JOIN card_sets s ON s.id = p.set_id
              JOIN card_catalog cc ON cc.id = p.catalog_id WHERE lower(s.code) = lower($2)
            )
          )
    """, user_id, set_code.lower(), acquired_at)
    return {"updatedCount": int(count or 0)}


@router.get("/printings/{printing_id}/prices", response_model=list[PriceHistoryPoint])
async def price_history(printing_id: str, provider: PriceProvider = Query("cardmarket")):
    printing = await db.cardprinting.find_unique(where={"id": printing_id})
    if not printing:
        raise HTTPException(status_code=404, detail="Carta no encontrada")
    where = {"cardPrintingId": printing_id}
    where["provider"] = provider
    return await db.cardpricehistory.find_many(where=where, order={"recordedAt": "asc"})


@router.get("/cards/{card_id}/price-history", response_model=CardPriceHistoryResponse)
async def card_price_history(
    card_id: str,
    provider: PriceProvider = Query("cardmarket"),
    days: Optional[int] = Query(30, ge=1, le=3650),
    response: Response = None,
):
    """Price history for every printing of a catalog card (or resolve via printing id)."""
    # 1. Check in-memory cache using printing -> catalog alias or direct card_id
    effective_catalog_id = _printing_to_catalog_map.get(card_id, card_id)
    cached = get_cached_price_history(effective_catalog_id, provider, days)
    if cached:
        if response:
            response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
            response.headers["X-Cache"] = "HIT"
        return cached

    # 2. Resolve catalog and printings from DB
    catalog = await db.cardcatalog.find_unique(where={"id": card_id})
    catalog_id = card_id
    card_name: Optional[str] = catalog.name if catalog else None

    if not catalog:
        printing = await db.cardprinting.find_unique(where={"id": card_id})
        if not printing or not printing.catalogId:
            raise HTTPException(status_code=404, detail="Carta no encontrada")
        catalog_id = printing.catalogId
        _printing_to_catalog_map[card_id] = catalog_id

        # Check cache again with resolved catalog_id
        cached = get_cached_price_history(catalog_id, provider, days)
        if cached:
            if response:
                response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
                response.headers["X-Cache"] = "HIT"
            return cached

        catalog = await db.cardcatalog.find_unique(where={"id": catalog_id})
        card_name = catalog.name if catalog else None

    printings = await db.cardprinting.find_many(
        where={"catalogId": catalog_id},
        include={"set": True},
        order={"releasedAt": "asc"},
    )
    if not printings:
        raise HTTPException(status_code=404, detail="Sin printings para esta carta")

    # Map all printings of this card for fast future lookups
    for p in printings:
        _printing_to_catalog_map[p.id] = catalog_id

    printing_ids = [p.id for p in printings]
    since = datetime.now(timezone.utc) - timedelta(days=days if days is not None else 30)
    cm_model = getattr(db, "cmpricehistory", None)
    cm_histories = []
    if cm_model is not None:
        cm_histories = await cm_model.find_many(
            where={"scryfallId": {"in": printing_ids}, "date": {"gte": since}},
            order={"date": "asc"},
        )
    by_printing: dict[str, dict[str, PriceHistoryPoint]] = {pid: {} for pid in printing_ids}
    for h in cm_histories:
        from datetime import date as _date
        pt_dt = datetime.combine(h.date, datetime.min.time(), tzinfo=timezone.utc) if isinstance(h.date, _date) and not isinstance(h.date, datetime) else (h.date if getattr(h.date, "tzinfo", None) else h.date.replace(tzinfo=timezone.utc))
        date_str = h.date.isoformat() if hasattr(h.date, "isoformat") else str(h.date)[:10]
        # Keep 1 price point per date per printing (prefer finish 0 normal over finish 1 foil)
        if date_str not in by_printing[h.scryfallId] or h.finish == 0:
            by_printing[h.scryfallId][date_str] = PriceHistoryPoint(
                provider="cardmarket",
                currency="EUR",
                trendPrice=round(h.priceCents / 100.0, 2),
                minPrice=round(h.priceCents / 100.0, 2),
                maxPrice=round(h.priceCents / 100.0, 2),
                recordedAt=pt_dt,
            )

    if not cm_histories:
        card_hist_model = getattr(db, "cardpricehistory", None)
        if card_hist_model is not None:
            histories = await card_hist_model.find_many(
                where={"cardPrintingId": {"in": printing_ids}, "provider": "cardmarket", "recordedAt": {"gte": since}},
                order={"recordedAt": "asc"},
            )
            for h in histories:
                date_str = h.recordedAt.strftime("%Y-%m-%d") if hasattr(h.recordedAt, "strftime") else str(h.recordedAt)[:10]
                if date_str not in by_printing[h.cardPrintingId]:
                    by_printing[h.cardPrintingId][date_str] = PriceHistoryPoint(
                        provider=h.provider,
                        currency=h.currency,
                        trendPrice=h.trendPrice,
                        minPrice=h.minPrice,
                        maxPrice=h.maxPrice,
                        recordedAt=h.recordedAt,
                    )

    statistics, _ = await get_history_statistics(db, [(pid, foil, 1) for pid in printing_ids for foil in (False, True)])

    series: list[PrintingPriceSeries] = []
    card_printings_by_set: dict[str, Any] = {}
    for printing in printings:
        set_obj = getattr(printing, "set", None)
        set_code = (getattr(set_obj, "code", None) or "").lower()
        if set_code and set_code not in card_printings_by_set:
            card_printings_by_set[set_code] = printing

        set_name = getattr(set_obj, "name", None) or set_code.upper()
        icon_svg = getattr(set_obj, "iconSvgUri", None)
        rel_at = printing.releasedAt.isoformat() if getattr(printing, "releasedAt", None) else (set_obj.releasedAt.isoformat() if (set_obj and getattr(set_obj, "releasedAt", None)) else None)
        pts = list(by_printing.get(printing.id, {}).values())

        series.append(
            PrintingPriceSeries(
                statistics=statistics.get((printing.id, False)),
                foilStatistics=statistics.get((printing.id, True)),
                printingId=printing.id,
                setCode=set_code,
                collectorNumber=getattr(printing, "collectorNumber", ""),
                setName=set_name,
                releasedAt=rel_at,
                rarity=getattr(printing, "rarity", None),
                iconSvgUri=icon_svg,
                imageUri=getattr(printing, "imageUriSmall", None) or getattr(printing, "imageUri", None),
                points=pts,
            )
        )

    # MTG Expansion Releases like in MTGGoldfish
    expansions_map: dict[str, CardExpansionRelease] = {}
    major_sets = await get_major_expansions()
    since_str = since.strftime("%Y-%m-%d") if days is not None else None

    for s in major_sets:
        s_code = (s.get("code") or "").lower()
        if not s_code:
            continue
        rel_val = s.get("releasedAt")
        rel_str = str(rel_val)[:10] if rel_val else ""
        if since_str and rel_str and rel_str < since_str:
            continue

        iso_rel = rel_val.isoformat() if hasattr(rel_val, "isoformat") else (str(rel_val) if rel_val else None)
        card_p = card_printings_by_set.get(s_code)
        expansions_map[s_code] = CardExpansionRelease(
            setCode=s_code,
            setName=s.get("name") or s_code.upper(),
            releasedAt=iso_rel,
            iconSvgUri=s.get("iconSvgUri"),
            collectorNumber=getattr(card_p, "collectorNumber", None) if card_p else None,
            printingId=getattr(card_p, "id", None) if card_p else None,
            trendPrice=(getattr(card_p, "priceCardmarketTrend", None) or getattr(card_p, "priceEur", None)) if card_p else None,
            hasPrinting=bool(card_p),
        )

    # Also include any printings of this card in special sets (e.g. SLD, MPS, Box topper, promo)
    # or as fallback if major_sets wasn't populated (e.g. in minimal unit test mocks)
    for printing in printings:
        set_obj = getattr(printing, "set", None)
        set_code = (getattr(set_obj, "code", None) or "").lower()
        if set_code and set_code not in expansions_map:
            p_rel = getattr(printing, "releasedAt", None) or (set_obj and getattr(set_obj, "releasedAt", None))
            p_rel_str = str(p_rel)[:10] if p_rel else ""
            if not since_str or not p_rel_str or p_rel_str >= since_str:
                rel_at = printing.releasedAt.isoformat() if getattr(printing, "releasedAt", None) else (set_obj.releasedAt.isoformat() if (set_obj and getattr(set_obj, "releasedAt", None)) else None)
                expansions_map[set_code] = CardExpansionRelease(
                    setCode=set_code,
                    setName=getattr(set_obj, "name", None) or set_code.upper(),
                    releasedAt=rel_at,
                    iconSvgUri=getattr(set_obj, "iconSvgUri", None),
                    collectorNumber=getattr(printing, "collectorNumber", ""),
                    printingId=printing.id,
                    trendPrice=getattr(printing, "priceCardmarketTrend", None) or getattr(printing, "priceEur", None),
                    hasPrinting=True,
                )

    expansions = sorted(
        expansions_map.values(),
        key=lambda e: e.releasedAt or "",
    )

    prov = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
    result = CardPriceHistoryResponse(
        catalogId=catalog_id,
        cardName=card_name,
        provider=provider,
        currency=prov["currency"],
        days=days,
        series=series,
        expansions=expansions,
    )

    set_cached_price_history(catalog_id, provider, days, result)
    if response:
        response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
        response.headers["X-Cache"] = "MISS"

    return result
