import re
from typing import List, Optional
from src.schemas.import_export import ParsedCardEntry, ParsedDecklist

def parse_decklist_text(raw_text: str) -> List[ParsedCardEntry]:
    """
    Parses decklists in standard MTG formats:
    - Moxfield format: 1 Atraxa, Praetors' Voice (2XM) 198 *F*
    - MTG Arena format: Deck, 4 Lightning Bolt (CLB) 123, Sideboard, 1 Force of Will
    - Plaintext: 4x Lightning Bolt, 1 Sol Ring
    - Sideboard prefix: SB: 1 Card
    """
    lines = raw_text.splitlines()
    parsed_cards: List[ParsedCardEntry] = []
    in_sideboard = False
    in_commander = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        lower = line.lower()
        if lower.startswith("// sideboard") or lower.startswith("sideboard") or lower == "sideboard:":
            in_sideboard = True
            in_commander = False
            continue

        if lower.startswith("// commander") or lower.startswith("commander") or lower == "commander:":
            in_commander = True
            in_sideboard = False
            continue

        if lower.startswith("// main") or lower.startswith("deck") or lower.startswith("// deck"):
            in_sideboard = False
            in_commander = False
            continue

        # Commentary
        if line.startswith("//") or line.startswith("#"):
            continue

        is_sideboard = in_sideboard
        is_commander = in_commander
        card_text = line

        # SB: format
        if re.match(r"^sb:\s*", card_text, re.IGNORECASE):
            is_sideboard = True
            card_text = re.sub(r"^sb:\s*", "", card_text, flags=re.IGNORECASE).strip()

        # Quantity
        quantity = 1
        qty_match = re.match(r"^(\d+)(?:x|\s)\s*(.*)$", card_text, re.IGNORECASE)
        if qty_match:
            try:
                quantity = int(qty_match.group(1))
            except ValueError:
                quantity = 1
            card_text = qty_match.group(2).strip()

        # Foil tags like *F*, *E*, *Foil*
        is_foil = bool(re.search(r"\*[A-Za-z0-9]+\*$", card_text))
        card_text = re.sub(r"\s*\*[A-Za-z0-9]+\*\s*$", "", card_text).strip()

        # (SET) 123
        set_code: Optional[str] = None
        collector_number: Optional[str] = None

        set_match = re.search(r"\(([A-Za-z0-9_]{3,6})\)\s*([A-Za-z0-9\-pP]+)?$", card_text, re.IGNORECASE)
        if set_match:
            set_code = set_match.group(1).lower()
            collector_number = set_match.group(2).strip() if set_match.group(2) else None
            card_text = card_text[:set_match.start()].strip()
        else:
            alt_match = re.search(r"\[([A-Za-z0-9_]{3,6}):([A-Za-z0-9\-pP]+)\]$", card_text, re.IGNORECASE)
            if alt_match:
                set_code = alt_match.group(1).lower()
                collector_number = alt_match.group(2).strip() if alt_match.group(2) else None
                card_text = card_text[:alt_match.start()].strip()

        if card_text:
            parsed_cards.append(ParsedCardEntry(
                name=card_text,
                quantity=quantity,
                setCode=set_code,
                collectorNumber=collector_number,
                isFoil=is_foil,
                isSideboard=is_sideboard,
                isCommander=is_commander,
            ))

    return parsed_cards

def parse_decklist(raw_text: str) -> ParsedDecklist:
    entries = parse_decklist_text(raw_text)
    mainboard = [e for e in entries if not e.isSideboard]
    sideboard = [e for e in entries if e.isSideboard]
    total = sum(e.quantity for e in entries)
    return ParsedDecklist(
        mainboard=mainboard,
        sideboard=sideboard,
        totalCards=total,
    )
