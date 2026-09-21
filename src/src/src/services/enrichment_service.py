import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Set
import httpx

from src.core.db import db
from src.services.scryfall_service import ScryfallService

logger = logging.getLogger("mtg_backend.enrichment")

_priority_lock = asyncio.Lock()
_priority_names: Set[str] = set()
_running_tasks: Set[asyncio.Task] = set()

RATE_LIMIT_DELAY_SECONDS = 0.1
WORKER_URL = os.getenv("WORKER_URL", "http://worker:8001").rstrip("/")


async def enqueue_priority_card_names(names: List[str]) -> None:
    """Adds card names to the priority queue to be enriched immediately."""
    trimmed = {name.strip() for name in names if name and name.strip()}
    if not trimmed:
        return
    async with _priority_lock:
        _priority_names.update(trimmed)


async def _drain_priority_names(limit: int) -> List[str]:
    async with _priority_lock:
        names = sorted(_priority_names)[:limit]
        for name in names:
            _priority_names.discard(name)
    return names


async def enrich_card_name(name: str) -> bool:
    """
    Resolves a single card name through the catalog (DB-first, Scryfall as
    fallback) and back-fills any pending DeckCard / CollectionCard rows that
    reference it under a `pending:` id or with missing metadata.
    """
    cat = await ScryfallService.get_or_resolve_catalog_card(name)
    if not cat:
        return False

    card_id = cat["id"]
    mana_cost = cat.get("manaCost")
    type_line = cat.get("typeLine")
    image_uri = cat.get("imageUri")

    pending_deck = await db.deckcard.find_many(
        where={
            "cardName": {"equals": name, "mode": "insensitive"},
            "OR": [
                {"cardScryfallId": {"startswith": "pending:"}},
                {"typeLine": None},
                {"imageUri": None},
            ],
        }
    )
    for dc in pending_deck:
        try:
            existing = await db.deckcard.find_unique(
                where={
                    "deckId_cardScryfallId_isSideboard": {
                        "deckId": dc.deckId,
                        "cardScryfallId": card_id,
                        "isSideboard": dc.isSideboard,
                    }
                }
            )
            if existing and existing.id != dc.id:
                await db.deckcard.update(
                    where={"id": existing.id},
                    data={
                        "quantity": existing.quantity + dc.quantity,
                        "imageUri": existing.imageUri or image_uri,
                        "manaCost": existing.manaCost or mana_cost,
                        "typeLine": existing.typeLine or type_line,
                    },
                )
                await db.deckcard.delete(where={"id": dc.id})
            else:
                await db.deckcard.update(
                    where={"id": dc.id},
                    data={
                        "cardScryfallId": card_id,
                        "imageUri": image_uri,
                        "manaCost": mana_cost,
                        "typeLine": type_line,
                    },
                )
        except Exception as e:
            logger.warning(f"Could not enrich deck card {dc.id}: {e}")

    pending_coll = await db.collectioncard.find_many(
        where={
            "enrichmentKey": None,
            "cardName": {"equals": name, "mode": "insensitive"},
            "OR": [
                {"cardScryfallId": {"startswith": "pending:"}},
                {"typeLine": None},
                {"imageUri": None},
            ],
        }
    )
    for cc in pending_coll:
        try:
            existing = await db.collectioncard.find_unique(
                where={
                    "userId_cardScryfallId": {
                        "userId": cc.userId,
                        "cardScryfallId": card_id,
                        "isFoil": getattr(cc, "isFoil", False),
                    }
                }
            )
            if existing and existing.id != cc.id:
                await db.collectioncard.update(
                    where={"id": existing.id},
                    data={
                        "quantity": existing.quantity + cc.quantity,
                        "imageUri": existing.imageUri or image_uri,
                        "manaCost": existing.manaCost or mana_cost,
                        "typeLine": existing.typeLine or type_line,
                    },
                )
                await db.collectioncard.delete(where={"id": cc.id})
            else:
                await db.collectioncard.update(
                    where={"id": cc.id},
                    data={
                        "cardScryfallId": card_id,
                        "imageUri": image_uri,
                        "manaCost": mana_cost,
                        "typeLine": type_line,
                    },
                )
        except Exception as e:
            logger.warning(f"Could not enrich collection card {cc.id}: {e}")

    return True


async def run_priority_enrichment(max_names: int = 50) -> Dict[str, Any]:
    """
    Processes the queued priority card names immediately, resolving them
    through the catalog (DB-first, Scryfall fallback) and back-filling any
    pending rows. Respects the 100ms Scryfall rate-limit between calls.
    """
    names = await _drain_priority_names(limit=max_names)
    resolved = 0
    for name in names:
        try:
            if await enrich_card_name(name):
                resolved += 1
        except Exception as e:
            logger.error(f"Priority enrichment failed for '{name}': {e}")
        await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)

    return {
        "status": "success",
        "prioritizedNames": len(names),
        "resolvedCards": resolved,
    }


def trigger_async_priority_enrichment(names: Optional[List[str]] = None) -> None:
    """
    Fire-and-forget: delegate imports to the unified worker. This keeps every
    Scryfall/MinIO write in one service and never delays the HTTP import.
    """
    async def _dispatch() -> None:
        unique_names = list(dict.fromkeys(name.strip() for name in (names or []) if name and name.strip()))
        if not unique_names:
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{WORKER_URL}/prioritize", json=unique_names)
            response.raise_for_status()

    async def _schedule() -> None:
        task = asyncio.create_task(_dispatch())
        _running_tasks.add(task)
        task.add_done_callback(_running_tasks.discard)

    try:
        asyncio.get_running_loop().create_task(_schedule())
    except RuntimeError:
        logger.warning("No running event loop; skipping async priority enrichment")
