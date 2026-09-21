"""Compute top price gainers / losers over a rolling window."""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.db import db
from src.schemas.pricing import (
    PriceMoverItem,
    PriceMoversResponse,
    PriceProvider,
    MoversScope,
)
from src.services.pricing_service import PRICE_PROVIDERS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _current_printing_trend(printing: Any, provider: str = "cardmarket") -> Optional[Tuple[float, Optional[datetime]]]:
    trend = getattr(printing, "priceCardmarketTrend", None)
    if trend is None:
        trend = getattr(printing, "priceEur", None)
    updated = getattr(printing, "pricesUpdatedAt", None)
    if trend is None:
        return None
    return float(trend), updated


def compute_mover_for_points(
    points: Sequence[Any],
    *,
    window_start: datetime,
    printing: Any,
    provider: str,
    currency: str,
    symbol: str,
) -> Optional[PriceMoverItem]:
    """Derive a mover from ordered history points (asc) plus optional printing quote."""
    usable = [p for p in points if getattr(p, "trendPrice", None) is not None and p.trendPrice > 0]
    if not usable:
        return None

    window_start = _as_aware(window_start)
    before = [p for p in usable if _as_aware(p.recordedAt) <= window_start]
    within = [p for p in usable if _as_aware(p.recordedAt) > window_start]

    if before:
        baseline_pt = before[-1]
    elif within:
        baseline_pt = within[0]
    else:
        return None

    latest_hist = usable[-1]
    current_price = float(latest_hist.trendPrice)
    current_at = _as_aware(latest_hist.recordedAt)

    printing_quote = _current_printing_trend(printing, provider) if printing is not None else None
    if printing_quote is not None:
        p_trend, p_updated = printing_quote
        p_at = _as_aware(p_updated) if p_updated is not None else current_at
        if p_at >= current_at:
            current_price = p_trend
            current_at = p_at

    baseline_price = float(baseline_pt.trendPrice)
    baseline_at = _as_aware(baseline_pt.recordedAt)

    if baseline_price <= 0:
        return None
    if current_at <= baseline_at and abs(current_price - baseline_price) < 1e-9:
        return None

    change_abs = round(current_price - baseline_price, 4)
    change_pct = round((change_abs / baseline_price) * 100.0, 4)
    if abs(change_pct) < 1e-6:
        return None

    catalog = getattr(printing, "catalog", None) if printing is not None else None
    card_set = getattr(printing, "set", None) if printing is not None else None
    card_name = getattr(catalog, "name", None) or getattr(printing, "cardName", None) or "Unknown"
    set_code = getattr(card_set, "code", None) or getattr(printing, "setCode", None)
    collector = getattr(printing, "collectorNumber", None) if printing is not None else None
    image = None
    if printing is not None:
        image = printing.imageUriSmall or printing.imageUri or printing.imageUriLarge

    return PriceMoverItem(
        printingId=printing.id if printing is not None else usable[0].cardPrintingId,
        catalogId=getattr(printing, "catalogId", None) if printing is not None else None,
        cardName=card_name,
        setCode=set_code,
        collectorNumber=collector,
        imageUri=image,
        provider=provider,  # type: ignore[arg-type]
        currency=currency,
        currencySymbol=symbol,
        currentPrice=round(current_price, 4),
        baselinePrice=round(baseline_price, 4),
        changeAbs=change_abs,
        changePct=change_pct,
        baselineAt=baseline_at,
        currentAt=current_at,
    )


