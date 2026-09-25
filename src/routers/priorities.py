from fastapi import APIRouter, Depends, Query
from typing import Optional
from src.core.auth import get_current_user_id
from src.schemas.priorities import PrioritiesResponse, GoldenWantsResponse
from src.services.priority_service import PriorityService

router = APIRouter(prefix="/priorities", tags=["priorities"])

@router.get("", response_model=PrioritiesResponse)
async def get_priorities(
    sort: str = Query("demand", pattern="^(demand|impact|completion|complete_decks|price_opportunity)$"),
    includePriceSignals: bool = Query(False),
    priceWindowDays: int = Query(30, ge=1, le=365),
    reassignableOnly: bool = Query(False),
    hideOwned: bool = Query(False),
    cardType: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=200),
    provider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    """Calculates prioritized card acquisitions and reassignments across all user decks."""
    return await PriorityService.get_priorities(
        user_id=user_id,
        sort=sort,
        include_price_signals=includePriceSignals,
        price_window_days=priceWindowDays,
        reassignable_only=reassignableOnly,
        hide_owned=hideOwned,
        card_type=cardType,
        page=page,
        limit=limit,
        provider=provider,
    )

@router.get("/golden-wants", response_model=GoldenWantsResponse)
async def get_golden_wants(
    budget: float = Query(50.0, ge=0.0),
    strategy: str = Query("complete_decks", pattern="^(complete_decks|max_completion)$"),
    provider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    """Calculates the optimal set of cards to acquire to complete decks or maximize completion within a budget."""
    return await PriorityService.get_golden_wants(
        user_id=user_id,
        budget=budget,
        strategy=strategy,
        provider=provider,
    )
