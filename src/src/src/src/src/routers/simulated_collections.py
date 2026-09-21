from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from src.core.auth import get_current_user_id
from src.schemas.simulated_collections import (
    SimulatedCollectionSummary,
    SimulatedCollectionAnalysisResponse,
    SimulatedCollectionCreateRequest,
    SimulatedCollectionAnalyzeRequest,
)
from src.services.simulated_collection_service import SimulatedCollectionService

router = APIRouter(prefix="/simulated-collections", tags=["simulated-collections"])

@router.post("/analyze-raw", response_model=SimulatedCollectionAnalysisResponse)
async def analyze_raw_collection(
    payload: SimulatedCollectionAnalyzeRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Analyzes a raw text collection on-the-fly without saving to database."""
    return await SimulatedCollectionService.analyze_raw_text(
        user_id=user_id,
        raw_text=payload.rawText,
        name="Análisis Temporal",
        provider=payload.provider,
    )

@router.post("", response_model=SimulatedCollectionAnalysisResponse)
async def create_simulated_collection(
    payload: SimulatedCollectionCreateRequest,
    provider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    """Creates and persists a new simulated collection, returning complete analysis."""
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="El nombre de la colección simulada es obligatorio.")
    return await SimulatedCollectionService.create_simulated_collection(
        user_id=user_id,
        name=payload.name.strip(),
        description=payload.description,
        raw_text=payload.rawText,
        provider=provider,
    )

@router.get("", response_model=List[SimulatedCollectionSummary])
async def list_simulated_collections(
    provider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    """Lists all saved simulated collections for the current user."""
    return await SimulatedCollectionService.list_simulated_collections(
        user_id=user_id,
        provider=provider,
    )

@router.get("/{collection_id}", response_model=SimulatedCollectionAnalysisResponse)
async def get_simulated_collection(
    collection_id: str,
    provider: str = Query("cardmarket"),
    user_id: str = Depends(get_current_user_id),
):
    """Fetches detailed metrics and candidate decks for a saved simulated collection."""
    res = await SimulatedCollectionService.get_simulated_collection(
        user_id=user_id,
        collection_id=collection_id,
        provider=provider,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Colección simulada no encontrada.")
    return res

@router.delete("/{collection_id}")
async def delete_simulated_collection(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Deletes a saved simulated collection."""
    success = await SimulatedCollectionService.delete_simulated_collection(
        user_id=user_id,
        collection_id=collection_id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Colección simulada no encontrada.")
    return {"status": "deleted", "id": collection_id}
