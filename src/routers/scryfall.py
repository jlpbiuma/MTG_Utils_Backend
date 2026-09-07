from fastapi import APIRouter, Query
from typing import List, Dict, Any, Optional
from src.services.scryfall_service import ScryfallService

router = APIRouter(prefix="/scryfall", tags=["scryfall"])

@router.get("/search")
async def search_cards(q: str = Query(..., min_length=1), page: int = Query(1, ge=1)):
    return await ScryfallService.search_cards(q, page)

@router.get("/autocomplete")
async def autocomplete_cards(q: str = Query(..., min_length=2)):
    return await ScryfallService.autocomplete_cards(q)

@router.get("/named")
async def get_card_named(name: str = Query(..., min_length=1), exact: bool = Query(False)):
    card = await ScryfallService.get_card_named(name, exact)
    return card or {}

@router.post("/bulk")
async def resolve_bulk(identifiers: List[Dict[str, Any]]):
    return await ScryfallService.resolve_cards_in_bulk(identifiers)

@router.get("/card")
async def get_card_details(
    id: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    set: Optional[str] = Query(None),
    collector_number: Optional[str] = Query(None),
):
    card = await ScryfallService.get_card_details_es(
        card_id=id,
        name=name,
        set_code=set,
        collector_number=collector_number,
    )
    return card or {}

