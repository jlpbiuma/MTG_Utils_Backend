from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from src.core.auth import get_current_user_id
from src.schemas.wants import (
    WantCardCreate,
    WantCardUpdate,
    WantCardResponse,
    WantStats,
    WantQueryResponse,
)
from src.services.want_service import WantAlreadyOwnedError, WantService

router = APIRouter(prefix="/wants", tags=["wants"])


@router.get("", response_model=List[WantCardResponse])
async def get_wants(
    query: Optional[str] = Query(None),
    user_id: str = Depends(get_current_user_id),
):
    return await WantService.get_user_wants(user_id, query)


@router.get("/query", response_model=WantQueryResponse)
async def get_wants_query(
    query: Optional[str] = Query(None),
    sort: str = Query("name", pattern="^(name|cmc|type|quantity|price_trend|price_subtotal|requested_decks)$"),
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    grouped: bool = Query(True),
    priceProvider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    return await WantService.get_user_wants_query(
        user_id,
        query=query,
        sort=sort,
        direction=direction,
        grouped=grouped,
        price_provider=priceProvider,
    )


@router.post("/add-or-increment", response_model=WantCardResponse)
async def add_or_increment(data: WantCardCreate, user_id: str = Depends(get_current_user_id)):
    try:
        return await WantService.add_or_increment(user_id, data)
    except WantAlreadyOwnedError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Ya tienes «{exc.card_name}» en la colección",
        )


@router.patch("/{card_id}", response_model=Optional[WantCardResponse])
async def update_want_quantity(
    card_id: str,
    data: WantCardUpdate,
    user_id: str = Depends(get_current_user_id),
):
    return await WantService.update_quantity(
        user_id,
        card_id,
        data.quantity,
        data.setCode,
        set_code_provided="setCode" in data.model_fields_set,
    )


@router.delete("/{card_id}")
async def delete_want_card(card_id: str, user_id: str = Depends(get_current_user_id)):
    ok = await WantService.delete_card(user_id, card_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Carta no encontrada en wants")
    return {"status": "success"}


@router.get("/stats", response_model=WantStats)
async def get_stats(user_id: str = Depends(get_current_user_id)):
    return await WantService.get_stats(user_id)


@router.post("/add-deck-missing/{deck_id}")
async def add_deck_missing_to_wants(
    deck_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Adds all missing cards from the specified deck into user wants."""
    res = await WantService.add_deck_missing_to_wants(user_id, deck_id)
    if res.get("status") == "error":
        raise HTTPException(status_code=404, detail=res.get("message", "Error al procesar el mazo"))
    return res

