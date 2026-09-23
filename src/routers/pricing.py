from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Dict, Any
from src.core.auth import get_current_user_id
from src.core.db import db
from src.services.card_utils import normalize_card_name, is_basic_land
from src.schemas.pricing import (
    PricingRequest,
    PricingCardsRequest,
    PriceSummary,
    PriceProvider,
    PriceMoversResponse,
    MoversScope,
    CollectionValueHistoryResponse,
)
from src.services.pricing_service import PricingService
from src.services.price_movers_service import PriceMoversService

router = APIRouter(prefix="/pricing", tags=["pricing"])

@router.post("", response_model=PriceSummary)
async def get_prices(data: PricingRequest, user_id: str = Depends(get_current_user_id)):
    cards_to_price: List[Dict[str, Any]] = []

    if data.deckId:
        deck = await db.deck.find_unique(where={"id": data.deckId}, include={"cards": True})
        if not deck or deck.userId != user_id:
            raise HTTPException(status_code=404, detail="Mazo no encontrado")

        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map = {normalize_card_name(c.cardName): c.quantity for c in col_cards}

        cards = list(deck.cards or [])
        has_cmd = any(
            c.isCommander or (deck.commander and normalize_card_name(c.cardName) == normalize_card_name(deck.commander))
            for c in cards
        )
        if deck.commander and not has_cmd:
            norm_cmd = normalize_card_name(deck.commander)
            in_col = col_map.get(norm_cmd, 0)
            actual_owned = min(1, in_col)
            missing_qty = max(0, 1 - actual_owned)
            cards_to_price.append({
                "name": deck.commander,
                "scryfallId": deck.commanderScryfallId,
                "quantity": 1,
                "ownedQuantity": actual_owned,
                "missingQuantity": missing_qty,
                "isMissing": (missing_qty > 0),
            })

        for c in cards:
            norm = normalize_card_name(c.cardName)
            in_col = col_map.get(norm, 0)
            assigned = c.assignedQuantity or 0
            actual_owned = min(c.quantity, max(assigned, min(in_col, c.quantity)))

            if is_basic_land(c.typeLine, c.cardName):
                # Basic lands never count toward completion: they never
                # contribute to the missing cost, but still count as owned.
                owned_qty, missing_qty = c.quantity, 0
            else:
                owned_qty = actual_owned
                missing_qty = max(0, c.quantity - actual_owned)

            cards_to_price.append({
                "name": c.cardName,
                "scryfallId": c.cardScryfallId,
                "quantity": c.quantity,
                "ownedQuantity": owned_qty,
                "missingQuantity": missing_qty,
                "isMissing": (missing_qty > 0),
            })
    elif data.includeCollection:
        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        for c in col_cards:
            cards_to_price.append({
                "name": c.cardName,
                "scryfallId": c.cardScryfallId,
                "quantity": c.quantity,
                "ownedQuantity": c.quantity,
                "missingQuantity": 0,
                "isMissing": False,
            })

    return await PricingService.get_price_summary(
        cards=cards_to_price,
        provider=data.provider,
        bypass_cache=data.forceRefresh
    )

@router.post("/cards", response_model=PriceSummary)
async def get_card_prices(data: PricingCardsRequest):
    """Returns prices for an explicit card set using one selected provider."""
    return await PricingService.get_price_summary(
        cards=[card.model_dump() for card in data.cards],
        provider=data.provider,
        bypass_cache=data.forceRefresh,
    )

@router.post("/decks/{deck_id}", response_model=PriceSummary)
async def get_deck_prices(deck_id: str, provider: PriceProvider = "cardmarket", forceRefresh: bool = False,
                          user_id: str = Depends(get_current_user_id)):
    """Convenience endpoint for one deck; provider is always explicit/defaulted."""
    return await get_prices(PricingRequest(deckId=deck_id, provider=provider, forceRefresh=forceRefresh), user_id)


@router.get("/movers", response_model=PriceMoversResponse)
async def get_price_movers(
    provider: PriceProvider = Query("cardmarket"),
    windowDays: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    scope: MoversScope = Query("global"),
    user_id: str = Depends(get_current_user_id),
):
    """Top price gainers and losers over a rolling window (default 30 days)."""
    return await PriceMoversService.get_movers(
        provider=provider,
        window_days=windowDays,
        limit=limit,
        scope=scope,
        user_id=user_id,
    )


@router.get("/collection/history", response_model=CollectionValueHistoryResponse)
async def get_collection_value_history(
    provider: PriceProvider = Query("cardmarket"),
    days: int = Query(30, ge=1, le=3650),
    user_id: str = Depends(get_current_user_id),
):
    """Historical collection valuation over a specified time window."""
    return await PricingService.get_collection_value_history(
        user_id=user_id,
        provider=provider,
        days=days,
    )
