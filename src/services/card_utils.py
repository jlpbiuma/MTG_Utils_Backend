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
    if "/" in cleaned:
        cleaned = re.sub(r"\s*/+\s*", " // ", cleaned)
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

# Magic Arena and digital-only set codes
ARENA_AND_DIGITAL_SET_CODES = {
    # Arena beginner / introductory
    "ana", "anb", "xana", "oana", "ajmp", "pana", "parl",
    # Arena anthologies & remastered
    "ea1", "ea2", "ea3", "ha1", "ha2", "ha3", "ha4", "ha5", "ha6", "ha7",
    "aa1", "aa2", "aa3", "aa4", "pa1", "hbg", "j21", "sir", "sis", "akr", "klr", "pio", "om1", "omb",
    # MTGO-only and digital exclusives
    "me1", "me2", "me3", "me4", "vma", "tpr", "td0", "td2", "pz1", "pz2", "prm", "pmoa", "psdg", "past",
}

def is_arena_or_digital_set_code(set_code: Optional[str]) -> bool:
    """Returns True if the set code corresponds to an Arena, Alchemy, or digital-only set."""
    if not set_code:
        return False
    code = set_code.strip().lower()
    if code in ARENA_AND_DIGITAL_SET_CODES:
        return True
    # Alchemy sets: 'y' followed by 2 or 3 alphanumeric characters (e.g. y22, yone, ydmu, yblb, ydft)
    if re.match(r"^y[0-9a-z]{2,3}$", code):
        return True
    return False

def is_digital_or_arena_card(card: Optional[Dict[str, Any]]) -> bool:
    """Returns True if the card represents an MTG Arena, Alchemy, or digital-only card or printing."""
    if not card or not isinstance(card, dict):
        return False

    # 1. Official Alchemy rebalanced card name or collector number
    name = (card.get("name") or "").strip()
    if name.startswith(("A-", "a-")):
        return True
    for face in card.get("card_faces") or []:
        if (face.get("name") or "").strip().startswith(("A-", "a-")):
            return True

    collector_num = str(card.get("collector_number") or "").strip()
    if collector_num.startswith(("A-", "a-")):
        return True

    # 2. Digital flag
    if card.get("digital") is True:
        return True

    # 3. Games availability (paper must be included for physical cards)
    games = card.get("games")
    if games is not None and isinstance(games, list) and "paper" not in games:
        return True

    # 4. Arena security stamp or Alchemy set_type/layout
    if card.get("security_stamp") == "arena":
        return True
    if (card.get("set_type") or "").strip().lower() == "alchemy":
        return True
    if (card.get("layout") or "").strip().lower() == "alchemy":
        return True

    # 5. Set code check
    set_code = (card.get("set") or "").strip().lower()
    if is_arena_or_digital_set_code(set_code):
        return True

    return False

def is_catalog_record_playable(record: Any) -> bool:
    """
    Returns True if a local CardCatalog record represents a real, playable MTG
    card. The catalog row does not store Scryfall layout/set_type, so the art
    card heuristics are reproduced from the fields it does carry: type_line,
    name, collector_number and set_code. Blocks Arena Alchemy rebalances (A-*),
    tokens, emblems, and digital/art sets.
    """
    if record is None:
        return False

    name = (getattr(record, "name", None) or "").strip()
    if name.startswith(("A-", "a-")):
        return False

    collector_number = str(
        getattr(record, "collectorNumber", None)
        or getattr(record, "collector_number", None)
        or ""
    ).strip()
    if collector_number.startswith(("A-", "a-")):
        return False

    type_line = (getattr(record, "typeLine", None) or getattr(record, "type_line", None) or "").strip().lower()
    if type_line in NON_PLAYABLE_TYPES or type_line.startswith("card // card"):
        return False
    if "token" in type_line or "emblem" in type_line:
        return False

    set_code = (getattr(record, "setCode", None) or getattr(record, "set_code", None) or "").strip().lower()
    # Memorabilia / art-series sets use 4-letter codes starting with "a"
    # (e.g. atmt, af30). Keep the heuristic aligned with is_art_card.
    if len(set_code) == 4 and set_code.startswith("a"):
        return False
    if is_arena_or_digital_set_code(set_code):
        return False

    return True

def extract_cmc(mana_cost: Optional[str]) -> int:
    """
    Calculates Converted Mana Cost (CMC / Mana Value) from a mana string like
    "{2}{U}{B}". Mirrors the frontend implementation.
    """
    if not mana_cost:
        return 0
    cmc = 0
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost):
        inner = symbol.strip()
        try:
            cmc += int(inner)
        except ValueError:
            if inner not in ("X", "Y", "Z"):
                cmc += 1
    return cmc

def is_playable_card(card: Optional[Dict[str, Any]]) -> bool:
    """Returns True if the given Scryfall card payload is a real, playable paper MTG card."""
    if not card or not isinstance(card, dict):
        return False
    if is_art_card(card):
        return False
    if is_digital_or_arena_card(card):
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


