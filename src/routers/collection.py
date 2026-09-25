from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from src.core.auth import get_current_user_id
from src.schemas.collection import (
    CollectionCardCreate, CollectionCardUpdate, CollectionCardUpdateVersion,
    CollectionCardResponse, CollectionStats, CollectionQueryResponse,
    DormantCardsResponse,
)
from src.services.collection_service import CollectionService
from src.services.collection_view_service import CollectionViewService
from src.schemas.collection import CollectionViewResponse
from src.schemas.pricing import PriceProvider

router = APIRouter(prefix="/collection", tags=["collection"])

@router.get("", response_model=List[CollectionCardResponse])
async def get_collection(
    query: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user_id)
):
    return await CollectionService.get_user_collection(user_id, query, limit, offset)

@router.get("/view", response_model=CollectionViewResponse)
async def get_collection_view(
    provider: PriceProvider = "cardmarket",
    user_id: str = Depends(get_current_user_id),
):
    return await CollectionViewService.get_view(user_id, provider)

@router.get("/query", response_model=CollectionQueryResponse)
async def get_collection_query(
    query: Optional[str] = Query(None),
    sort: str = Query("name", pattern="^(name|cmc|type|quantity|price_trend|price_subtotal|requested_decks)$"),
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    grouped: bool = Query(True),
    priceProvider: str = Query("cardmarket"),
    page: int = Query(1, ge=1),
    limit: Optional[int] = Query(None, ge=1, le=500),
    user_id: str = Depends(get_current_user_id)
):
    """
    Returns the collection filtered, sorted and optionally grouped into MTG type
    sections on the backend. Flat lists accept page/limit slicing.
    """
    return await CollectionService.get_user_collection_query(
        user_id,
        query=query,
        sort=sort,
        direction=direction,
        grouped=grouped,
        price_provider=priceProvider,
        page=page,
        limit=limit,
    )

@router.post("/add-or-increment", response_model=CollectionCardResponse)
async def add_or_increment(data: CollectionCardCreate, user_id: str = Depends(get_current_user_id)):
    return await CollectionService.add_or_increment_card(user_id, data)

@router.patch("/{card_id}", response_model=Optional[CollectionCardResponse])
async def update_collection_quantity(
    card_id: str,
    data: CollectionCardUpdate,
    user_id: str = Depends(get_current_user_id)
):
    res = await CollectionService.update_quantity(
        user_id,
        card_id,
        data.quantity,
        data.setCode,
        set_code_provided="setCode" in data.model_fields_set,
    )
    return res


@router.patch("/{card_id}/version", response_model=CollectionCardResponse)
async def update_collection_card_version(
    card_id: str,
    data: CollectionCardUpdateVersion,
    user_id: str = Depends(get_current_user_id),
):
    res = await CollectionService.update_card_version(
        user_id=user_id,
        card_id=card_id,
        card_scryfall_id=data.cardScryfallId,
        image_uri=data.imageUri,
        set_code=data.setCode,
        collector_number=data.collectorNumber,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Carta no encontrada en la colección")
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


@router.get("/dormant", response_model=DormantCardsResponse)
async def get_dormant_cards(
    minPrice: float = Query(0.0, ge=0.0),
    provider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    """Detects dormant cards owned in collection not used in any deck nor recommended by active EDHREC commanders."""
    return await CollectionService.get_dormant_cards(
        user_id=user_id,
        min_price=minPrice,
        provider=provider,
    )

