from fastapi import APIRouter, Query
from typing import List, Dict, Any, Optional
from src.services.scryfall_service import ScryfallService
from src.services.card_utils import normalize_card_name
from src.core.db import db
from src.services.image_resolver import safe_image_uri, strip_scryfall_image_uris

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
    return strip_scryfall_image_uris(card) if card else {}

@router.post("/bulk")
async def resolve_bulk(identifiers: List[Dict[str, Any]]):
    cards = await ScryfallService.resolve_cards_in_bulk(identifiers)
    return strip_scryfall_image_uris(cards)

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
    if card:
        catalog_id = card.get("id")
        try:
            catalog = await db.cardcatalog.find_unique(
                where={"normalizedName": normalize_card_name(card.get("name", ""))}
            )
            if catalog:
                catalog_id = catalog.id
        except Exception:
            pass
        try:
            printings = await db.cardprinting.find_many(
                where={
                    "OR": [
                        {"catalogId": catalog_id},
                        {"id": catalog_id},
                    ]
                },
                order={"releasedAt": "desc"},
                include={"set": True},
            )
            ruling_card_ids = list(dict.fromkeys([catalog_id, *(item.id for item in printings)]))
            rulings = await db.cardruling.find_many(
                where={"scryfallCardId": {"in": ruling_card_ids}},
                order={"rulingDate": "desc"},
            )
        except Exception:
            printings, rulings = [], []
        card_name_es = card.get("name_es") or card.get("name") or ""
        card["printings"] = []
        for item in printings:
            set_obj = getattr(item, "set", None)
            set_name = getattr(set_obj, "name", None) if set_obj else None
            set_code = getattr(set_obj, "code", None) if set_obj else getattr(item, "setCode", None)
            card["printings"].append(
                {
                    "id": item.id,
                    "set_code": set_code,
                    "set_name": set_name if set_name is not None else (card.get("set_name") or None),
                    "collector_number": item.collectorNumber,
                    "rarity": item.rarity,
                    "name_es": card_name_es,
                    "image_uri": safe_image_uri(item.imageUri),
                    "image_uri_small": safe_image_uri(item.imageUriSmall),
                    "image_uri_large": safe_image_uri(item.imageUriLarge),
                    "trend": item.priceCardmarketTrend,
                    "min": item.priceCardmarketMin,
                    "max": item.priceCardmarketMax,
                    "cardtrader_trend": item.priceCardtraderTrend,
                    "cardtrader_min": item.priceCardtraderMin,
                    "cardtrader_max": item.priceCardtraderMax,
                    "price_eur": item.priceEur,
                    "price_eur_foil": item.priceEurFoil,
                    "price_usd": item.priceUsd,
                    "price_usd_foil": item.priceUsdFoil,
                    "released_at": item.releasedAt.isoformat() if item.releasedAt else None,
                }
            )
        local_printing = next(
            (
                printing
                for printing in card["printings"]
                if printing["image_uri"]
                or printing["image_uri_small"]
                or printing["image_uri_large"]
            ),
            None,
        )
        if local_printing:
            normal = local_printing["image_uri"] or local_printing["image_uri_large"] or local_printing["image_uri_small"]
            small = local_printing["image_uri_small"] or normal
            large = local_printing["image_uri_large"] or normal
        else:
            current_images = card.get("image_uris") or {}
            normal = next(
                (
                    image
                    for size in ("normal", "large", "small")
                    if (image := safe_image_uri(current_images.get(size)))
                ),
                None,
            )
            small = safe_image_uri(current_images.get("small")) or normal
            large = safe_image_uri(current_images.get("large")) or normal
        card["image_uris"] = {
            "small": small,
            "normal": normal,
            "large": large,
            "art_crop": normal,
        } if normal else {}
        for face in card.get("card_faces") or []:
            face["image_uris"] = {}

        card["rulings"] = [
            {"date": ruling.rulingDate.isoformat(), "text": ruling.text, "source": ruling.source}
            for ruling in rulings
        ]
        # The worker-produced cached details do not carry Scryfall prices; derive
        # them from the most recent printing so market data is always present.
        if card.get("prices") is None and card["printings"]:
            first = card["printings"][0]
            card["prices"] = {
                "eur": first.get("price_eur") if first.get("price_eur") is not None else first.get("trend"),
                "eur_foil": first.get("price_eur_foil") if first.get("price_eur_foil") is not None else first.get("trend"),
                "usd": first.get("price_usd"),
                "usd_foil": first.get("price_usd_foil"),
            }
    return strip_scryfall_image_uris(card) if card else {}
