from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.core.db import db
from src.schemas.pricing import (
    CardPrintingResponse,
    CardSetResponse,
    PriceHistoryPoint,
    PriceProvider,
    CardPriceHistoryResponse,
    PrintingPriceSeries,
    CardExpansionRelease,
)
from src.services.pricing_service import PRICE_PROVIDERS

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/sets", response_model=list[CardSetResponse])
async def list_sets(limit: int = Query(100, ge=1, le=500)):
    return await db.cardset.find_many(take=limit, order={"releasedAt": "desc"})


@router.get("/sets/{set_code}/cards", response_model=list[CardPrintingResponse])
async def set_cards(set_code: str):
    set_obj = await db.cardset.find_unique(where={"code": set_code.lower()})
    if not set_obj:
        raise HTTPException(status_code=404, detail="Edición no encontrada")

    printings = await db.cardprinting.find_many(
        where={"setId": set_obj.id},
        take=250,
        order={"collectorNumber": "asc"},
    )
    return [
        {
            "id": printing.id,
            "catalogId": getattr(printing, "catalogId", None),
            "setCode": set_code,
            "collectorNumber": getattr(printing, "collectorNumber", ""),
            "rarity": getattr(printing, "rarity", None),
            "imageUri": getattr(printing, "imageUri", None),
            "priceEur": getattr(printing, "priceEur", None),
            "priceCardmarketTrend": getattr(printing, "priceCardmarketTrend", None),
            "priceCardmarketMin": getattr(printing, "priceCardmarketMin", None),
            "priceCardmarketMax": getattr(printing, "priceCardmarketMax", None),
        }
        for printing in printings
    ]


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
):
    """Price history for every printing of a catalog card (or resolve via printing id)."""
    catalog = await db.cardcatalog.find_unique(where={"id": card_id})
    catalog_id = card_id
    card_name: Optional[str] = catalog.name if catalog else None

    if not catalog:
        printing = await db.cardprinting.find_unique(where={"id": card_id})
        if not printing or not printing.catalogId:
            raise HTTPException(status_code=404, detail="Carta no encontrada")
        catalog_id = printing.catalogId
        catalog = await db.cardcatalog.find_unique(where={"id": catalog_id})
        card_name = catalog.name if catalog else None

    printings = await db.cardprinting.find_many(
        where={"catalogId": catalog_id},
        include={"set": True},
        order={"releasedAt": "asc"},
    )
    if not printings:
        raise HTTPException(status_code=404, detail="Sin printings para esta carta")

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

    series: list[PrintingPriceSeries] = []
    expansions_map: dict[str, CardExpansionRelease] = {}
    for printing in printings:
        set_obj = getattr(printing, "set", None)
        set_code = getattr(set_obj, "code", None) or ""
        set_name = getattr(set_obj, "name", None) or set_code.upper()
        icon_svg = getattr(set_obj, "iconSvgUri", None)
        rel_at = printing.releasedAt.isoformat() if getattr(printing, "releasedAt", None) else (set_obj.releasedAt.isoformat() if (set_obj and getattr(set_obj, "releasedAt", None)) else None)
        pts = list(by_printing.get(printing.id, {}).values())

        series.append(
            PrintingPriceSeries(
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

        if set_code and set_code not in expansions_map:
            expansions_map[set_code] = CardExpansionRelease(
                setCode=set_code,
                setName=set_name,
                releasedAt=rel_at,
                iconSvgUri=icon_svg,
                collectorNumber=getattr(printing, "collectorNumber", ""),
                printingId=printing.id,
                trendPrice=getattr(printing, "priceCardmarketTrend", None) or getattr(printing, "priceEur", None),
            )

    expansions = sorted(
        expansions_map.values(),
        key=lambda e: e.releasedAt or "",
    )

    prov = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
    return CardPriceHistoryResponse(
        catalogId=catalog_id,
        cardName=card_name,
        provider=provider,
        currency=prov["currency"],
        days=days,
        series=series,
        expansions=expansions,
    )
