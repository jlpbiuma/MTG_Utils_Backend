"""Remove acquired names from an offer while retaining its remaining seller rows."""
import re
from src.services.card_utils import normalize_card_name
from src.services.cardmarket_cart_parser import parse_cardmarket_cart
from src.services.import_service import parse_decklist_text


def remaining_offer_text(raw_text: str, names: set[str]) -> str:
    text = "\n".join(line.rstrip().removesuffix("\\") for line in raw_text.splitlines())
    cart = parse_cardmarket_cart(text)
    if cart is None:
        return "\n".join(line for line in text.splitlines()
                         if not any(normalize_card_name(entry.name) in names
                                    for entry in parse_decklist_text(line)))
    # Table exports have one complete seller row per line.
    if any(re.search(r"\|\s*\d+x\b", line) for line in text.splitlines()):
        return "\n".join(line for line in text.splitlines()
                         if not ((parsed := parse_cardmarket_cart(line)) and
                                 any(normalize_card_name(entry.name) in names for entry in parsed.entries)))
    markers = list(re.finditer(r"(?im)^\s*(\d+)x\b", text))
    if not markers:
        raise ValueError("No se pudo separar el listado de la oferta.")
    kept = [text[:markers[0].start()]]
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        chunk = text[marker.start():end]
        parsed = parse_cardmarket_cart(chunk + "\n0,00 €")
        if not parsed or not any(normalize_card_name(entry.name) in names for entry in parsed.entries):
            kept.append(chunk)
    return "".join(kept)
