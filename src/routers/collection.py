from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from src.core.auth import get_current_user_id
from src.schemas.collection import (
    CollectionCardCreate, CollectionCardUpdate,
    CollectionCardResponse, CollectionStats
)
from src.services.collection_service import CollectionService

router = APIRouter(prefix="/collection", tags=["collection"])

@router.get("", response_model=List[CollectionCardResponse])
async def get_collection(
    query: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user_id)
):
    return await CollectionService.get_user_collection(user_id, query, limit, offset)

@router.post("/add-or-increment", response_model=CollectionCardResponse)
async def add_or_increment(data: CollectionCardCreate, user_id: str = Depends(get_current_user_id)):
    return await CollectionService.add_or_increment_card(user_id, data)

@router.patch("/{card_id}", response_model=Optional[CollectionCardResponse])
async def update_collection_quantity(
    card_id: str,
    data: CollectionCardUpdate,
    user_id: str = Depends(get_current_user_id)
):
    res = await CollectionService.update_quantity(user_id, card_id, data.quantity)
    return res

@router.delete("/{card_id}")
async def delete_collection_card(card_id: str, user_id: str = Depends(get_current_user_id)):
    ok = await CollectionService.delete_card(user_id, card_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Carta no encontrada en la colección")
    return {"status": "success"}

@router.get("/stats", response_model=CollectionStats)
async def get_stats(user_id: str = Depends(get_current_user_id)):
    return await CollectionService.get_stats(user_id)
