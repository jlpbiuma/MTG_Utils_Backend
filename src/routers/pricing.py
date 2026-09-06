from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from src.core.auth import get_current_user_id
from src.core.db import db
from src.schemas.pricing import PricingRequest, PriceSummary
from src.services.pricing_service import PricingService

router = APIRouter(prefix="/pricing", tags=["pricing"])

@router.post("", response_model=PriceSummary)
async def get_prices(data: PricingRequest, user_id: str = Depends(get_current_user_id)):
    cards_to_price: List[Dict[str, Any]] = []

    if data.deckId:
        deck = await db.deck.find_unique(where={"id": data.deckId}, include={"cards": True})
        if not deck:
            raise HTTPException(status_code=404, detail="Mazo no encontrado")

        for c in (deck.cards or []):
            cards_to_price.append({
                "name": c.cardName,
                "scryfallId": c.cardScryfallId,
                "quantity": c.quantity,
                "isMissing": (c.assignedQuantity < c.quantity),
            })
    elif data.includeCollection:
        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        for c in col_cards:
            cards_to_price.append({
                "name": c.cardName,
                "scryfallId": c.cardScryfallId,
                "quantity": c.quantity,
                "isMissing": False,
            })

    return await PricingService.get_price_summary(
        cards=cards_to_price,
        provider=data.provider,
        bypass_cache=data.forceRefresh
    )
