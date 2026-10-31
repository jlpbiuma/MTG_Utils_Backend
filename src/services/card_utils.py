from functools import lru_cache
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
    "minigame",
}

NON_PLAYABLE_TYPES = {
    "card",
    "card // card",
}

_WHITESPACE_RE = re.compile(r"\s+")
_FACE_SEPARATOR_RE = re.compile(r"\s*/+\s*")
_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


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
    return _normalize_card_name(name)


@lru_cache(maxsize=65536)
def _normalize_card_name(name: str) -> str:
    # Pure and called once per card row in every completion/demand pass.
    cleaned = _WHITESPACE_RE.sub(" ", name.strip().lower())
    if "/" in cleaned:
        cleaned = _FACE_SEPARATOR_RE.sub(" // ", cleaned)
        cleaned = cleaned.split(" // ")[0]
    return cleaned


def front_face(text: Optional[str]) -> str:
    """Front half of a multi-face value such as "Battle — Siege // Creature"."""
    if not text:
        return ""
    return text.split("//", 1)[0].strip()


def card_mana_cost(card: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Mana cost to persist for a Scryfall card payload.

    Split and adventure cards carry a combined top-level cost ("{1}{R} // {U}").
    Transform, modal DFC, battle and reversible cards only carry it per face:
    their mana value and colors on the stack are the front face's, so the front
    face cost is persisted. The full per-face costs stay in ``card_faces``.
    """
    if not card:
        return None
    mana_cost = card.get("mana_cost")
    if mana_cost:
        return mana_cost
    faces = card.get("card_faces") or []
    front = faces[0] if faces and isinstance(faces[0], dict) else {}
    return front.get("mana_cost") or mana_cost


def card_face_stat(card: Optional[Dict[str, Any]], field: str) -> Any:
    """Top-level stat (power, toughness, loyalty, defense) or the front face's."""
    if not card:
        return None
    if card.get(field) is not None:
        return card.get(field)
    faces = card.get("card_faces") or []
    return faces[0].get(field) if faces and isinstance(faces[0], dict) else None

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

def _symbol_mana_value(symbol: str) -> float:
    inner = symbol.strip().upper()
    if inner in ("X", "Y", "Z"):
        return 0
    if inner.isdigit():
        return int(inner)
    if inner in ("½", "1/2") or (inner.startswith("H") and len(inner) > 1):
        return 0.5
    if "/" in inner:
        # Hybrid symbols cost their most expensive half: {2/W} is 2, {W/U}
        # and Phyrexian {W/P} / {G/U/P} are 1.
        return max(int(part) if part.isdigit() else 1 for part in inner.split("/"))
    return 1


def extract_cmc(mana_cost: Optional[str], type_line: Optional[str] = None) -> float:
    """
    Mana value from a mana string like "{2}{U}{B}". Mirrors the frontend.

    A combined split cost ("{1}{R} // {1}{U}") adds both halves, as on
    Scryfall. Adventure and Omen cards only count their main (front) half.
    """
    if not mana_cost:
        return 0
    halves = mana_cost.split("//")
    if len(halves) > 1 and type_line:
        lowered = type_line.lower()
        if "adventure" in lowered or "aventura" in lowered or "omen" in lowered or "presagio" in lowered:
            halves = halves[:1]
    total = sum(_symbol_mana_value(symbol) for half in halves for symbol in _MANA_SYMBOL_RE.findall(half))
    return int(total) if float(total).is_integer() else total

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
    for symbol in _MANA_SYMBOL_RE.findall(mana_cost):
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
        if not deck_id or getattr(card, "isSideboard", False) is True:
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
    # Exact names only: a prefix/suffix match made Tropical Island, Volcanic
    # Island or Mountain Goat count as always-owned basic lands.
    return bool(card_name) and normalize_card_name(card_name) in BASIC_LAND_NAMES


def completion_cards(deck_cards: List[Any]) -> List[Any]:
    """One mainboard entry per normalized name; quantities never weight completion."""
    unique = {}
    for card in deck_cards or []:
        if getattr(card, "isSideboard", False) is True or (getattr(card, "quantity", 0) or 0) <= 0:
            continue
        key = (getattr(card, "deckId", None), normalize_card_name(card.cardName))
        if key not in unique or (getattr(card, "assignedQuantity", 0) or 0) > (getattr(unique[key], "assignedQuantity", 0) or 0):
            unique[key] = card
    return list(unique.values())


def completion_owned(card: Any, collection_by_name: Dict[str, int]) -> bool:
    """A single owned/assigned copy covers this name in every requesting deck."""
    return (is_basic_land(getattr(card, "typeLine", None), card.cardName)
            or collection_by_name.get(normalize_card_name(card.cardName), 0) > 0
            or (getattr(card, "assignedQuantity", 0) or 0) > 0)


def completion_counts(deck_cards: List[Any], collection_by_name: Dict[str, int]) -> tuple[int, int]:
    cards = completion_cards(deck_cards)
    return len(cards), sum(completion_owned(card, collection_by_name) for card in cards)


def completion_percentages_by_deck(
    deck_cards: List[Any], collection_by_name: Dict[str, int],
) -> Dict[str, float]:
    totals = {}
    for card in completion_cards(deck_cards):
        deck_id = getattr(card, "deckId", None)
        if not deck_id:
            continue
        bucket = totals.setdefault(deck_id, [0, 0])
        bucket[0] += 1
        bucket[1] += int(completion_owned(card, collection_by_name))
    return {deck_id: round(owned / total * 100, 1) for deck_id, (total, owned) in totals.items()}


def get_card_category(type_line: Optional[str] = None, card_name: Optional[str] = None) -> str:
    """
    Categorizes an MTG card based on its type_line, with intelligent fallback
    heuristics based on card name for basic and common lands when type_line is missing.
    Follows MTG convention where creature types take precedence. Multi-face
    cards are grouped by their front face, so a battle that transforms into a
    creature stays a battle and an MDFC spell // land stays a spell.
    """
    if type_line:
        lower = front_face(type_line).lower()
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
