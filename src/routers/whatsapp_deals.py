from fastapi import APIRouter, Depends, HTTPException
from src.core.auth import get_current_user_id
from src.schemas.whatsapp_deals import WhatsAppMatchesResponse, ManualOpportunityRequest
from src.services.manual_opportunity_service import compare_link
from src.services.whatsapp_deals_service import WhatsAppDealsService

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.post("/compare-link", response_model=WhatsAppMatchesResponse)
async def compare_manual_link(
    data: ManualOpportunityRequest,
    user_id: str = Depends(get_current_user_id),
):
    try:
        return await compare_link(user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/matches", response_model=WhatsAppMatchesResponse)
async def get_whatsapp_matches(
    user_id: str = Depends(get_current_user_id),
):
    """
    Devuelve los cruces entre las cartas detectadas en WhatsApp y:
    - Los Wants del usuario (ofertas de venta de cartas que buscas).
    - La Colección del usuario (demandas de compra de cartas que tienes).
    """
    return await WhatsAppDealsService.get_matches(user_id)
