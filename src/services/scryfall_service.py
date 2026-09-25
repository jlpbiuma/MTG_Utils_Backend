import httpx
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from prisma import Json
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.services.card_utils import normalize_card_name, is_playable_card, is_catalog_record_playable
from src.services.image_resolver import safe_image_uri, strip_scryfall_image_uris

logger = logging.getLogger("mtg_backend.scryfall")

SCRYFALL_BASE = "https://api.scryfall.com"
SCRYFALL_HEADERS = {
    "User-Agent": "MTGUtils/2.0 (FastAPI-Python-Backend)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}
WORKER_URL = os.getenv("WORKER_URL", "http://worker:8001").rstrip("/")

# Apostrophes are deleted by translate (from-string longer than to-string).
# Accents are folded so "Seance" matches "Séance" and "yshtola" matches "Y'shtola".
_SEARCH_TRANSLATE_FROM = "áéíóúüñàèìòùâêîôûäëïöüç''’"
_SEARCH_TRANSLATE_TO = "aeiouunaeiouaeiouaeiouc"
_CATALOG_SEARCH_SQL = f"""
WITH searchable AS (
    SELECT id, name, mana_cost, type_line, image_uri, set_code, collector_number,
           regexp_replace(translate(lower(name), '{_SEARCH_TRANSLATE_FROM}', '{_SEARCH_TRANSLATE_TO}'),
                          '[^a-z0-9]+', '', 'g') AS search_name,
           regexp_replace(translate(lower(coalesce(name_es, '')), '{_SEARCH_TRANSLATE_FROM}', '{_SEARCH_TRANSLATE_TO}'),
                          '[^a-z0-9]+', '', 'g') AS search_name_es,
           regexp_replace(translate(lower(name), '{_SEARCH_TRANSLATE_FROM}', '{_SEARCH_TRANSLATE_TO}'),
                          '[^a-z0-9]+', ' ', 'g') AS search_name_spaced,
           regexp_replace(translate(lower(coalesce(name_es, '')), '{_SEARCH_TRANSLATE_FROM}', '{_SEARCH_TRANSLATE_TO}'),
                          '[^a-z0-9]+', ' ', 'g') AS search_name_es_spaced
    FROM card_catalog
    WHERE name NOT LIKE 'A-%'
      AND name NOT LIKE 'a-%'
      AND coalesce(collector_number, '') NOT LIKE 'A-%'
      AND coalesce(collector_number, '') NOT LIKE 'a-%'
      AND coalesce(type_line, '') NOT ILIKE '%emblem%'
      AND coalesce(type_line, '') NOT ILIKE '%token%'
      AND coalesce(type_line, '') NOT IN ('Card', 'Card // Card', 'card', 'card // card')
      AND (length(coalesce(set_code, '')) != 4 OR coalesce(set_code, '') NOT LIKE 'a%')
      AND coalesce(set_code, '') NOT LIKE 'y%'
      AND coalesce(set_code, '') NOT IN (
          'ana', 'anb', 'xana', 'oana', 'ajmp', 'pana', 'parl',
          'ea1', 'ea2', 'ea3', 'ha1', 'ha2', 'ha3', 'ha4', 'ha5', 'ha6', 'ha7',
          'aa1', 'aa2', 'aa3', 'aa4', 'pa1', 'hbg', 'j21', 'sir', 'sis', 'akr', 'klr', 'pio', 'om1', 'omb',
          'me1', 'me2', 'me3', 'me4', 'vma', 'tpr', 'td0', 'td2', 'pz1', 'pz2', 'prm', 'pmoa', 'psdg', 'past'
      )
)
SELECT id, name, mana_cost, type_line, image_uri, set_code, collector_number
FROM searchable
WHERE search_name LIKE '%' || $1 || '%' OR search_name_es LIKE '%' || $1 || '%'
ORDER BY CASE
    WHEN search_name = $1 OR search_name_es = $1 THEN 0
    WHEN search_name_spaced LIKE $1 || ' %' OR search_name_spaced LIKE $1 || 's %' OR search_name_spaced LIKE $1 || ' s %' THEN 1
    WHEN search_name LIKE $1 || '%' THEN 2
    WHEN search_name_spaced LIKE '% ' || $1 || ' %' OR search_name_spaced LIKE '% ' || $1 OR search_name_spaced LIKE '% ' || $1 || 's %' THEN 3
    WHEN search_name_es_spaced LIKE $1 || ' %' OR search_name_es LIKE $1 || '%' THEN 4
    ELSE 5 END, name ASC
LIMIT $2
"""


def fold_search_text(value: Optional[str]) -> str:
    """Lowercase key with accents and punctuation removed, for catalog search."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFD", value)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    folded = folded.lower().replace("'", "").replace("’", "").replace("`", "")
    return re.sub(r"[^a-z0-9]+", "", folded)


def score_card_match(name: Optional[str], query: Optional[str]) -> int:
    """Scores how well a card name matches the query. Lower is better."""
    norm_name = fold_search_text(name)
    norm_q = fold_search_text(query)
    if not norm_name or not norm_q:
        return 99
    if norm_name == norm_q:
        return 0

    def _clean(val: Optional[str]) -> str:
        s = unicodedata.normalize("NFD", (val or "").lower())
        s = re.sub(r"['`’]", "", s)
        s = re.sub(r"[^a-z0-9 ]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    clean_name = _clean(name)
    clean_q = _clean(query)

    if clean_name.startswith(f"{clean_q} ") or clean_name.startswith(f"{clean_q}s "):
        return 1
    if norm_name.startswith(norm_q):
        return 2
    if f" {clean_q} " in f" {clean_name} " or clean_name.endswith(f" {clean_q}"):
        return 3
    if norm_q in norm_name:
        return 4
    return 5


def _field(row: Any, *names: str) -> Any:
    if isinstance(row, dict):
        for name in names:
            if name in row and row[name] is not None:
                return row[name]
        for name in names:
            if name in row:
                return row[name]
        return None
    for name in names:
        if hasattr(row, name):
            return getattr(row, name)
    return None


class ScryfallService:
    @staticmethod
    async def get_catalog_card(name: str) -> Optional[Dict[str, Any]]:
        """Returns a card from the local catalog without contacting Scryfall."""
        norm = normalize_card_name(name)
        if not norm:
            return None

        existing = await db.cardcatalog.find_unique(where={"normalizedName": norm})
        if not existing:
            return None

        # A local catalog entry must still be a real playable card. Art series,
        # tokens, emblems and memorabilia (e.g. "Humongous Fungus // Humongous
        # Fungus") must never resolve as collection/deck cards.
        if not is_catalog_record_playable(existing):
            logger.info("Skipping non-playable catalog record for '%s'", name)
            return None

        # The worker hydrates full localized details into the detailsEs blob,
        # which carries the English oracle text too. Use it to decide commander
        # eligibility server-side without a live Scryfall round-trip.
        oracle_text: Optional[str] = None
        details = getattr(existing, "detailsEs", None)
        if isinstance(details, dict):
            oracle_text = details.get("oracle_text") or details.get("oracleText")

        return {
            "id": existing.id,
            "name": existing.name,
            "normalizedName": existing.normalizedName,
            "manaCost": existing.manaCost,
            "typeLine": existing.typeLine,
            "imageUri": safe_image_uri(existing.imageUri),
            "setCode": existing.setCode,
            "collectorNumber": existing.collectorNumber,
            "oracleText": oracle_text,
            "nameEs": getattr(existing, "nameEs", None),
            "typeLineEs": getattr(existing, "typeLineEs", None),
            "oracleTextEs": getattr(existing, "oracleTextEs", None),
            "flavorTextEs": getattr(existing, "flavorTextEs", None),
        }

    @staticmethod
    async def _search_catalog_rows(query: str, limit: int) -> List[Any]:
        needle = fold_search_text(query)
        if not needle or limit <= 0:
            return []
        try:
            return await db.query_raw(_CATALOG_SEARCH_SQL, needle, limit)
        except Exception as e:
            logger.error(f"Error searching local card catalog: {e}")
            return []

    @staticmethod
    async def search_cards_local(query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Searches the local CardCatalog database first. Matches ignore case,
        accents and punctuation, so "yshtola" finds "Y'shtola, Night's Blessed".
        """
        records = await ScryfallService._search_catalog_rows(query, limit)
        if not records:
            return []

        printings_by_catalog: Dict[str, Any] = {}
        try:
            printings = await db.cardprinting.find_many(
                where={"catalogId": {"in": [_field(record, "id") for record in records if _field(record, "id")]}},
                order={"releasedAt": "desc"},
            )
            for printing in printings:
                printings_by_catalog.setdefault(printing.catalogId, printing)
        except Exception:
            logger.warning("Could not resolve local image variants for search results", exc_info=True)

        results: List[Dict[str, Any]] = []
        for record in records:
            record_id = _field(record, "id")
            image_uris: Dict[str, str] = {}
            printing = printings_by_catalog.get(record_id)
            normal = safe_image_uri(getattr(printing, "imageUri", None)) or safe_image_uri(
                _field(record, "image_uri", "imageUri")
            )
            small = safe_image_uri(getattr(printing, "imageUriSmall", None)) or normal
            large = safe_image_uri(getattr(printing, "imageUriLarge", None)) or normal
            if normal:
                image_uris = {
                    "small": small,
                    "normal": normal,
                    "large": large,
                    "art_crop": normal,
                }
            results.append({
                "id": record_id,
                "name": _field(record, "name"),
                "mana_cost": _field(record, "mana_cost", "manaCost"),
                "type_line": _field(record, "type_line", "typeLine"),
                "set": _field(record, "set_code", "setCode"),
                "collector_number": _field(record, "collector_number", "collectorNumber"),
                "image_uris": image_uris,
            })
        return results

    @staticmethod
    async def search_cards(query: str, page: int = 1) -> Dict[str, Any]:
        if not query or not query.strip():
            return {"total_cards": 0, "has_more": False, "data": []}

        # Database-first: hit the local CardCatalog cache before Scryfall.
        local_results = await ScryfallService.search_cards_local(query.strip())
        local_results = [c for c in local_results if is_playable_card(c)]
        local_results.sort(key=lambda c: (score_card_match(c.get("name"), query), c.get("name") or ""))

        top_score = score_card_match(local_results[0].get("name"), query) if local_results else 99
        # Return local directly if we have a top exact/word match
        if local_results and top_score <= 1:
            return {
                "total_cards": len(local_results),
                "has_more": False,
                "data": local_results,
                "source": "local",
            }

        # Fallback to or supplement with live Scryfall API (filtering out Arena and digital cards)
        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=10.0) as client:
            try:
                scryfall_q = f"({query.strip()}) game:paper -is:digital -set_type:alchemy"
                res = await client.get(f"{SCRYFALL_BASE}/cards/search", params={"q": scryfall_q, "page": page})
                if res.status_code == 404:
                    return {"total_cards": len(local_results), "has_more": False, "data": local_results}
                res.raise_for_status()
                data = res.json()
                clean_cards = [c for c in data.get("data", []) if is_playable_card(c)]
                seen = {normalize_card_name(c["name"]) for c in clean_cards}
                clean_cards.extend(c for c in local_results if normalize_card_name(c["name"]) not in seen)
                clean_cards.sort(key=lambda c: (score_card_match(c.get("name"), query), c.get("name") or ""))
                return {
                    "total_cards": len(clean_cards) if len(clean_cards) != len(data.get("data", [])) else data.get("total_cards", 0),
                    "has_more": bool(data.get("has_more", False)),
                    "data": strip_scryfall_image_uris(clean_cards),
                }
            except Exception as e:
                logger.error(f"Error searching cards on Scryfall: {e}")
                return {"total_cards": len(local_results), "has_more": False, "data": local_results}

    @staticmethod
    async def autocomplete_local(query: str, limit: int = 10) -> List[str]:
        """Names matching the query from the local CardCatalog, ignoring punctuation."""
        records = await ScryfallService._search_catalog_rows(query, limit)
        return [
            name for name in (_field(record, "name") for record in records)
            if name and not name.startswith(("A-", "a-"))
        ]

    @staticmethod
    async def autocomplete_cards(query: str) -> List[str]:
        if not query or len(query.strip()) < 2:
            return []

        local_names = await ScryfallService.autocomplete_local(query.strip())
        if local_names:
            return [name for name in local_names if not name.startswith(("A-", "a-"))]

        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=5.0) as client:
            try:
                res = await client.get(f"{SCRYFALL_BASE}/cards/autocomplete", params={"q": query.strip()})
                if res.status_code != 200:
                    return []
                data = res.json()
                return [name for name in data.get("data", []) if not name.startswith(("A-", "a-"))]
            except Exception as e:
                logger.error(f"Error autocompleting cards: {e}")
                return []

    @staticmethod
    async def get_card_named(name: str, exact: bool = False) -> Optional[Dict[str, Any]]:
        if not name or not name.strip():
            return None

        clean_name = name.strip()
        if clean_name.startswith(("A-", "a-")):
            return None

        param_key = "exact" if exact else "fuzzy"
        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=8.0) as client:
            try:
                res = await client.get(f"{SCRYFALL_BASE}/cards/named", params={param_key: clean_name})
                if res.status_code == 200:
                    card_data = res.json()
                    if is_playable_card(card_data):
                        return card_data

                # If named returned a digital/unplayable printing or 404, search for paper printings
                search_q = f'!\"{clean_name}\" game:paper -is:digital -set_type:alchemy' if exact else f'({clean_name}) game:paper -is:digital -set_type:alchemy'
                search_res = await client.get(
                    f"{SCRYFALL_BASE}/cards/search",
                    params={"q": search_q, "order": "released", "dir": "desc"},
                )
                if search_res.status_code == 200:
                    search_data = search_res.json()
                    playable = [c for c in search_data.get("data", []) if is_playable_card(c)]
                    if playable:
                        return playable[0]
                return None
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
                        results.extend([c for c in data.get("data", []) if is_playable_card(c)])
                except Exception as e:
                    logger.error(f"Error in Scryfall bulk collection resolve: {e}")

        return results

    @staticmethod
    async def get_or_resolve_catalog_card(name: str) -> Optional[Dict[str, Any]]:
        """
        Checks CardCatalog in database, if not found queries Scryfall and saves to CardCatalog.
        """
        if not name or name.strip().startswith(("A-", "a-")):
            return None

        norm = normalize_card_name(name)
        if not norm:
            return None

        # Read the local catalog first. Only the worker's fallback below may
        # contact Scryfall when the card is absent.
        existing = await ScryfallService.get_catalog_card(name)
        if existing:
            return existing

        # Query Scryfall with an EXACT name match. A fuzzy match may silently
        # resolve a misspelled or home-made name to an unrelated real card
        # (e.g. "Slash Clone" -> Steelbane Hydra), downloading the wrong art
        # and never surfacing the typo to the user.
        card_data = await ScryfallService.get_card_named(name, exact=True)
        if not card_data or not is_playable_card(card_data):
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
        image_uri = safe_image_uri(image_uri)

        # Check if already in CardCatalog by normalizedName, name, or id before attempting to create
        existing_record = await db.cardcatalog.find_first(
            where={
                "OR": [
                    {"normalizedName": card_norm},
                    {"name": card_name},
                    {"id": card_id},
                ]
            }
        )
        if existing_record:
            return {
                "id": existing_record.id,
                "name": existing_record.name,
                "normalizedName": existing_record.normalizedName,
                "manaCost": existing_record.manaCost,
                "typeLine": existing_record.typeLine,
                "imageUri": safe_image_uri(existing_record.imageUri),
                "setCode": existing_record.setCode,
                "collectorNumber": existing_record.collectorNumber,
            }

        try:
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
        except Exception:
            created = await db.cardcatalog.find_first(
                where={
                    "OR": [
                        {"normalizedName": card_norm},
                        {"name": card_name},
                        {"id": card_id},
                    ]
                }
            )
            if not created:
                raise

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

    @staticmethod
    def _translate_type_line(type_line: Optional[str]) -> Optional[str]:
        if not type_line:
            return None
        parts = type_line.split(" — ")
        main_types = parts[0]
        subtypes = parts[1] if len(parts) > 1 else None

        main_dict = {
            "Legendary": "Legendario/a",
            "Basic": "Básica",
            "Snow": "Nevada",
            "World": "Mundo",
            "Artifact": "Artefacto",
            "Creature": "Criatura",
            "Enchantment": "Encantamiento",
            "Instant": "Instantáneo",
            "Sorcery": "Conjuro",
            "Land": "Tierra",
            "Planeswalker": "Planeswalker",
            "Battle": "Batalla",
            "Kindred": "Familiar",
            "Tribal": "Tribal",
        }
        for eng, esp in main_dict.items():
            main_types = main_types.replace(eng, esp)

        subtype_dict = {
            "Forest": "Bosque",
            "Island": "Isla",
            "Mountain": "Montaña",
            "Plains": "Llanura",
            "Swamp": "Pantano",
            "Dragon": "Dragón",
            "Elf": "Elfo",
            "Goblin": "Trasgo",
            "Human": "Humano",
            "Knight": "Caballero",
            "Wizard": "Hechicero",
            "Angel": "Ángel",
            "Demon": "Demonio",
            "Zombie": "Zombi",
            "Vampire": "Vampiro",
            "Cleric": "Clérigo",
            "Warrior": "Guerrero",
            "Soldier": "Soldado",
            "Rogue": "Bribón",
            "Shaman": "Chamán",
            "Druid": "Druida",
            "Beast": "Bestia",
            "Bird": "Ave",
            "Cat": "Felino",
            "Dog": "Perro",
            "Fish": "Pez",
            "Insect": "Insecto",
            "Snake": "Serpiente",
            "Spider": "Araña",
            "Wolf": "Lobo",
            "Wurm": "Sierpe",
            "Equipment": "Equipo",
            "Aura": "Aura",
            "Vehicle": "Vehículo",
            "Saga": "Saga",
            "Siege": "Asedio",
            "Cartouche": "Cartucho",
            "Curse": "Maldición",
            "Rune": "Runa",
            "Shrine": "Santuario",
            "Class": "Clase",
        }
        if subtypes:
            sub_words = subtypes.split(" ")
            translated_subs = [subtype_dict.get(w, w) for w in sub_words]
            return f"{main_types} — {' '.join(translated_subs)}"
        return main_types

    @staticmethod
    async def _get_cached_card_details(
        card_id: Optional[str], name: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Reads an already-hydrated localized card detail from CardCatalog.

        The UI often opens the detail dialog with a *printing* id (from movers,
        collection rows, etc.). Resolve printing → catalog, then fall back to
        normalized name so we do not miss a warm cache and block on enrichment.
        """
        try:
            record = None
            if card_id:
                record = await db.cardcatalog.find_unique(where={"id": card_id})
                if record is None:
                    printing = await db.cardprinting.find_unique(where={"id": card_id})
                    catalog_id = getattr(printing, "catalogId", None) if printing else None
                    if catalog_id:
                        record = await db.cardcatalog.find_unique(where={"id": catalog_id})
            if record is None and name:
                normalized_name = normalize_card_name(name)
                if normalized_name:
                    record = await db.cardcatalog.find_unique(
                        where={"normalizedName": normalized_name}
                    )
            details = getattr(record, "detailsEs", None) if record else None
            if isinstance(details, dict):
                return details
        except Exception as exc:
            # A cache failure must not prevent the live fallback.
            logger.warning("Unable to read card details cache: %s", exc)
        return None

    @staticmethod
    async def _cache_card_details(details: Dict[str, Any]) -> None:
        """Persists the complete localized response for future detail requests."""
        card_id = details.get("id")
        card_name = details.get("name")
        normalized_name = normalize_card_name(card_name or "")
        if not card_id or not card_name or not normalized_name:
            return

        image_uris = details.get("image_uris") or {}
        image_uri = safe_image_uri(image_uris.get("normal") or image_uris.get("large"))
        try:
            await db.cardcatalog.upsert(
                where={"id": card_id},
                data={
                    "create": {
                        "id": card_id,
                        "name": card_name,
                        "normalizedName": normalized_name,
                        "manaCost": details.get("mana_cost"),
                        "typeLine": details.get("type_line"),
                        "oracleTextEs": details.get("oracle_text_es"),
                        "nameEs": details.get("name_es"),
                        "typeLineEs": details.get("type_line_es"),
                        "flavorTextEs": details.get("flavor_text_es"),
                        "imageUri": image_uri,
                        "setCode": details.get("set", "").lower() or None,
                        "collectorNumber": details.get("collector_number"),
                        "detailsEs": Json(details),
                        "detailsUpdatedAt": datetime.now(timezone.utc),
                    },
                    "update": {
                        "manaCost": details.get("mana_cost"),
                        "typeLine": details.get("type_line"),
                        "oracleTextEs": details.get("oracle_text_es"),
                        "nameEs": details.get("name_es"),
                        "typeLineEs": details.get("type_line_es"),
                        "flavorTextEs": details.get("flavor_text_es"),
                        "detailsEs": Json(details),
                        "detailsUpdatedAt": datetime.now(timezone.utc),
                    },
                },
            )
        except Exception as exc:
            # The live response remains valid even if persistence is unavailable.
            logger.warning("Unable to cache card details for %s: %s", card_id, exc)

    @staticmethod
    def _finalize_cached_details(details: Dict[str, Any], key: Any) -> Dict[str, Any]:
        """
        Completes the stable Swift API contract on top of worker-produced
        localized details that do not expose a translation flag.
        """
        details.setdefault("name_es", details.get("name", ""))
        details.setdefault("rarity_es", details.get("rarity", ""))
        details.setdefault("has_spanish_print", False)
        details.setdefault("cmc", None)
        details.setdefault("prices", None)
        legalities = details.get("legalities")
        if isinstance(legalities, dict):
            details["legalities"] = [
                {
                    "format": fmt,
                    "format_name": fmt.replace("_", " ").title(),
                    "status": value,
                    "status_es": value,
                }
                for fmt, value in legalities.items()
            ]
        return details

    @staticmethod
    async def enrich_card_via_worker(
        card_id: Optional[str], name: Optional[str]
    ) -> Optional[str]:
        """
        Asks the worker to hydrate a missing card fully (Scryfall data,
        images, prices and rulings) and blocks until it completes. Returns the
        catalog id when the card was enriched, else None.
        """
        payload: Dict[str, str] = {}
        if card_id:
            payload["id"] = card_id
        if name:
            payload["name"] = name
        if not payload:
            return None
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                res = await client.post(f"{WORKER_URL}/enrich-card", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") == "enriched":
                        return (data.get("card") or {}).get("id") or (card_id or "")
        except Exception as exc:
            logger.warning("Worker on-demand enrichment unavailable: %s", exc)
        return None

    @staticmethod
    async def get_card_details_es(
        card_id: Optional[str] = None,
        name: Optional[str] = None,
        set_code: Optional[str] = None,
        collector_number: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches complete card data with official Spanish print resolution and
        localization. When the card is missing from the database, delegates the
        full hydration (Scryfall data, images, prices and rulings) to the worker
        and waits for it to finish. Only falls back to a direct Scryfall call
        when the worker is unreachable.

        Any already-persisted detailsEs payload is returned immediately so the
        detail dialog never blocks on a slow enrich-card round-trip.
        """
        cached = await ScryfallService._get_cached_card_details(card_id, name)
        if cached:
            logger.info("Card details cache hit for %s", card_id or name)
            return ScryfallService._finalize_cached_details(cached, card_id or name)

        # Card not in the database: delegate full hydration to the worker.
        enriched_id = await ScryfallService.enrich_card_via_worker(card_id, name)
        if enriched_id:
            cached = await ScryfallService._get_cached_card_details(enriched_id, name)
            if cached:
                logger.info(
                    "Card details hydrated on-demand by the worker for %s",
                    card_id or name,
                )
                return ScryfallService._finalize_cached_details(cached, enriched_id)

        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=12.0) as client:
            card_data: Optional[Dict[str, Any]] = None

            # 1. Fetch base card
            try:
                if card_id:
                    res = await client.get(f"{SCRYFALL_BASE}/cards/{card_id}")
                    if res.status_code == 200:
                        card_data = res.json()
                if not card_data and name:
                    res = await client.get(f"{SCRYFALL_BASE}/cards/named", params={"fuzzy": name.strip()})
                    if res.status_code == 200:
                        card_data = res.json()
            except Exception as e:
                logger.error(f"Error fetching base card {card_id or name}: {e}")

            if not card_data:
                return None

            c_set = card_data.get("set", set_code)
            c_num = card_data.get("collector_number", collector_number)
            c_name = card_data.get("name")

            # 2. Check for Spanish print
            es_card: Optional[Dict[str, Any]] = None
            if card_data.get("lang") == "es":
                es_card = card_data
            else:
                # Try exact set & collector number /es
                if c_set and c_num:
                    try:
                        res_es = await client.get(f"{SCRYFALL_BASE}/cards/{c_set}/{c_num}/es")
                        if res_es.status_code == 200:
                            es_card = res_es.json()
                    except Exception:
                        pass

                # Fallback: search for any Spanish printing of this card
                if not es_card and c_name:
                    try:
                        search_q = f'!"{c_name}" lang:es game:paper -is:digital -set_type:alchemy'
                        res_search = await client.get(
                            f"{SCRYFALL_BASE}/cards/search",
                            params={"q": search_q, "order": "released", "dir": "desc"},
                        )
                        if res_search.status_code == 200:
                            s_data = res_search.json()
                            candidates = [c for c in s_data.get("data", []) if is_playable_card(c)]
                            # Best match: same name and printed_name exists
                            best = None
                            for c in candidates:
                                if c.get("printed_name") and c.get("name") == c_name:
                                    best = c
                                    break
                            if not best:
                                for c in candidates:
                                    if c.get("printed_name"):
                                        best = c
                                        break
                            if not best and candidates:
                                best = candidates[0]
                            es_card = best
                    except Exception as e:
                        logger.error(f"Error searching Spanish print for {c_name}: {e}")

            # 3. Extract Spanish and English values
            name_es = None
            type_line_es = None
            oracle_text_es = None
            flavor_text_es = None

            if es_card:
                name_es = es_card.get("printed_name") or es_card.get("name")
                type_line_es = es_card.get("printed_type_line")
                oracle_text_es = es_card.get("printed_text") or es_card.get("oracle_text")
                flavor_text_es = es_card.get("flavor_text")

            if not name_es:
                name_es = card_data.get("name")
            if not type_line_es:
                type_line_es = ScryfallService._translate_type_line(card_data.get("type_line"))
            else:
                type_line_es = ScryfallService._translate_type_line(type_line_es)
            if not oracle_text_es:
                oracle_text_es = card_data.get("oracle_text")
            if not flavor_text_es:
                flavor_text_es = card_data.get("flavor_text")

            # 4. Rarities & Legalities translation
            rarity_map = {
                "common": "Común",
                "uncommon": "Infrecuente",
                "rare": "Rara",
                "mythic": "Rara mítica",
                "special": "Especial",
                "bonus": "Bonus",
            }
            rarity_raw = card_data.get("rarity", "common").lower()
            rarity_es = rarity_map.get(rarity_raw, rarity_raw.capitalize())

            legality_status_map = {
                "legal": "Legal",
                "not_legal": "No legal",
                "banned": "Prohibida",
                "restricted": "Restringida",
            }
            format_names_map = {
                "commander": "Commander / EDH",
                "modern": "Modern",
                "standard": "Estándar",
                "pioneer": "Pioneer",
                "legacy": "Legacy",
                "vintage": "Vintage",
                "pauper": "Pauper",
                "brawl": "Brawl",
                "historic": "Histórico",
                "timeless": "Timeless",
                "alchemy": "Alchemy",
                "duel": "Duelo (1v1)",
                "premodern": "Premodern",
                "oathbreaker": "Oathbreaker",
            }
            raw_legalities = card_data.get("legalities", {})
            legalities_es = []
            for fmt_key, fmt_label in format_names_map.items():
                if fmt_key in raw_legalities:
                    status_raw = raw_legalities[fmt_key]
                    legalities_es.append({
                        "format": fmt_key,
                        "format_name": fmt_label,
                        "status": status_raw,
                        "status_es": legality_status_map.get(status_raw, status_raw),
                    })

            # 5. Dual faces handling
            card_faces = []
            if "card_faces" in card_data and card_data["card_faces"]:
                es_faces = (es_card.get("card_faces") or []) if es_card else []
                for idx, face in enumerate(card_data["card_faces"]):
                    es_face = es_faces[idx] if idx < len(es_faces) else None
                    face_name_es = (
                        (es_face.get("printed_name") or es_face.get("name"))
                        if es_face
                        else face.get("name")
                    )
                    face_type_es = (
                        (es_face.get("printed_type_line") or es_face.get("type_line"))
                        if es_face
                        else ScryfallService._translate_type_line(face.get("type_line"))
                    )
                    face_text_es = (
                        (es_face.get("printed_text") or es_face.get("oracle_text"))
                        if es_face
                        else face.get("oracle_text")
                    )
                    card_faces.append({
                        "name": face.get("name"),
                        "name_es": face_name_es,
                        "mana_cost": face.get("mana_cost"),
                        "type_line": face.get("type_line"),
                        "type_line_es": face_type_es,
                        "oracle_text": face.get("oracle_text"),
                        "oracle_text_es": face_text_es,
                        "flavor_text_es": (es_face.get("flavor_text") if es_face else face.get("flavor_text")),
                        "power": face.get("power"),
                        "toughness": face.get("toughness"),
                        "loyalty": face.get("loyalty"),
                        "defense": face.get("defense"),
                        "image_uris": face.get("image_uris"),
                    })

            # 6. Images fallback (check base card then es_card)
            image_uris = card_data.get("image_uris")
            if not image_uris and es_card and es_card.get("image_uris"):
                image_uris = es_card.get("image_uris")

            prices = card_data.get("prices", {})

            details = {
                "id": card_data.get("id"),
                "name": card_data.get("name"),
                "name_es": name_es,
                "mana_cost": card_data.get("mana_cost"),
                "cmc": card_data.get("cmc"),
                "type_line": card_data.get("type_line"),
                "type_line_es": type_line_es,
                "oracle_text": card_data.get("oracle_text"),
                "oracle_text_es": oracle_text_es,
                "flavor_text": card_data.get("flavor_text"),
                "flavor_text_es": flavor_text_es,
                "power": card_data.get("power"),
                "toughness": card_data.get("toughness"),
                "loyalty": card_data.get("loyalty"),
                "defense": card_data.get("defense"),
                "rarity": card_data.get("rarity"),
                "rarity_es": rarity_es,
                "set": card_data.get("set", "").upper(),
                "set_name": card_data.get("set_name"),
                "collector_number": card_data.get("collector_number"),
                "artist": card_data.get("artist"),
                "image_uris": image_uris,
                "card_faces": card_faces,
                "legalities": legalities_es,
                "prices": {
                    "eur": prices.get("eur"),
                    "eur_foil": prices.get("eur_foil"),
                    "usd": prices.get("usd"),
                    "usd_foil": prices.get("usd_foil"),
                },
            }
            await ScryfallService._cache_card_details(details)
            return details
