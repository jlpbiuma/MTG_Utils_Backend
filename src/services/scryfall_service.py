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
    async def get_card_details_es(
        card_id: Optional[str] = None,
        name: Optional[str] = None,
        set_code: Optional[str] = None,
        collector_number: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches complete card data with official Spanish print resolution and localization.
        """
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
                        search_q = f'!"{c_name}" lang:es'
                        res_search = await client.get(
                            f"{SCRYFALL_BASE}/cards/search",
                            params={"q": search_q, "order": "released", "dir": "desc"},
                        )
                        if res_search.status_code == 200:
                            s_data = res_search.json()
                            candidates = s_data.get("data", [])
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
            has_spanish_print = es_card is not None
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

            return {
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
                "has_spanish_print": has_spanish_print,
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

