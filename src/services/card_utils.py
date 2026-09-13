from typing import Optional, Dict, Any, List
import re

NON_PLAYABLE_LAYOUTS = {
    "art_series",
    "token",
    "double_faced_token",
    "emblem",
    "front_card",
    "minigame",
}

NON_PLAYABLE_SET_TYPES = {
    "memorabilia",
    "token",
}

NON_PLAYABLE_TYPES = {
    "card",
    "card // card",
}

def normalize_card_name(name: Optional[str]) -> str:
    """
    Normalizes a card name for edition-agnostic matching:
    - Lowercase
    - Strip outer whitespace
    - Normalize internal whitespace
    - Keep only the front face for split / dual face cards (" // ")
    """
    if not name:
        return ""
    cleaned = name.strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if " // " in cleaned:
        cleaned = cleaned.split(" // ")[0]
    return cleaned

def is_art_card(card: Optional[Dict[str, Any]]) -> bool:
    """Returns True if the given Scryfall card payload represents an art card or memorabilia."""
    if not card or not isinstance(card, dict):
        return False
    layout = (card.get("layout") or "").strip().lower()
    if layout in ("art_series", "front_card"):
        return True
    set_type = (card.get("set_type") or "").strip().lower()
    if set_type == "memorabilia":
        return True
    set_code = (card.get("set") or "").strip().lower()
    if len(set_code) == 4 and set_code.startswith("a") and set_type in ("memorabilia", "unknown", ""):
        return True
    type_line = (card.get("type_line") or "").strip().lower()
    if type_line in NON_PLAYABLE_TYPES or type_line.startswith("card // card"):
        return True
    for face in card.get("card_faces") or []:
        face_type = (face.get("type_line") or "").strip().lower()
        if face_type in NON_PLAYABLE_TYPES or face_type.startswith("card // card"):
            return True
    return False

def is_playable_card(card: Optional[Dict[str, Any]]) -> bool:
    """Returns True if the given Scryfall card payload is a real, playable MTG card."""
    if not card or not isinstance(card, dict):
        return False
    if is_art_card(card):
        return False
    layout = (card.get("layout") or "").strip().lower()
    if layout in NON_PLAYABLE_LAYOUTS:
        return False
    set_type = (card.get("set_type") or "").strip().lower()
    if set_type in NON_PLAYABLE_SET_TYPES:
        return False
    type_line = (card.get("type_line") or "").strip().lower()
    if type_line in NON_PLAYABLE_TYPES:
        return False
    return True


# WUBRG ordering used by MTG / EDHREC for color identity keys.
COLOR_ORDER = ["W", "U", "B", "R", "G"]

def extract_colors_from_mana_cost(mana_cost: Optional[str]) -> List[str]:
    """
    Returns the WUBRG colors present in a mana cost string such as "{2}{W}{U}".
    Hybrid symbols ({W/U}, {2/W}, {W/P}...) contribute every color they contain.
    Returns colors ordered canonically (W, U, B, R, G).
    """
    if not mana_cost:
        return []
    found = set()
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost):
        upper = symbol.strip().upper()
        if upper in COLOR_ORDER:
            found.add(upper)
            continue
        # Hybrid / Phyrexian symbols like {W/U}, {2/W} or {W/P}.
        for color in COLOR_ORDER:
            if color in upper:
                found.add(color)
    return [c for c in COLOR_ORDER if c in found]

def combine_colors_from_mana_costs(mana_costs: List[Optional[str]]) -> List[str]:
    """Union of colors across several cards, ordered canonically (WUBRG)."""
    combined = set()
    for mana_cost in mana_costs:
        combined.update(extract_colors_from_mana_cost(mana_cost))
    return [c for c in COLOR_ORDER if c in combined]

CARD_TYPE_CATEGORIES = [
    "creatures",
    "planeswalkers",
    "instants",
    "sorceries",
    "artifacts",
    "enchantments",
    "battles",
    "lands",
    "other",
]

CARD_TYPE_GROUPS = {
    "creatures": {"key": "creatures", "label": "Criaturas", "order": 1},
    "planeswalkers": {"key": "planeswalkers", "label": "Planeswalkers", "order": 2},
    "instants": {"key": "instants", "label": "Instantáneos", "order": 3},
    "sorceries": {"key": "sorceries", "label": "Conjuros", "order": 4},
    "artifacts": {"key": "artifacts", "label": "Artefactos", "order": 5},
    "enchantments": {"key": "enchantments", "label": "Encantamientos", "order": 6},
    "battles": {"key": "battles", "label": "Batallas", "order": 7},
    "lands": {"key": "lands", "label": "Tierras", "order": 8},
    "other": {"key": "other", "label": "Otras Cartas", "order": 9},
}

# The five WUBRG basic lands plus the niche Basic Land supertype cards
# (Wastes, and snow-covered variants of the WUBRG basics).
BASIC_LAND_NAMES = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
}

def is_basic_land(type_line: Optional[str] = None, card_name: Optional[str] = None) -> bool:
    """
    Returns whether a card is one of the generic basic lands (Plains, Island,
    Swamp, Mountain, Forest, Wastes and their snow-covered forms). Basic lands
    are excluded from deck completion metrics: they don't count in the
    numerator (owned) nor the denominator (total).
    """
    if type_line:
        lower = type_line.lower()
        if "basic land" in lower or "tierra básica" in lower:
            return True
    if card_name:
        return normalize_card_name(card_name) in BASIC_LAND_NAMES
    return False

def get_card_category(type_line: Optional[str] = None, card_name: Optional[str] = None) -> str:
    """
    Categorizes an MTG card based on its type_line, with intelligent fallback
    heuristics based on card name for basic and common lands when type_line is missing.
    Follows MTG convention where creature types take precedence.
    """
    if type_line:
        lower = type_line.lower()
        if "creature" in lower or "criatura" in lower:
            return "creatures"
        if "planeswalker" in lower:
            return "planeswalkers"
        if "instant" in lower or "instantáneo" in lower:
            return "instants"
        if "sorcery" in lower or "conjuro" in lower:
            return "sorceries"
        if "artifact" in lower or "artefacto" in lower:
            return "artifacts"
        if "enchantment" in lower or "encantamiento" in lower:
            return "enchantments"
        if "battle" in lower or "batalla" in lower:
            return "battles"
        if "land" in lower or "tierra" in lower:
            return "lands"

    if card_name:
        lower_name = card_name.strip().lower()
        # Basic lands
        if is_basic_land(type_line, card_name):
            return "lands"

        # Ubiquitous MTG lands
        land_keywords = [
            "command tower", "reliquary tower", "boilerworks", "sanctuary",
            "headquarters", "cliffs", "evolving wilds", "terramorphic expanse",
            "fabled passage", "prismatic vista", "city of brass", "mana confluence",
            "reflecting pool", "path of ancestry", "exotic orchard"
        ]
        if any(kw in lower_name for kw in land_keywords):
            return "lands"

    return "other"