def is_non_playable_set(set_code: Optional[str], set_type: Optional[str], is_digital: bool = False) -> bool:
    """True for art-series, memorabilia, token, Arena, Alchemy, or digital sets."""
    if is_digital:
        return True
    st = (set_type or "").strip().lower()
    if st in ("alchemy", "memorabilia", "token"):
        return True
    code = (set_code or "").strip().lower()
    if len(code) == 4 and code.startswith("a"):
        return True
    if is_arena_or_digital_set_code(code):
        return True
    return False


def is_non_playable_catalog_fields(
    name: Optional[str] = None,
    type_line: Optional[str] = None,
    set_code: Optional[str] = None,
    collector_number: Optional[str] = None,
) -> bool:
    """True when catalog-like fields describe art, tokens, or digital/Alchemy cards."""
    n = (name or "").strip()
    if n.startswith(("A-", "a-")):
        return True
    cn = str(collector_number or "").strip()
    if cn.startswith(("A-", "a-")):
        return True
    tl = (type_line or "").strip().lower()
    if tl in NON_PLAYABLE_TYPES or tl.startswith("card // card"):
        return True
    if "token" in tl or "emblem" in tl:
        return True
    code = (set_code or "").strip().lower()
    if len(code) == 4 and code.startswith("a"):
        return True
    if is_arena_or_digital_set_code(code):
        return True
    return False


# WUBRG ordering used by MTG / EDHREC for color identity keys.
COLOR_ORDER = ["W", "U", "B", "R", "G"]

def extract_colors_from_mana_cost(mana_cost: Optional[str]) -> List[str]:
    """
    Returns the WUBRG colors present in a mana cost string such as "{2}{W}{U}".
    Hybrid symbols ({W/U}, {2/W}, {W/P}...) contribute every color they contain.
    Returns colors ordered canonically (W, U, B, R, G).
    """
    if not mana_cost or not isinstance(mana_cost, str):
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


def colors_by_deck(deck_cards: List[Any]) -> Dict[str, List[str]]:
    """Color identity of each deck, from the mana costs of its cards."""
    costs: Dict[str, List[Optional[str]]] = {}
    for card in deck_cards or []:
        deck_id = getattr(card, "deckId", None)
        if not deck_id:
            continue
        costs.setdefault(deck_id, []).append(getattr(card, "manaCost", None))
    return {
        deck_id: combine_colors_from_mana_costs(mana_costs)
        for deck_id, mana_costs in costs.items()
    }

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
# (Wastes, snow-covered variants, Spanish translations, and plurals).
BASIC_LAND_NAMES = {
    # English
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
    # English plurals
    "islands", "swamps", "mountains", "forests",
    # Spanish
    "llanura", "isla", "pantano", "montaña", "montana", "bosque", "yermo", "yermos",
    # Spanish plurals
    "llanuras", "islas", "pantanos", "montañas", "montanas", "bosques",
    # Spanish snow-covered
    "llanura nevada", "llanuras nevadas", "llanura cubierta de nieve",
    "isla nevada", "islas nevadas", "isla cubierta de nieve",
    "pantano nevado", "pantanos nevados", "pantano cubierto de nieve",
    "montaña nevada", "montañas nevadas", "montana nevada", "montanas nevadas",
    "montaña cubierta de nieve", "montana cubierta de nieve",
    "bosque nevado", "bosques nevados", "bosque cubierto de nieve",
    "yermo nevado", "yermos nevados", "yermo cubierto de nieve",
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
        if (
            "basic land" in lower
            or "tierra básica" in lower
            or "tierra basica" in lower
            or ("basic" in lower and "land" in lower)
            or ("básica" in lower and "tierra" in lower)
            or ("basica" in lower and "tierra" in lower)
        ):
            return True
    if card_name:
        norm = normalize_card_name(card_name)
        if norm in BASIC_LAND_NAMES:
            return True
        for b in BASIC_LAND_NAMES:
            if norm == b or norm.startswith(f"{b} ") or norm.endswith(f" {b}"):
                return True
    return False


def completion_percentages_by_deck(
    deck_cards: List[Any],
    collection_by_name: Dict[str, int],
) -> Dict[str, float]:
    """Deck completion including basic lands (which are always 100% owned), matching the deck list formula."""
    totals: Dict[str, List[int]] = {}
    for card in deck_cards or []:
        deck_id = getattr(card, "deckId", None)
        if not deck_id or getattr(card, "isSideboard", False):
            continue
        quantity = getattr(card, "quantity", None) or 0
        if quantity <= 0:
            continue
        is_basic = is_basic_land(
            getattr(card, "typeLine", None),
            getattr(card, "cardName", None),
        )
        if is_basic:
            actual_owned = quantity
        else:
            assigned = getattr(card, "assignedQuantity", None) or 0
            owned_copies = collection_by_name.get(normalize_card_name(card.cardName), 0)
            actual_owned = min(quantity, max(assigned, min(owned_copies, quantity)))
        bucket = totals.setdefault(deck_id, [0, 0])
        bucket[0] += quantity
        bucket[1] += actual_owned
    return {
        deck_id: round((owned / total) * 100, 1) if total else 0.0
        for deck_id, (total, owned) in totals.items()
    }


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
