import httpx
import unicodedata
import re
import time
import logging
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.services.card_utils import normalize_card_name
from src.schemas.edhrec import EdhrecCardRecommendation

logger = logging.getLogger("mtg_backend.edhrec")

def to_edhrec_slug(name: str) -> str:
    if not name:
        return ""
    s = name.lower()
    # Remove accents
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s*//\s*", "-", s)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s

def get_edhrec_card_image_url(card_id: str) -> Optional[str]:
    if not card_id or len(card_id) < 2:
        return None
    return f"https://card-images.edhrec.com/normal/front/{card_id[0]}/{card_id[1]}/{card_id}.jpg"

# In-memory cache with 24h TTL
_edhrec_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 24 * 3600

class EdhrecService:
    @staticmethod
    async def fetch_edhrec_data(commander_name: str) -> Optional[Dict[str, Any]]:
        slug = to_edhrec_slug(commander_name)
        if not slug:
            return None

        now = time.time()
        if slug in _edhrec_cache:
            entry = _edhrec_cache[slug]
            if now - entry["timestamp"] < CACHE_TTL_SECONDS:
                return entry["data"]

        url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
        headers = {
            "User-Agent": "MTGUtils/2.0 (FastAPI-Python-Backend)",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
            try:
                res = await client.get(url)
                if res.status_code != 200:
                    logger.warning(f"EDHREC returned {res.status_code} for slug '{slug}'")
                    return None
                raw = res.json()
            except Exception as e:
                logger.error(f"Error requesting EDHREC for '{commander_name}': {e}")
                return None

        cardlist = raw.get("cardlist", [])
        categories: List[str] = []
        cards_map: Dict[str, Dict[str, Any]] = {}

        for group in cardlist:
            tag = group.get("header") or group.get("tag") or "Recommendations"
            if tag not in categories:
                categories.append(tag)

            for cv in group.get("cardviews", []):
                name = cv.get("name", "")
                if not name:
                    continue

                card_id = cv.get("id") or cv.get("sanitized", "")
                num_decks = cv.get("num_decks", 0)
                potential_decks = cv.get("potential_decks", 0)
                inclusion_pct = round((num_decks / potential_decks) * 100, 1) if potential_decks > 0 else 0.0
                synergy = round(cv.get("synergy", 0.0) * 100, 1) if "synergy" in cv else 0.0
                img = get_edhrec_card_image_url(card_id)

                norm = normalize_card_name(name)
                if norm in cards_map:
                    if tag not in cards_map[norm]["categories"]:
                        cards_map[norm]["categories"].append(tag)
                    if inclusion_pct > cards_map[norm]["inclusionPct"]:
                        cards_map[norm]["inclusionPct"] = inclusion_pct
                else:
                    cards_map[norm] = {
                        "id": card_id,
                        "name": name,
                        "normalizedName": norm,
                        "sanitized": cv.get("sanitized", ""),
                        "category": tag,
                        "categories": [tag],
                        "numDecks": num_decks,
                        "potentialDecks": potential_decks,
                        "inclusionPct": inclusion_pct,
                        "synergy": synergy,
                        "imageUri": img,
                    }

        parsed_cards = list(cards_map.values())
        parsed_cards.sort(key=lambda c: c["inclusionPct"], reverse=True)

        result = {
            "categories": categories,
            "cards": parsed_cards,
        }

        _edhrec_cache[slug] = {
            "timestamp": now,
            "data": result,
        }
        return result

    @staticmethod
    async def get_deck_recommendations(deck_id: str, current_user_id: str) -> "DeckRecommendationsResponse":
        from src.schemas.edhrec import DeckRecommendationsResponse, CommanderStats

        # Fetch deck
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck:
            return DeckRecommendationsResponse(error="Mazo no encontrado")

        commander_name = deck.commander
        if not commander_name:
            # Fallback: check deck cards with isCommander
            cmd_card = await db.deckcard.find_first(
                where={"deckId": deck_id, "isCommander": True}
            )
            if cmd_card:
                commander_name = cmd_card.cardName

        if not commander_name:
            return DeckRecommendationsResponse(error="El mazo no tiene comandante asignado")

        edhrec_data = await EdhrecService.fetch_edhrec_data(commander_name)
        if not edhrec_data:
            return DeckRecommendationsResponse(error=f"No se encontraron recomendaciones en EDHREC para {commander_name}")

        # Fetch deck cards for ownership matching
        deck_cards = await db.deckcard.find_many(where={"deckId": deck_id})
        deck_set = {normalize_card_name(c.cardName) for c in deck_cards}

        # Fetch owner collection cards
        collection_owner_id = deck.userId or current_user_id
        col_cards = await db.collectioncard.find_many(where={"userId": collection_owner_id})
        col_map = {normalize_card_name(c.cardName): c.quantity for c in col_cards}

        recommendations: List[EdhrecCardRecommendation] = []
        for c in edhrec_data.get("cards", []):
            norm = c["normalizedName"]
            is_in_deck = norm in deck_set
            col_qty = col_map.get(norm, 0)
            is_in_col = col_qty > 0

            recommendations.append(EdhrecCardRecommendation(
                id=c["id"],
                name=c["name"],
                normalizedName=norm,
                sanitized=c["sanitized"],
                category=c["category"],
                categories=c.get("categories", [c["category"]]),
                numDecks=c["numDecks"],
                potentialDecks=c["potentialDecks"],
                inclusionPct=c["inclusionPct"],
                synergy=c["synergy"],
                imageUri=c.get("imageUri"),
                isInDeck=is_in_deck,
                isInCollection=is_in_col,
                collectionQuantity=col_qty,
            ))

        return DeckRecommendationsResponse(
            commander=CommanderStats(
                name=commander_name,
                imageUri=deck.commanderImageUri or get_edhrec_card_image_url(deck.commanderScryfallId or ""),
                numDecks=0,
                colorIdentity=[],
            ),
            categories=edhrec_data.get("categories", []),
            recommendations=recommendations,
        )

