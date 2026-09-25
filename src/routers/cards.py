from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from src.core.auth import get_current_user_id
from src.schemas.card_scan import CardScanResponse
from src.services.card_scan_service import CardScanService

router = APIRouter(prefix="/cards", tags=["cards"])


@router.post("/from-image", response_model=CardScanResponse)
async def scan_card_from_image(
    file: UploadFile = File(...),
    _user_id: str = Depends(get_current_user_id),
) -> CardScanResponse:
    """
    OCR via the OCR home service, then local catalog match + cheapest paper printing.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty image")
    filename = file.filename or "card.jpg"
    return await CardScanService.scan_image(data, filename=filename)
