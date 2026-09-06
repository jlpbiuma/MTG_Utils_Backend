import httpx
import logging
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.services.card_utils import normalize_card_name

logger = logging.getLogger("mtg_backend.scryfall")

SCRYFALL_BASE = "https://api.scryfall.com"
SCRYFALL_HEADERS = {
    "User-Agent": "MTGUtils/2.0 (FastAPI-Python-Backend)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}

class ScryfallService:
    @staticmethod
    async def search_cards(query: str, page: int = 1) -> Dict[str, Any]:
        if not query or not query.strip():
            return {"total_cards": 0, "has_more": False, "data": []}

        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=10.0) as client:
            try:
                res = await client.get(f"{SCRYFALL_BASE}/cards/search", params={"q": query.strip(), "page": page})
                if res.status_code == 404:
                    return {"total_cards": 0, "has_more": False, "data": []}
                res.raise_for_status()
                data = res.json()
                return {
                    "total_cards": data.get("total_cards", 0),
                    "has_more": bool(data.get("has_more", False)),
                    "data": data.get("data", []),
                }
            except Exception as e:
                logger.error(f"Error searching cards on Scryfall: {e}")
                return {"total_cards": 0, "has_more": False, "data": []}

    @staticmethod
    async def autocomplete_cards(query: str) -> List[str]:
        if not query or len(query.strip()) < 2:
            return []

        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=5.0) as client:
            try:
                res = await client.get(f"{SCRYFALL_BASE}/cards/autocomplete", params={"q": query.strip()})
                if res.status_code != 200:
                    return []
                data = res.json()
                return data.get("data", [])
            except Exception as e:
                logger.error(f"Error autocompleting cards: {e}")
                return []

    @staticmethod
    async def get_card_named(name: str, exact: bool = False) -> Optional[Dict[str, Any]]:
        if not name or not name.strip():
            return None

        param_key = "exact" if exact else "fuzzy"
        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=8.0) as client:
            try:
                res = await client.get(f"{SCRYFALL_BASE}/cards/named", params={param_key: name.strip()})
                if res.status_code != 200:
                    return None
                return res.json()
            except Exception as e:
                logger.error(f"Error fetching card named '{name}': {e}")
                return None

    @staticmethod
    async def resolve_cards_in_bulk(identifiers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Resolves up to 75 identifiers using POST /cards/collection
        """
        if not identifiers:
            return []

        # Batch in chunks of 75
        results: List[Dict[str, Any]] = []
        chunks = [identifiers[i:i + 75] for i in range(0, len(identifiers), 75)]

        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=15.0) as client:
            for chunk in chunks:
                try:
                    res = await client.post(f"{SCRYFALL_BASE}/cards/collection", json={"identifiers": chunk})
                    if res.status_code == 200:
                        data = res.json()
                        results.extend(data.get("data", []))
                except Exception as e:
                    logger.error(f"Error in Scryfall bulk collection resolve: {e}")

        return results

    @staticmethod
    async def get_or_resolve_catalog_card(name: str) -> Optional[Dict[str, Any]]:
        """
        Checks CardCatalog in database, if not found queries Scryfall and saves to CardCatalog.
        """
        norm = normalize_card_name(name)
        if not norm:
            return None

        # Check local catalog
        existing = await db.cardcatalog.find_unique(where={"normalizedName": norm})
        if existing:
            return {
                "id": existing.id,
                "name": existing.name,
                "normalizedName": existing.normalizedName,
                "manaCost": existing.manaCost,
                "typeLine": existing.typeLine,
                "imageUri": existing.imageUri,
                "setCode": existing.setCode,
                "collectorNumber": existing.collectorNumber,
            }

        # Query Scryfall
        card_data = await ScryfallService.get_card_named(name, exact=False)
        if not card_data:
            return None

        card_id = card_data.get("id")
        card_name = card_data.get("name", name)
        card_norm = normalize_card_name(card_name)

        image_uri = None
        if "image_uris" in card_data and card_data["image_uris"]:
            image_uri = card_data["image_uris"].get("normal") or card_data["image_uris"].get("large")
        elif "card_faces" in card_data and card_data["card_faces"]:
            front = card_data["card_faces"][0]
            if "image_uris" in front and front["image_uris"]:
                image_uri = front["image_uris"].get("normal")

        created = await db.cardcatalog.upsert(
            where={"id": card_id},
            data={
                "create": {
                    "id": card_id,
                    "name": card_name,
                    "normalizedName": card_norm,
                    "manaCost": card_data.get("mana_cost"),
                    "typeLine": card_data.get("type_line"),
                    "imageUri": image_uri,
                    "setCode": card_data.get("set"),
                    "collectorNumber": card_data.get("collector_number"),
                },
                "update": {
                    "name": card_name,
                    "normalizedName": card_norm,
                    "manaCost": card_data.get("mana_cost"),
                    "typeLine": card_data.get("type_line"),
                    "imageUri": image_uri,
                }
            }
        )

        return {
            "id": created.id,
            "name": created.name,
            "normalizedName": created.normalizedName,
            "manaCost": created.manaCost,
            "typeLine": created.typeLine,
            "imageUri": created.imageUri,
            "setCode": created.setCode,
            "collectorNumber": created.collectorNumber,
        }
