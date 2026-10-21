"""Pure ManaBox/Archidekt parsers adapted from worker_whatsapp/src/scraper.py.

Kept local because the API and WhatsApp bot are independently deployed repos.
No bot storage, settings, or background processes are imported by the API.
"""
import html
import json
import logging
import re
from typing import Any, Dict, List
import httpx

logger = logging.getLogger(__name__)

def parse_archidekt(resp: httpx.Response) -> List[Dict[str, Any]]:
    """
    Parser para mazos de Archidekt.
    Soporta HTML SSR (con __NEXT_DATA__) y respuestas JSON de la API.
    """
    content_type = resp.headers.get("Content-Type", "")

    # Caso 1: JSON directo
    if "application/json" in content_type or (resp.text and resp.text.lstrip().startswith("{")):
        try:
            data = resp.json()
            if "cardMap" in data:
                return _parse_archidekt_cardmap(data.get("cardMap", {}))
            if "cards" in data and isinstance(data["cards"], list):
                return _parse_archidekt_card_list(data["cards"])
            if "redux" in data and "deck" in data["redux"]:
                deck = data["redux"]["deck"]
                if "cardMap" in deck:
                    return _parse_archidekt_cardmap(deck.get("cardMap", {}))
        except Exception as exc:
            logger.warning("Fallo al parsear JSON de Archidekt: %s", exc)

    # Caso 2: HTML SSR con __NEXT_DATA__
    text = resp.text
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text)
    if m:
        try:
            next_data = json.loads(m.group(1))
            props = next_data.get("props", {})
            page_props = props.get("pageProps", {})
            deck = page_props.get("redux", {}).get("deck") or page_props.get("deck", {})
            if isinstance(deck, dict):
                card_map = deck.get("cardMap", {})
                if card_map:
                    return _parse_archidekt_cardmap(card_map)
                card_list = deck.get("cards", [])
                if card_list:
                    return _parse_archidekt_card_list(card_list)
        except Exception as exc:
            logger.warning("Fallo al parsear __NEXT_DATA__ de Archidekt: %s", exc)

    return []


def _parse_archidekt_cardmap(card_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for c in card_map.values():
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        set_code = c.get("setCode") or (c.get("edition", {}).get("editioncode") if isinstance(c.get("edition"), dict) else None)
        prices = c.get("prices", {})
        cm = prices.get("cm") or prices.get("cmFoil")
        tcg = prices.get("tcg") or prices.get("tcgFoil")
        price = f"{cm:.2f}" if cm and cm > 0 else (f"{tcg:.2f}" if tcg and tcg > 0 else None)
        currency = "EUR" if cm and cm > 0 else ("USD" if tcg and tcg > 0 else None)
        cards.append({
            "name": name,
            "set_code": set_code.upper() if set_code else None,
            "price": price,
            "currency": currency,
            "condition": "NM",
            "extra": {
                "quantity": c.get("qty", 1),
                "collector_number": str(c.get("collectorNumber")) if c.get("collectorNumber") else None,
                "rarity": c.get("rarity"),
                "categories": c.get("categories", []),
            },
        })
    return cards


def _parse_archidekt_card_list(card_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for item in card_list:
        if not isinstance(item, dict):
            continue
        c_info = item.get("card", {})
        oracle = c_info.get("oracleCard", {}) if isinstance(c_info, dict) else {}
        name = oracle.get("name") or c_info.get("displayName") or item.get("name")
        edition = c_info.get("edition", {})
        set_code = edition.get("editioncode") if isinstance(edition, dict) else c_info.get("setCode")
        prices = c_info.get("prices", {}) if isinstance(c_info, dict) else {}
        cm = prices.get("cm") or prices.get("cmfoil")
        tcg = prices.get("tcg") or prices.get("tcgfoil")
        price = f"{cm:.2f}" if cm and cm > 0 else (f"{tcg:.2f}" if tcg and tcg > 0 else None)
        currency = "EUR" if cm and cm > 0 else ("USD" if tcg and tcg > 0 else None)
        cards.append({
            "name": name,
            "set_code": set_code.upper() if set_code else None,
            "price": price,
            "currency": currency,
            "condition": "NM",
            "extra": {
                "quantity": item.get("quantity", 1),
                "collector_number": str(c_info.get("collectorNumber")) if c_info.get("collectorNumber") else None,
                "rarity": c_info.get("rarity"),
                "categories": item.get("categories", []),
            },
        })
    return cards


def parse_manabox(resp: httpx.Response) -> List[Dict[str, Any]]:
    """
    Parser para mazos y listas compartidas de ManaBox.
    Extrae la lista de cartas de los componentes Astro Island embebidos en el HTML.
    """
    cards: List[Dict[str, Any]] = []
    text = resp.text

    def unwrap(val):
        if isinstance(val, list) and len(val) == 2 and isinstance(val[0], int):
            return unwrap(val[1])
        if isinstance(val, dict):
            return {k: unwrap(v) for k, v in val.items()}
        if isinstance(val, list):
            return [unwrap(v) for v in val]
        return val

    for match in re.finditer(r'props="([^"]+)"', text):
        raw_props = html.unescape(match.group(1))
        if '"deck"' in raw_props or '"cards"' in raw_props:
            try:
                props = json.loads(raw_props)
            except Exception:
                continue

            unwrapped = unwrap(props)
            deck = unwrapped.get("deck", {})
            raw_cards = deck.get("cards", [])
            for c in raw_cards:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                set_id = c.get("setId")
                pricing = c.get("pricing", {})
                cm = pricing.get("cardmarket", {})
                tcg = pricing.get("tcgplayer", {})
                price = cm.get("value") if cm else (tcg.get("value") if tcg else None)
                currency = (
                    "EUR"
                    if cm and cm.get("value") is not None
                    else ("USD" if tcg and tcg.get("value") is not None else None)
                )

                cards.append({
                    "name": name,
                    "set_code": set_id.upper() if set_id else None,
                    "price": f"{price:.2f}" if price is not None else None,
                    "currency": currency,
                    "condition": "NM",
                    "extra": {
                        "quantity": c.get("quantity", 1),
                        "set_name": c.get("setName"),
                        "collector_number": c.get("collectorNumber"),
                        "rarity": c.get("rarity"),
                    },
                })
    return cards



def parse_moxfield(data: dict) -> List[Dict[str, Any]]:
    cards = []
    if not isinstance(data, dict):
        return cards
    boards = data.get("boards") or data
    for board in ("commanders", "companions", "mainboard", "sideboard", "maybeboard", "signatureSpells", "attractions"):
        entries = boards.get(board, {})
        entries = entries.get("cards", entries) if isinstance(entries, dict) else {}
        for key, item in entries.items():
            card = item.get("card", {})
            prices = card.get("prices", {})
            price = prices.get("eur") if prices.get("eur") is not None else prices.get("usd")
            cards.append({
                "name": card.get("name") or key,
                "set_code": card.get("set"),
                "price": str(price) if price is not None else None,
                "currency": "EUR" if prices.get("eur") is not None else "USD",
                "extra": {"quantity": item.get("quantity", 1), "board": board,
                          "collector_number": card.get("cn"), "price_is_reference": True},
            })
    return cards
