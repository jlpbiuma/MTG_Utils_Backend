from src.schemas.edhrec import RecommendedDeckResponse
from src.services.edhrec_deck_service import get_recommended_deck as build_recommended_deck
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from src.core.auth import get_current_user_id
from src.schemas.edhrec import DeckRecommendationsResponse, CommanderRecommendationsListResponse
from src.services.edhrec_service import EdhrecService

router = APIRouter(prefix="/edhrec", tags=["edhrec"])

@router.get("/recommendations")
async def get_recommendations(
    deckId: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    colors: Optional[str] = Query(None),
    top100_only: bool = Query(False),
    owned_commander_only: bool = Query(False),
    sort_by: str = Query("completion"),
    page: int = Query(1),
    page_size: int = Query(24),
    user_id: str = Depends(get_current_user_id),
):
    if deckId:
        return await EdhrecService.get_deck_recommendations(deckId, user_id)
    return await EdhrecService.get_commander_recommendations_for_user(
        user_id=user_id,
        search=search,
        colors=colors,
        top100_only=top100_only,
        owned_commander_only=owned_commander_only,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )

@router.get("/commanders", response_model=CommanderRecommendationsListResponse)
async def get_commander_recommendations(
    search: Optional[str] = Query(None),
    colors: Optional[str] = Query(None),
    top100_only: bool = Query(False),
    owned_commander_only: bool = Query(False),
    sort_by: str = Query("completion"),
    page: int = Query(1),
    page_size: int = Query(24),
    user_id: str = Depends(get_current_user_id),
):
    return await EdhrecService.get_commander_recommendations_for_user(
        user_id=user_id,
        search=search,
        colors=colors,
        top100_only=top100_only,
        owned_commander_only=owned_commander_only,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )



@router.get("/commanders/{slug}/deck", response_model=RecommendedDeckResponse)
async def get_recommended_deck(slug: str, user_id: str = Depends(get_current_user_id)):
    deck = await build_recommended_deck(slug, user_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Recomendación no disponible")
    return deck
