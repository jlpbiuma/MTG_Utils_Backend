"""Value tradable copies using link prices or locally cached Cardmarket quotes."""
import math
import logging
from src.services.pricing_service import PricingService
from src.services.card_utils import normalize_card_name


def _amount(value):
    try:
        amount = float(str(value).strip().replace(",", "."))
        return amount if math.isfinite(amount) and amount >= 0 else None
    except (ValueError, TypeError):
        return None


async def value_matches(matches):
    if not matches:
        return
    price_cards = [{"name": match.card_name} for match in matches]
    for match in matches:
        capacity = (match.matched_want.quantity_wanted if match.matched_want
                    else match.matched_collection.available_quantity if match.matched_collection else 0)
        listed = match.extra.get("quantity")
        # Unknown list quantities do not limit a user's demand or free inventory.
        if isinstance(listed, (int, float)) and not isinstance(listed, bool) and math.isfinite(listed):
            capacity = min(capacity, max(0, int(listed)))
        match.trade_quantity = max(0, capacity)
        match.unit_price = _amount(match.price)
        if match.unit_price is None:
            match.unit_price = _amount(match.extra.get("reference_price"))
        if match.unit_price is not None:
            match.price_source = "link"
            match.price_currency = {"€": "EUR", "$": "USD"}.get(match.currency, match.currency or "EUR").upper()

    # Match by name here: a catalogue ID must not be used as a printing ID.
    unique = {normalize_card_name(card["name"]): card for card in price_cards}
    try:
        quotes = await PricingService.get_latest_quotes_batch(list(unique.values()), "cardmarket", "EUR")
    except Exception as exc:
        logging.getLogger(__name__).warning("Opportunity prices unavailable (%s)", type(exc).__name__)
        quotes = {}
    for match in matches:
        quote = quotes.get(match.normalized_name)
        amount = _amount(quote.unitPrice.trend) if quote else None
        match.trend_unit_price = amount
        if amount is not None and match.unit_price is None:
            match.unit_price = amount
            match.price_currency = "EUR"
            match.price_source = "cardmarket"
