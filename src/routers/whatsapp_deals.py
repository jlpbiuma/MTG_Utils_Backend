from fastapi import APIRouter, Depends
from src.core.auth import get_current_user_id
from src.schemas.whatsapp_deals import WhatsAppMatchesResponse
from src.services.whatsapp_deals_service import WhatsAppDealsService

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


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