class PriceMoversService:
    @staticmethod
    async def get_movers(
        *,
        provider: PriceProvider = "cardmarket",
        window_days: int = 30,
        limit: int = 20,
        scope: MoversScope = "global",
        user_id: Optional[str] = None,
    ) -> PriceMoversResponse:
        prov = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        currency = prov["currency"]
        symbol = prov["symbol"]
        now = _utcnow()
        window_start = now - timedelta(days=window_days)
        lookback_start = window_start - timedelta(days=window_days)

        printing_filter: Optional[List[str]] = None
        if scope == "collection":
            if not user_id:
                return PriceMoversResponse(
                    provider=provider,
                    currency=currency,
                    currencySymbol=symbol,
                    windowDays=window_days,
                    scope=scope,
                    gainers=[],
                    losers=[],
                    generatedAt=now,
                )
            col_cards = await db.collectioncard.find_many(where={"userId": user_id})
            printing_filter = list({c.cardScryfallId for c in col_cards if c.cardScryfallId})
            if not printing_filter:
                return PriceMoversResponse(
                    provider=provider,
                    currency=currency,
                    currencySymbol=symbol,
                    windowDays=window_days,
                    scope=scope,
                    gainers=[],
                    losers=[],
                    generatedAt=now,
                )
        elif scope == "wants":
            if not user_id:
                return PriceMoversResponse(
                    provider=provider,
                    currency=currency,
                    currencySymbol=symbol,
                    windowDays=window_days,
                    scope=scope,
                    gainers=[],
                    losers=[],
                    generatedAt=now,
                )
            want_cards = await db.wantcard.find_many(where={"userId": user_id})
            printing_filter = list({c.cardScryfallId for c in want_cards if c.cardScryfallId})
            if not printing_filter:
                return PriceMoversResponse(
                    provider=provider,
                    currency=currency,
                    currencySymbol=symbol,
                    windowDays=window_days,
                    scope=scope,
                    gainers=[],
                    losers=[],
                    generatedAt=now,
                )

        # Try cm_price_history first
        cm_model = getattr(db, "cmpricehistory", None)
        cm_histories = []
        if cm_model is not None:
            if printing_filter is not None:
                cm_where: Dict[str, Any] = {
                    "date": {"gte": lookback_start},
                    "scryfallId": {"in": printing_filter},
                }
                cm_histories = await cm_model.find_many(
                    where=cm_where,
                    order={"date": "asc"},
                )
            elif getattr(db, "query_raw", None) is not None:
                # Global scope: use efficient SQL directly in PostgreSQL to prevent loading 3.5+ million rows into Python RAM
                w_date = window_start.date() if isinstance(window_start, datetime) else window_start
                lb_date = lookback_start.date() if isinstance(lookback_start, datetime) else lookback_start
                sql = """
                WITH baseline AS (
                    SELECT DISTINCT ON (scryfall_id, finish) scryfall_id, finish, price_cents, date
                    FROM cm_price_history
                    WHERE date >= $1 AND date <= $2
                    ORDER BY scryfall_id, finish, date DESC
                ),
                latest AS (
                    SELECT DISTINCT ON (scryfall_id, finish) scryfall_id, finish, price_cents, date
                    FROM cm_price_history
                    WHERE date > $2
                    ORDER BY scryfall_id, finish, date DESC
                ),
                diffs AS (
                    SELECT l.scryfall_id, b.price_cents AS base_cents, l.price_cents AS curr_cents,
                           b.date AS base_date, l.date AS curr_date,
                           round(((l.price_cents - b.price_cents)::numeric / b.price_cents::numeric) * 100, 2) AS pct_change,
                           round((l.price_cents - b.price_cents)::numeric / 100.0, 2) AS abs_change
                    FROM latest l JOIN baseline b ON l.scryfall_id = b.scryfall_id AND l.finish = b.finish
                    WHERE b.price_cents > 50 AND l.price_cents != b.price_cents
                )
                (SELECT * FROM diffs WHERE pct_change > 0 ORDER BY pct_change DESC LIMIT $3)
                UNION ALL
                (SELECT * FROM diffs WHERE pct_change < 0 ORDER BY pct_change ASC LIMIT $3)
                """
                try:
                    raw_rows = await db.query_raw(sql, lb_date, w_date, limit)
                except Exception:
                    raw_rows = []

                if raw_rows:
                    top_ids = [r["scryfall_id"] if isinstance(r, dict) else r[0] for r in raw_rows]
                    printings = await db.cardprinting.find_many(
                        where={"id": {"in": top_ids}},
                        include={"catalog": True, "set": True},
                    )
                    printing_map = {p.id: p for p in printings}
                    gainers: List[PriceMoverItem] = []
                    losers: List[PriceMoverItem] = []
                    for r in raw_rows:
                        sid = r["scryfall_id"] if isinstance(r, dict) else r[0]
                        base_c = int(r["base_cents"] if isinstance(r, dict) else r[1])
                        curr_c = int(r["curr_cents"] if isinstance(r, dict) else r[2])
                        base_d = r["base_date"] if isinstance(r, dict) else r[3]
                        curr_d = r["curr_date"] if isinstance(r, dict) else r[4]
                        pct = float(r["pct_change"] if isinstance(r, dict) else r[5])
                        diff_abs = float(r["abs_change"] if isinstance(r, dict) else r[6])
                        p = printing_map.get(sid)
                        cat = getattr(p, "catalog", None)
                        c_set = getattr(p, "set", None)
                        base_at = datetime.combine(base_d, datetime.min.time(), tzinfo=timezone.utc) if isinstance(base_d, _date) and not isinstance(base_d, datetime) else base_d
                        curr_at = datetime.combine(curr_d, datetime.min.time(), tzinfo=timezone.utc) if isinstance(curr_d, _date) and not isinstance(curr_d, datetime) else curr_d
                        item = PriceMoverItem(
                            printingId=sid,
                            catalogId=getattr(p, "catalogId", None) if p else None,
                            cardName=getattr(cat, "name", None) or "Unknown",
                            setCode=getattr(c_set, "code", None) or "",
                            collectorNumber=getattr(p, "collectorNumber", None) if p else None,
                            imageUri=getattr(p, "imageUriSmall", None) or getattr(p, "imageUri", None) if p else None,
                            provider=provider,
                            currency=currency,
                            currencySymbol=symbol,
                            currentPrice=round(curr_c / 100.0, 2),
                            baselinePrice=round(base_c / 100.0, 2),
                            changeAbs=round(diff_abs, 2),
                            changePct=round(pct, 2),
                            baselineAt=base_at,
                            currentAt=curr_at,
                        )
                        if pct > 0:
                            gainers.append(item)
                        elif pct < 0:
                            losers.append(item)
                    return PriceMoversResponse(
                        provider=provider,
                        currency=currency,
                        currencySymbol=symbol,
                        windowDays=window_days,
                        scope=scope,
                        gainers=gainers,
                        losers=losers,
                        generatedAt=now,
                    )

        by_printing: Dict[str, List[Any]] = defaultdict(list)
        for h in cm_histories:
            pt_dt = datetime.combine(h.date, datetime.min.time(), tzinfo=timezone.utc) if isinstance(h.date, _date) and not isinstance(h.date, datetime) else (h.date if getattr(h.date, "tzinfo", None) else h.date.replace(tzinfo=timezone.utc))
            from types import SimpleNamespace
            by_printing[h.scryfallId].append(
                SimpleNamespace(
                    trendPrice=round(h.priceCents / 100.0, 2),
                    recordedAt=pt_dt,
                )
            )

        if not by_printing:
            where: Dict[str, Any] = {
                "provider": "cardmarket",
                "trendPrice": {"not": None},
                "recordedAt": {"gte": lookback_start},
            }
            if printing_filter is not None:
                where["cardPrintingId"] = {"in": printing_filter}

            histories = await db.cardpricehistory.find_many(
                where=where,
                order={"recordedAt": "asc"},
            )
            for h in histories:
                by_printing[h.cardPrintingId].append(h)

        if not by_printing:
            return PriceMoversResponse(
                provider=provider,
                currency=currency,
                currencySymbol=symbol,
                windowDays=window_days,
                scope=scope,
                gainers=[],
                losers=[],
                generatedAt=now,
            )

        printing_ids = list(by_printing.keys())
        printings = await db.cardprinting.find_many(
            where={"id": {"in": printing_ids}},
            include={"catalog": True, "set": True},
        )
        printing_map = {p.id: p for p in printings}

        movers: List[PriceMoverItem] = []
        for pid, points in by_printing.items():
            item = compute_mover_for_points(
                points,
                window_start=window_start,
                printing=printing_map.get(pid),
                provider=provider,
                currency=currency,
                symbol=symbol,
            )
            if item is not None:
                movers.append(item)

        gainers = sorted((m for m in movers if m.changePct > 0), key=lambda m: m.changePct, reverse=True)[:limit]
        losers = sorted((m for m in movers if m.changePct < 0), key=lambda m: m.changePct)[:limit]

        return PriceMoversResponse(
            provider=provider,
            currency=currency,
            currencySymbol=symbol,
            windowDays=window_days,
            scope=scope,
            gainers=gainers,
            losers=losers,
            generatedAt=now,
        )
