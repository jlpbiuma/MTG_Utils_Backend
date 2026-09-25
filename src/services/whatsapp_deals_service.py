import json
import logging
import os
import re
import sqlite3
import urllib.parse
from typing import Any, Dict, List, Optional, Set

from src.core.config import settings
from src.core.db import db
from src.schemas.whatsapp_deals import (
    CatalogCardSummary,
    CollectionMatchInfo,
    WantMatchInfo,
    WhatsAppDealMatch,
    WhatsAppMatchesResponse,
)
from src.services.card_utils import normalize_card_name
from src.services.image_resolver import resolve_minio_image_uris, safe_image_uri
from src.services.want_service import WantService

logger = logging.getLogger("mtg_backend.whatsapp_deals")


def _get_sqlite_path(custom_path: Optional[str] = None) -> Optional[str]:
    candidates = [
        custom_path,
        getattr(settings, "WHATSAPP_DB_PATH", None),
        "/app/whatsapp_data/cards.db",
        "../worker_whatsapp/data/cards.db",
        "worker_whatsapp/data/cards.db",
        "data/cards.db",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _format_whatsapp_link(phone: str, text: str) -> str:
    clean_phone = re.sub(r"[^\d]", "", phone)
    encoded = urllib.parse.quote(text)
    return f"https://wa.me/{clean_phone}?text={encoded}"


class WhatsAppDealsService:
    @staticmethod
    async def get_matches(
        user_id: str,
        custom_db_path: Optional[str] = None,
    ) -> WhatsAppMatchesResponse:
        db_path = _get_sqlite_path(custom_db_path)
        if not db_path:
            logger.info("Base de datos de WhatsApp no encontrada; devolviendo lista vacía.")
            return WhatsAppMatchesResponse()

        # 1. Leer extracted_cards de SQLite en modo solo lectura
        rows = []
        last_scraped: Optional[str] = None
        total_scanned = 0

        try:
            # Conexión read-only para evitar bloquear el WAL del worker
            conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = """
            SELECT
                c.id,
                c.url,
                c.card_name,
                c.set_code,
                c.price,
                c.currency,
                c.condition,
                c.extra_json,
                c.extracted_at,
                l.subject,
                l.source_phone,
                l.source_chat,
                l.raw_message,
                l.detected_at
            FROM extracted_cards c
            JOIN detected_links l ON c.url = l.url
            WHERE l.link_status = 'valid'
            ORDER BY c.id DESC
            """
            rows = cursor.execute(query).fetchall()

            total_row = cursor.execute("SELECT count(*) FROM extracted_cards").fetchone()
            total_scanned = total_row[0] if total_row else len(rows)

            last_row = cursor.execute("SELECT max(extracted_at) FROM extracted_cards").fetchone()
            last_scraped = last_row[0] if last_row else None

            conn.close()
        except Exception as exc:
            logger.error("Error al leer base de datos de WhatsApp (%s): %s", db_path, exc)
            return WhatsAppMatchesResponse()

        if not rows:
            return WhatsAppMatchesResponse(
                total_cards_scanned=total_scanned,
                last_scraped_at=last_scraped,
            )

        # 2. Consultar Wants del usuario
        user_wants = await db.wantcard.find_many(where={"userId": user_id})
        wants_by_norm: Dict[str, List[Any]] = {}
        for w in user_wants:
            norm = normalize_card_name(w.cardName)
            if norm:
                wants_by_norm.setdefault(norm, []).append(w)

        # Demanda de mazos por carta
        decks_by_norm = await WantService._deck_demand_by_name(user_id)

        # 3. Consultar Colección del usuario
        user_collection = await db.collectioncard.find_many(where={"userId": user_id})
        col_by_norm: Dict[str, List[Any]] = {}
        for col in user_collection:
            norm = normalize_card_name(col.cardName)
            if norm:
                col_by_norm.setdefault(norm, []).append(col)

        # Asignaciones de copias en mazos
        deck_cards = await db.deckcard.find_many(
            where={"deck": {"userId": user_id}}
        )
        assigned_by_norm: Dict[str, int] = {}
        for dc in deck_cards:
            norm = normalize_card_name(dc.cardName)
            if norm:
                assigned_by_norm[norm] = assigned_by_norm.get(norm, 0) + (getattr(dc, "quantity", 1) or 1)

        # 4. Obtener catálogo para enriquecer información visual y precios
        all_norm_matched: Set[str] = set()
        for r in rows:
            norm = normalize_card_name(r["card_name"])
            if not norm:
                continue
            subj = (r["subject"] or "").lower()
            if (subj == "vende" and norm in wants_by_norm) or (subj == "compra_busca" and norm in col_by_norm):
                all_norm_matched.add(norm)

        catalog_map: Dict[str, Any] = {}
        if all_norm_matched:
            catalog_cards = await db.cardcatalog.find_many(
                where={"normalizedName": {"in": list(all_norm_matched)}}
            )
            scryfall_ids = [cc.id for cc in catalog_cards if cc.id]
            resolved_images = await resolve_minio_image_uris(scryfall_ids)

            for cc in catalog_cards:
                img = resolved_images.get(cc.id) or safe_image_uri(cc.imageUri)
                catalog_map[cc.normalizedName] = {
                    "card_id": cc.id,
                    "name": cc.name,
                    "image_uri": img,
                    "type_line": cc.typeLine,
                    "mana_cost": cc.manaCost,
                    "rarity": None,
                }

        # 5. Generar cruces
        wants_matches: List[WhatsAppDealMatch] = []
        collection_matches: List[WhatsAppDealMatch] = []

        for r in rows:
            card_name = r["card_name"] or ""
            norm = normalize_card_name(card_name)
            if not norm:
                continue

            subj = (r["subject"] or "").lower()
            extra = {}
            if r["extra_json"]:
                try:
                    extra = json.loads(r["extra_json"])
                except Exception:
                    extra = {}

            cat_info = catalog_map.get(norm)
            cat_summary = (
                CatalogCardSummary(
                    card_id=cat_info["card_id"],
                    name=cat_info["name"],
                    image_uri=cat_info["image_uri"],
                    type_line=cat_info["type_line"],
                    mana_cost=cat_info["mana_cost"],
                    rarity=cat_info.get("rarity"),
                )
                if cat_info
                else None
            )

            # Cruce 1: Venta en WhatsApp vs Mis Wants (Oportunidades de Compra)
            if subj == "vende" and norm in wants_by_norm:
                want_items = wants_by_norm[norm]
                qty_wanted = sum(getattr(w, "quantity", 1) or 1 for w in want_items)
                req_decks = [
                    d.deckName for d in decks_by_norm.get(norm, {}).values()
                ]

                wa_msg = f"¡Hola! Te escribo por el grupo de Magic porque he visto que vendes «{card_name}». ¿Aún la tienes disponible?"
                match_item = WhatsAppDealMatch(
                    id=r["id"],
                    card_name=card_name,
                    normalized_name=norm,
                    set_code=r["set_code"],
                    price=r["price"],
                    currency=r["currency"],
                    condition=r["condition"],
                    subject="vende",
                    whatsapp_url=r["url"],
                    source_phone=r["source_phone"] or "",
                    source_chat=r["source_chat"],
                    raw_message=r["raw_message"],
                    detected_at=r["detected_at"],
                    direct_whatsapp_link=_format_whatsapp_link(r["source_phone"] or "", wa_msg),
                    extra=extra,
                    catalog_card=cat_summary,
                    matched_want=WantMatchInfo(
                        want_id=want_items[0].id,
                        quantity_wanted=qty_wanted,
                        requested_decks=req_decks,
                    ),
                )
                wants_matches.append(match_item)

            # Cruce 2: Compra/Busca en WhatsApp vs Mi Colección (Oportunidades de Venta)
            elif subj == "compra_busca" and norm in col_by_norm:
                col_items = col_by_norm[norm]
                qty_owned = sum(getattr(c, "quantity", 1) or 1 for c in col_items)
                qty_assigned = assigned_by_norm.get(norm, 0)
                qty_free = max(0, qty_owned - qty_assigned)

                wa_msg = f"¡Hola! Te escribo por el grupo de Magic porque he visto que buscas «{card_name}». Tengo copias disponibles en mi colección..."
                match_item = WhatsAppDealMatch(
                    id=r["id"],
                    card_name=card_name,
                    normalized_name=norm,
                    set_code=r["set_code"],
                    price=r["price"],
                    currency=r["currency"],
                    condition=r["condition"],
                    subject="compra_busca",
                    whatsapp_url=r["url"],
                    source_phone=r["source_phone"] or "",
                    source_chat=r["source_chat"],
                    raw_message=r["raw_message"],
                    detected_at=r["detected_at"],
                    direct_whatsapp_link=_format_whatsapp_link(r["source_phone"] or "", wa_msg),
                    extra=extra,
                    catalog_card=cat_summary,
                    matched_collection=CollectionMatchInfo(
                        collection_id=col_items[0].id,
                        quantity_owned=qty_owned,
                        available_quantity=qty_free,
                    ),
                )
                collection_matches.append(match_item)

        return WhatsAppMatchesResponse(
            wants_matches=wants_matches,
            collection_matches=collection_matches,
            total_wants_matches=len(wants_matches),
            total_collection_matches=len(collection_matches),
            total_cards_scanned=total_scanned,
            last_scraped_at=last_scraped,
        )
