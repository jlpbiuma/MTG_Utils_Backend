import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from src.core.db import db

logger = logging.getLogger("mtg_backend.images")


def is_scryfall_image_uri(value: Any) -> bool:
    """Return True only for Scryfall's public card-image CDN."""
    if not isinstance(value, str) or not value:
        return False
    try:
        return urlparse(value).hostname == "cards.scryfall.io"
    except ValueError:
        return False


def safe_image_uri(value: Any) -> Optional[str]:
    """Never expose a direct Scryfall image URL to an API client."""
    return value if isinstance(value, str) and value and not is_scryfall_image_uri(value) else None


def strip_scryfall_image_uris(value: Any) -> Any:
    """Recursively remove Scryfall CDN URLs from a JSON-compatible payload."""
    if isinstance(value, dict):
        return {key: strip_scryfall_image_uris(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_scryfall_image_uris(item) for item in value]
    return None if is_scryfall_image_uri(value) else value


async def resolve_minio_image_uris(card_ids: List[str]) -> Dict[str, str]:
    """Map CardPrinting ids to their locally-mirrored (MinIO) image URI.

    Collection/deck cards store the upstream Scryfall URL; prefer the
    locally-mirrored image when the worker has stored one so clients never
    depend on the Scryfall CDN.
    """
    if not card_ids:
        return {}
    try:
        printings = await db.cardprinting.find_many(
            where={"id": {"in": card_ids}},
        )
    except Exception:
        logger.warning("Could not resolve local images for %d cards", len(card_ids), exc_info=True)
        return {}
    return {
        p.id: image_uri
        for p in printings
        if (image_uri := safe_image_uri(p.imageUri))
    }
