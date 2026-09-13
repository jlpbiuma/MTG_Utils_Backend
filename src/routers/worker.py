import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Body

from src.core.db import db
from src.services.scryfall_service import ScryfallService
from src.services.enrichment_service import (
    enqueue_priority_card_names,
    run_priority_enrichment,
    trigger_async_priority_enrichment,
)

logger = logging.getLogger("mtg_backend.worker")

router = APIRouter(prefix="/worker", tags=["worker"])


@router.post("/enrich")
async def run_enrichment_worker(
    card_names: Optional[List[str]] = Body(None),
    priority: bool = Body(False),
    max_names: int = Body(50),
):
    """
    Enriches pending cards with Scryfall data.

    - Without `card_names`: finds cards with pending IDs or missing typeLine
      in decks and collections and enriches them in safe batches of 75.
    - With `card_names`: prioritizes exactly those cards immediately
      (database-first, Scryfall fallback). When `priority` is True the names
      are enqueued and resolved by the dedicated priority pipeline.
    """
    if card_names:
        if priority:
            trigger_async_priority_enrichment(card_names)
            return {"status": "success", "prioritized": len(card_names)}
        await enqueue_priority_card_names(card_names)
        return await run_priority_enrichment(max_names=max_names)

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


@router.post("/trigger-priority")
async def trigger_priority_enrichment(card_names: List[str] = Body(..., embed=True)):
    """Fire-and-forget: prioritizes enrichment for the given card names."""
    if not card_names:
        return {"status": "success", "prioritized": 0}
    trigger_async_priority_enrichment(card_names)
    return {"status": "success", "prioritized": len(card_names)}
