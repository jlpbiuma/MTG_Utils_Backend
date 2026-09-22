from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from src.core.auth import get_current_user_id
from src.schemas.deck import (
    DeckCreate, DeckUpdate, DeckSummaryResponse, DeckDetailResponse,
    DeckCardCreate, DeckCardUpdateQuantity, DeckCardUpdateVersion, DeckCardAssign,
    DeckCardRelease, DeckCardReassign, SetCommanderRequest, DeckDeleteRequest,
    UpdateCardTagsRequest, UpdateDeckTagsRequest, MoveCardSideboardRequest,
)
from src.services.deck_service import DeckService

from src.schemas.deck_view import DeckViewResponse
from src.schemas.pricing import PriceProvider
from src.services.deck_view_service import DeckViewService

router = APIRouter(prefix="/decks", tags=["decks"])

@router.get("", response_model=List[DeckSummaryResponse])
async def list_decks(user_id: str = Depends(get_current_user_id)):
    return await DeckService.get_user_decks(user_id)

@router.post("", response_model=DeckSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_deck(data: DeckCreate, user_id: str = Depends(get_current_user_id)):
    return await DeckService.create_deck(user_id, data)

@router.get("/overlap")
async def get_decks_overlap(user_id: str = Depends(get_current_user_id)):
    return await DeckService.get_decks_overlap(user_id)

@router.get("/{deck_id}/view", response_model=DeckViewResponse)
async def get_deck_view(
    deck_id: str,
    provider: PriceProvider = "cardmarket",
    user_id: str = Depends(get_current_user_id),
):
    view = await DeckViewService.get_view(deck_id, user_id, provider)
    if view is None:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return view

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

@router.patch("/{deck_id}/archive")
async def archive_deck(
    deck_id: str,
    archived: bool = True,
    user_id: str = Depends(get_current_user_id),
):
    res = await DeckService.update_deck(deck_id, user_id, DeckUpdate(isArchived=archived))
    if res is None:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return {"status": "success", "isArchived": archived}

@router.put("/{deck_id}/tags")
async def update_deck_tags(
    deck_id: str,
    data: UpdateDeckTagsRequest,
    user_id: str = Depends(get_current_user_id),
):
    res = await DeckService.update_deck_tags(deck_id, user_id, data.tags)
    if not res:
        raise HTTPException(status_code=404, detail="Mazo no encontrado")
    return res

@router.get("/{deck_id}/deletion-impact")
async def get_deck_deletion_impact(deck_id: str, user_id: str = Depends(get_current_user_id)):
    impact = await DeckService.get_deletion_impact(deck_id, user_id)
    if "error" in impact:
        raise HTTPException(status_code=404, detail=impact["error"])
    return impact

@router.post("/{deck_id}/delete")
@router.delete("/{deck_id}")
async def delete_deck(
    deck_id: str,
    data: Optional[DeckDeleteRequest] = None,
    user_id: str = Depends(get_current_user_id),
):
    reassignments = data.reassignments if data else None
    ok = await DeckService.delete_deck(deck_id, user_id, reassignments)
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
        image_uri=data.commanderImageUri,
        partner_name=data.partner,
        partner_scryfall_id=data.partnerScryfallId,
        partner_image_uri=data.partnerImageUri,
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
    ok = await DeckService.update_card_quantity(
        card_id,
        user_id,
        data.quantity,
        data.setCode,
        set_code_provided="setCode" in data.model_fields_set,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Carta no encontrada en el mazo")
    return {"status": "success"}

@router.patch("/cards/{card_id}/sideboard")
async def move_deck_card_sideboard(
    card_id: str,
    data: MoveCardSideboardRequest,
    user_id: str = Depends(get_current_user_id),
):
    res = await DeckService.move_card_sideboard(card_id, user_id, data.isSideboard)
    if not res:
        raise HTTPException(status_code=404, detail="Carta no encontrada en el mazo")
    return res

@router.put("/cards/{card_id}/tags")
async def update_deck_card_tags(
    card_id: str,
    data: UpdateCardTagsRequest,
    user_id: str = Depends(get_current_user_id),
):
    res = await DeckService.update_card_tags(card_id, user_id, data.tags)
    if not res:
        raise HTTPException(status_code=404, detail="Carta no encontrada en el mazo")
    return res

@router.patch("/cards/{card_id}/version")
async def update_deck_card_version(
    card_id: str,
    data: DeckCardUpdateVersion,
    user_id: str = Depends(get_current_user_id)
):
    ok = await DeckService.update_card_version(
        card_id=card_id,
        user_id=user_id,
        card_scryfall_id=data.cardScryfallId,
        image_uri=data.imageUri,
        set_code=data.setCode,
    )
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

@router.post("/cards/{card_id}/add-missing")
async def add_missing_card(card_id: str, user_id: str = Depends(get_current_user_id)):
    res = await DeckService.add_missing_card_to_collection(card_id, user_id)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Error al añadir carta faltante"))
    return res

@router.post("/{deck_id}/add-missing")
async def add_missing_cards(deck_id: str, user_id: str = Depends(get_current_user_id)):
    return await DeckService.add_missing_cards_to_collection(deck_id, user_id)
