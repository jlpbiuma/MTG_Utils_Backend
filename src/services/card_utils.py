from typing import Optional, Dict, Any, List
import re

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
        if lower_name in ["plains", "island", "swamp", "mountain", "forest", "wastes"] or lower_name.startswith("snow-covered "):
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
