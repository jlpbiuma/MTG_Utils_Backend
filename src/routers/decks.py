from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from src.core.auth import get_current_user_id
from src.schemas.deck import (
    DeckCreate, DeckUpdate, DeckSummaryResponse, DeckDetailResponse,
    DeckCardCreate, DeckCardUpdateQuantity, DeckCardAssign,
    DeckCardRelease, DeckCardReassign, SetCommanderRequest
)
from src.services.deck_service import DeckService

router = APIRouter(prefix="/decks", tags=["decks"])

@router.get("", response_model=List[DeckSummaryResponse])
async def list_decks(user_id: str = Depends(get_current_user_id)):
    return await DeckService.get_user_decks(user_id)

@router.post("", response_model=DeckSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_deck(data: DeckCreate, user_id: str = Depends(get_current_user_id)):
    return await DeckService.create_deck(user_id, data)

@router.get("/{deck_id}", response_model=DeckDetailResponse)
async def get_deck(deck_id: str, user_id: str = Depends(get_current_user_id)):
    deck = await DeckService.get_deck_detail(deck_id, user_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return deck

@router.put("/{deck_id}")
@router.patch("/{deck_id}")
async def update_deck(deck_id: str, data: DeckUpdate, user_id: str = Depends(get_current_user_id)):
    res = await DeckService.update_deck(deck_id, user_id, data)
    if res is None:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return {"status": "success", "deck": res}

@router.delete("/{deck_id}")
async def delete_deck(deck_id: str, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.delete_deck(deck_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return {"status": "success"}

@router.put("/{deck_id}/commander")
async def set_deck_commander(deck_id: str, data: SetCommanderRequest, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.set_commander(
        deck_id=deck_id,
        user_id=user_id,
        commander_name=data.commander,
        scryfall_id=data.commanderScryfallId,
        image_uri=data.commanderImageUri
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return {"status": "success"}

@router.post("/{deck_id}/cards")
async def add_card_to_deck(deck_id: str, data: DeckCardCreate, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.add_card_to_deck(deck_id, user_id, data)
    if not ok:
        raise HTTPException(status_code=400, detail="No se pudo añadir la carta al mazo")
    return {"status": "success"}

@router.patch("/cards/{card_id}")
async def update_deck_card_quantity(card_id: str, data: DeckCardUpdateQuantity, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.update_card_quantity(card_id, user_id, data.quantity)
    if not ok:
        raise HTTPException(status_code=404, detail="Carta no encontrada en el mazo")
    return {"status": "success"}

@router.delete("/cards/{card_id}")
async def remove_deck_card(card_id: str, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.remove_card(card_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Carta no encontrada en el mazo")
    return {"status": "success"}

@router.post("/cards/{card_id}/assign")
async def assign_card_to_deck(card_id: str, data: DeckCardAssign, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.assign_card(card_id, user_id, data.quantity)
    if not ok:
        raise HTTPException(status_code=400, detail="No se pudo asignar la carta al mazo")
    return {"status": "success"}

@router.post("/cards/{card_id}/release")
async def release_card_from_deck(card_id: str, data: DeckCardRelease, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.release_card(card_id, user_id, data.quantity)
    if not ok:
        raise HTTPException(status_code=400, detail="No se pudo liberar la carta")
    return {"status": "success"}

@router.post("/cards/{card_id}/reassign")
async def reassign_card_from_other_deck(card_id: str, data: DeckCardReassign, user_id: str = Depends(get_current_user_id)):
    ok = await DeckService.reassign_card(card_id, user_id, data.sourceDeckId, data.quantity)
    if not ok:
        raise HTTPException(status_code=400, detail="No se pudo reasignar la carta desde el otro mazo")
    return {"status": "success"}

@router.post("/{deck_id}/add-missing")
async def add_missing_cards(deck_id: str, user_id: str = Depends(get_current_user_id)):
    return await DeckService.add_missing_cards_to_collection(deck_id, user_id)
