from fastapi import APIRouter, Depends, Query
from typing import Optional
from src.core.auth import get_current_user_id
from src.schemas.priorities import PrioritiesResponse
from src.services.priority_service import PriorityService

router = APIRouter(prefix="/priorities", tags=["priorities"])

@router.get("", response_model=PrioritiesResponse)
async def get_priorities(
    sort: str = Query("demand", pattern="^(demand|impact|completion)$"),
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
        reassignable_only=reassignableOnly,
        hide_owned=hideOwned,
        card_type=cardType,
        page=page,
        limit=limit,
        provider=provider,
    )
