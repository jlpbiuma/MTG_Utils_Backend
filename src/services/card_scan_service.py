"""
Card scan: forward image to OCR home, match local catalog, pick cheapest printing.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from src.core.config import settings
from src.core.db import db
from src.schemas.card_scan import (
    CardScanAlternative,
    CardScanCatalog,
    CardScanPrinting,
    CardScanResponse,
)
from src.services.card_utils import normalize_card_name
from src.services.image_resolver import safe_image_uri
from src.services.pricing_service import PricingService
from src.services.scryfall_service import ScryfallService, score_card_match

logger = logging.getLogger("mtg_backend.card_scan")

# Accept local matches up to substring-ish quality (see score_card_match).
_MAX_ACCEPT_SCORE = 2


class CardScanService:
    @staticmethod
    async def ocr_card_title(image_bytes: bytes, filename: str = "card.jpg") -> str:
        base = (settings.OCR_API_URL or "").rstrip("/")
        if not base:
            raise HTTPException(status_code=503, detail="OCR_API_URL is not configured")

        headers: Dict[str, str] = {}
        if settings.OCR_API_TOKEN:
            headers["X-OCR-Token"] = settings.OCR_API_TOKEN

        timeout = httpx.Timeout(settings.OCR_API_TIMEOUT)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{base}/ocr/card-title",
                    headers=headers,
                    files={"file": (filename, image_bytes, "application/octet-stream")},
                )
        except httpx.HTTPError as exc:
            logger.error("OCR API request failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"OCR service unreachable: {exc}") from exc

        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OCR service returned HTTP {resp.status_code}",
            )

        title = (resp.json() or {}).get("title")
        if not isinstance(title, str) or len(title.strip()) < 2:
            raise HTTPException(status_code=422, detail="OCR did not detect a card title")
        return title.strip()

    @staticmethod
    async def _cheapest_printing_with_set(norm: str) -> Optional[Any]:
        """Cheapest paper printing for a normalized name, including set code."""
        if not norm:
            return None
        try:
            rows = await db.query_raw(
                """
                SELECT DISTINCT ON (cc.normalized_name)
                    cp.id,
                    cp.catalog_id AS "catalogId",
                    cp.collector_number AS "collectorNumber",
                    cp.image_uri AS "imageUri",
                    cp.price_eur AS "priceEur",
                    cp.price_cardmarket_trend AS "priceCardmarketTrend",
                    cs.code AS "setCode",
                    cc.normalized_name AS "normalizedName"
                FROM card_printings cp
                JOIN card_catalog cc ON cc.id = cp.catalog_id
                LEFT JOIN card_sets cs ON cs.id = cp.set_id
                WHERE cc.normalized_name = $1
                  AND coalesce(cp.collector_number, '') NOT LIKE 'A-%'
                  AND coalesce(cp.collector_number, '') NOT LIKE 'a-%'
                  AND coalesce(cs.is_digital, false) = false
                  AND lower(coalesce(cs.set_type, '')) NOT IN ('alchemy', 'memorabilia', 'token')
                  AND NOT (
                    length(lower(coalesce(cs.code, ''))) = 4
                    AND lower(coalesce(cs.code, '')) LIKE 'a%'
                  )
                  AND lower(coalesce(cc.type_line, '')) NOT IN ('card', 'card // card')
                  AND lower(coalesce(cc.type_line, '')) NOT LIKE 'card // card%'
                  AND lower(coalesce(cc.type_line, '')) NOT LIKE '%token%'
                  AND lower(coalesce(cc.type_line, '')) NOT LIKE '%emblem%'
                  AND coalesce(cc.name, '') NOT LIKE 'A-%'
                  AND coalesce(cc.name, '') NOT LIKE 'a-%'
                ORDER BY cc.normalized_name,
                         CASE
                           WHEN coalesce(cp.price_cardmarket_trend, cp.price_eur, 0) > 0
                           THEN coalesce(cp.price_cardmarket_trend, cp.price_eur)
                           ELSE 1e18
                         END ASC,
                         cp.updated_at DESC
                LIMIT 1
                """,
                norm,
            )
            if rows:
                row = rows[0]
                return SimpleNamespace(
                    id=row["id"],
                    catalogId=row.get("catalogId"),
                    collectorNumber=row.get("collectorNumber"),
                    imageUri=row.get("imageUri"),
                    priceEur=row.get("priceEur"),
                    priceCardmarketTrend=row.get("priceCardmarketTrend"),
                    setCode=row.get("setCode"),
                )
        except Exception:
            logger.debug("cheapest printing SQL failed; ORM fallback", exc_info=True)

        by_norm = await PricingService._cheapest_printings_by_norm([norm])
        printing = by_norm.get(norm)
        if not printing:
            return None
        set_obj = getattr(printing, "set", None)
        set_code = getattr(set_obj, "code", None) if set_obj else None
        return SimpleNamespace(
            id=printing.id,
            catalogId=getattr(printing, "catalogId", None),
            collectorNumber=getattr(printing, "collectorNumber", None),
            imageUri=getattr(printing, "imageUri", None),
            priceEur=getattr(printing, "priceEur", None),
            priceCardmarketTrend=getattr(printing, "priceCardmarketTrend", None),
            setCode=set_code,
        )

    @staticmethod
    async def resolve_from_title(ocr_title: str) -> CardScanResponse:
        title = ocr_title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="Empty OCR title")

        # 1) Exact local catalog (O(1))
        catalog = await ScryfallService.get_catalog_card(title)
        match_score = 0 if catalog else 99
        alternatives: List[CardScanAlternative] = []

        # 2) Folded local search if not exact
        if not catalog:
            local = await ScryfallService.search_cards_local(title, limit=8)
            local.sort(key=lambda c: (score_card_match(c.get("name"), title), c.get("name") or ""))
            for hit in local[:5]:
                alternatives.append(
                    CardScanAlternative(
                        id=hit.get("id") or "",
                        name=hit.get("name") or "",
                        matchScore=score_card_match(hit.get("name"), title),
                        setCode=hit.get("setCode") or hit.get("set"),
                        collectorNumber=hit.get("collectorNumber") or hit.get("collector_number"),
                        imageUri=safe_image_uri(hit.get("imageUri") or hit.get("image_uri")),
                    )
                )
            if local:
                best = local[0]
                best_score = score_card_match(best.get("name"), title)
                if best_score <= _MAX_ACCEPT_SCORE and best.get("id") and best.get("name"):
                    match_score = best_score
                    catalog = {
                        "id": best["id"],
                        "name": best["name"],
                        "normalizedName": best.get("normalizedName")
                        or normalize_card_name(best["name"]),
                        "manaCost": best.get("manaCost") or best.get("mana_cost"),
                        "typeLine": best.get("typeLine") or best.get("type_line"),
                        "imageUri": safe_image_uri(best.get("imageUri") or best.get("image_uri")),
                    }

        if not catalog:
            raise HTTPException(
                status_code=404,
                detail=f"No local catalog match for OCR title {title!r}",
            )

        norm = catalog.get("normalizedName") or normalize_card_name(catalog["name"])
        printing_ns = await CardScanService._cheapest_printing_with_set(norm)
        printing: Optional[CardScanPrinting] = None
        if printing_ns:
            printing = CardScanPrinting(
                id=printing_ns.id,
                catalogId=getattr(printing_ns, "catalogId", None) or catalog["id"],
                setCode=getattr(printing_ns, "setCode", None),
                collectorNumber=getattr(printing_ns, "collectorNumber", None),
                imageUri=safe_image_uri(getattr(printing_ns, "imageUri", None)),
                priceEur=getattr(printing_ns, "priceEur", None),
                priceCardmarketTrend=getattr(printing_ns, "priceCardmarketTrend", None),
            )

        return CardScanResponse(
            ocrTitle=title,
            matchScore=match_score,
            catalog=CardScanCatalog(
                id=catalog["id"],
                name=catalog["name"],
                normalizedName=norm,
                manaCost=catalog.get("manaCost"),
                typeLine=catalog.get("typeLine"),
                imageUri=safe_image_uri(catalog.get("imageUri")),
            ),
            printing=printing,
            alternatives=alternatives,
        )

    @staticmethod
    async def scan_image(image_bytes: bytes, filename: str = "card.jpg") -> CardScanResponse:
        title = await CardScanService.ocr_card_title(image_bytes, filename=filename)
        return await CardScanService.resolve_from_title(title)
