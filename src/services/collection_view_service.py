"""Collection screen read with local prices, without external enrichment."""
from datetime import datetime, timezone
from src.schemas.pricing import PriceSummary
from src.services.card_utils import normalize_card_name
from src.services.collection_service import CollectionService
from src.services.pricing_service import PricingService


class CollectionViewService:
    @staticmethod
    async def get_view(user_id: str, provider: str = "cardmarket"):
        cards = await CollectionService.get_user_collection(user_id)
        currency = "USD" if provider == "mtggoldfish" else "EUR"
        symbol = "$" if currency == "USD" else "€"
        unique = {c.cardScryfallId or c.cardName: {
            "name": c.cardName, "scryfallId": c.cardScryfallId
        } for c in cards}
        quotes = await PricingService.get_latest_quotes_batch(list(unique.values()), provider, currency)
        # Keep quote lookup by ID/name intact for pending catalog entries.
        visible_quotes = {}
        total_value = 0.0
        for card in cards:
            norm = normalize_card_name(card.cardName)
            quote = quotes.get(card.cardScryfallId) or quotes.get(norm)
            if quote:
                visible_quotes[card.cardScryfallId or norm] = quote
                total_value += quote.unitPrice.trend * card.quantity
        return {
            "cards": cards,
            "priceSummary": PriceSummary(
                provider=provider, currency=currency, currencySymbol=symbol,
                totalCards=sum(c.quantity for c in cards), totalNetValue=round(total_value, 2),
                totalOwnedValue=round(total_value, 2), totalMissingValue=0,
                quotes=visible_quotes, lastUpdated=datetime.now(timezone.utc),
            ),
        }
