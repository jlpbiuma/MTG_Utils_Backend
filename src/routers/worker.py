import asyncio
import logging
from fastapi import APIRouter
from src.core.db import db
from src.services.scryfall_service import ScryfallService

logger = logging.getLogger("mtg_backend.worker")

router = APIRouter(prefix="/worker", tags=["worker"])

@router.post("/enrich")
async def run_enrichment_worker():
    """
    Finds cards with pending IDs or missing typeLine in decks and collections
    and enriches them with Scryfall in safe batches of 75.
    """
    pending_deck_cards = await db.deckcard.find_many(
        where={
            "OR": [
                {"cardScryfallId": {"startswith": "pending:"}},
                {"typeLine": None}
            ]
        },
        take=75
    )

    enriched_count = 0
    for card in pending_deck_cards:
        try:
            cat = await ScryfallService.get_or_resolve_catalog_card(card.cardName)
            if cat:
                await db.deckcard.update(
                    where={"id": card.id},
                    data={
                        "cardScryfallId": cat["id"],
                        "typeLine": cat.get("typeLine"),
                        "manaCost": cat.get("manaCost"),
                        "imageUri": cat.get("imageUri"),
                    }
                )
                enriched_count += 1
            await asyncio.sleep(0.1) # 100ms rate limit
        except Exception as e:
            logger.error(f"Error enriching card '{card.cardName}': {e}")

    return {"status": "success", "enrichedCards": enriched_count}
