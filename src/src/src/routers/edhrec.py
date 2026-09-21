from fastapi import APIRouter, Depends, Query
from src.core.auth import get_current_user_id
from src.schemas.edhrec import DeckRecommendationsResponse
from src.services.edhrec_service import EdhrecService

router = APIRouter(prefix="/edhrec", tags=["edhrec"])

@router.get("/recommendations", response_model=DeckRecommendationsResponse)
async def get_recommendations(
    deckId: str = Query(...),
    user_id: str = Depends(get_current_user_id)
):
    return await EdhrecService.get_deck_recommendations(deckId, user_id)

