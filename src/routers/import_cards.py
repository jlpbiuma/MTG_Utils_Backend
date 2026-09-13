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


async def _fetch_moxfield_deck(mox_id: str) -> dict:
    """Fetch a public Moxfield deck, tolerating the API version in use."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.moxfield.com/",
    }
    endpoints = (
        f"https://api2.moxfield.com/v3/decks/all/{mox_id}",
        f"https://api2.moxfield.com/v2/decks/all/{mox_id}",
    )
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as client:
        last_status = None
        try:
            for api_url in endpoints:
                res = await client.get(api_url)
                last_status = res.status_code
                if res.status_code == 200:
                    payload = res.json()
                    if isinstance(payload, dict) and any(payload.get(key) for key in ("mainboard", "commanders", "sideboard")):
                        return payload
                if res.status_code not in (400, 403, 404, 405, 429, 500, 502, 503, 504):
                    res.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=400, detail=f"Error conectando con Moxfield: {exc}") from exc
    raise HTTPException(status_code=400, detail=f"Moxfield no permitió obtener el mazo (HTTP {last_status})")

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
    return await CollectionService.import_collection_text(user_id=user_id, raw_text=data.text, request_key=data.requestKey)

@router.post("/moxfield")
async def import_moxfield_deck(data: ImportMoxfieldRequest, user_id: str = Depends(get_current_user_id)):
    # Extract deck ID from url
    # e.g. https://www.moxfield.com/decks/AbCdEfGh123
    import re
    match = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", data.url)
    if not match:
        raise HTTPException(status_code=400, detail="URL de Moxfield inválida")

    mox_id = match.group(1)
    deck_data = await _fetch_moxfield_deck(mox_id)

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

@router.get("/collection/{import_id}")
async def get_collection_import(import_id: str, user_id: str = Depends(get_current_user_id)):
    from src.core.db import db
    from src.services.bulk_import import import_status
    return await import_status(db, user_id, import_id)

@router.post("/collection/{import_id}/retry")
async def retry_collection_import(import_id: str, user_id: str = Depends(get_current_user_id)):
    from src.core.db import db
    from src.services.bulk_import import retry_import
    return await retry_import(db, user_id, import_id)
