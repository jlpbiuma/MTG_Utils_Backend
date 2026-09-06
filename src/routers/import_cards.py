import httpx
from fastapi import APIRouter, Depends, HTTPException
from src.core.auth import get_current_user_id
from src.schemas.import_export import (
    ParseTextRequest, ParsedDecklist,
    ImportDeckTextRequest, ImportMoxfieldRequest,
    ImportCollectionTextRequest
)
from src.services.import_service import parse_decklist
from src.services.deck_service import DeckService
from src.services.collection_service import CollectionService

router = APIRouter(prefix="/import", tags=["import"])

@router.post("/parse", response_model=ParsedDecklist)
async def parse_text(data: ParseTextRequest):
    return parse_decklist(data.text)

@router.post("/deck")
async def import_deck_text(data: ImportDeckTextRequest, user_id: str = Depends(get_current_user_id)):
    deck = await DeckService.import_deck_text(
        user_id=user_id,
        name=data.name,
        raw_text=data.text,
        format=data.format,
        commander=data.commander
    )
    return deck

@router.post("/collection")
async def import_collection_text(data: ImportCollectionTextRequest, user_id: str = Depends(get_current_user_id)):
    return await CollectionService.import_collection_text(user_id=user_id, raw_text=data.text)

@router.post("/moxfield")
async def import_moxfield_deck(data: ImportMoxfieldRequest, user_id: str = Depends(get_current_user_id)):
    # Extract deck ID from url
    # e.g. https://www.moxfield.com/decks/AbCdEfGh123
    import re
    match = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", data.url)
    if not match:
        raise HTTPException(status_code=400, detail="URL de Moxfield inválida")

    mox_id = match.group(1)
    api_url = f"https://api2.moxfield.com/v3/decks/all/{mox_id}"

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}, timeout=12.0) as client:
        try:
            res = await client.get(api_url)
            if res.status_code != 200:
                raise HTTPException(status_code=400, detail="No se pudo obtener el mazo desde Moxfield")
            deck_data = res.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error conectando con Moxfield: {str(e)}")

    name = deck_data.get("name", "Moxfield Deck")
    format_name = data.format or deck_data.get("format", "Commander")

    # Reconstruct text list
    lines = []
    # Commander
    commanders = deck_data.get("commanders", {})
    commander_name = None
    if commanders:
        lines.append("// Commander")
        for card_name, card_info in commanders.items():
            qty = card_info.get("quantity", 1)
            lines.append(f"{qty} {card_name}")
            if not commander_name:
                commander_name = card_name

    # Mainboard
    mainboard = deck_data.get("mainboard", {})
    if mainboard:
        lines.append("// Main")
        for card_name, card_info in mainboard.items():
            qty = card_info.get("quantity", 1)
            lines.append(f"{qty} {card_name}")

    # Sideboard
    sideboard = deck_data.get("sideboard", {})
    if sideboard:
        lines.append("// Sideboard")
        for card_name, card_info in sideboard.items():
            qty = card_info.get("quantity", 1)
            lines.append(f"{qty} {card_name}")

    raw_text = "\n".join(lines)
    deck = await DeckService.import_deck_text(
        user_id=user_id,
        name=name,
        raw_text=raw_text,
        format=format_name,
        commander=commander_name
    )
    return deck
