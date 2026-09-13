from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.core.db import db
from src.schemas.pricing import CardPrintingResponse, CardSetResponse, PriceHistoryPoint, PriceProvider

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/sets", response_model=list[CardSetResponse])
async def list_sets(limit: int = Query(100, ge=1, le=500)):
    return await db.cardset.find_many(take=limit, order={"releasedAt": "desc"})


@router.get("/sets/{set_code}/cards", response_model=list[CardPrintingResponse])
async def list_set_cards(set_code: str, limit: int = Query(250, ge=1, le=500)):
    card_set = await db.cardset.find_unique(where={"code": set_code.lower()})
    if not card_set:
        raise HTTPException(status_code=404, detail="Expansión no encontrada")
    printings = await db.cardprinting.find_many(
        where={"setId": card_set.id}, take=limit, order={"collectorNumber": "asc"}
    )
    return [
        {
            "id": printing.id,
            "catalogId": printing.catalogId,
            "setCode": card_set.code,
            "collectorNumber": printing.collectorNumber,
            "rarity": printing.rarity,
            "imageUri": printing.imageUri,
            "imageUriSmall": printing.imageUriSmall,
            "imageUriLarge": printing.imageUriLarge,
            "priceCardmarketTrend": printing.priceCardmarketTrend,
            "priceCardmarketMin": printing.priceCardmarketMin,
            "priceCardmarketMax": printing.priceCardmarketMax,
            "priceCardtraderTrend": printing.priceCardtraderTrend,
            "priceCardtraderMin": printing.priceCardtraderMin,
            "priceCardtraderMax": printing.priceCardtraderMax,
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
