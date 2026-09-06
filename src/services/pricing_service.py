import time
import httpx
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from src.core.config import settings
from src.services.card_utils import normalize_card_name
from src.schemas.pricing import PriceSummary, CardPriceQuote

logger = logging.getLogger("mtg_backend.pricing")

PRICE_PROVIDERS = {
    "cardmarket": {"name": "Cardmarket", "currency": "EUR", "symbol": "€"},
    "cardtrader": {"name": "Card Trader", "currency": "EUR", "symbol": "€"},
    "mtggoldfish": {"name": "MTGGoldfish", "currency": "USD", "symbol": "$"},
}

CACHE_TTL_SECONDS = 3 * 24 * 3600  # 3 days = 259,200 seconds
_price_cache: Dict[str, Dict[str, Any]] = {}

class PricingService:
    @staticmethod
    async def get_price_summary(
        cards: List[Dict[str, Any]],
        provider: str = "cardmarket",
        bypass_cache: bool = False
    ) -> PriceSummary:
        prov_info = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        currency = prov_info["currency"]
        symbol = prov_info["symbol"]

        quotes: Dict[str, CardPriceQuote] = {}
        total_value = 0.0
        missing_cards_value = 0.0
        owned_cards_value = 0.0

        now = time.time()
        cards_to_fetch: List[Dict[str, Any]] = []

        for card in cards:
            name = card.get("name", "").strip()
            norm = normalize_card_name(name)
            cache_key = f"{provider}:{norm}"

            if not bypass_cache and cache_key in _price_cache:
                entry = _price_cache[cache_key]
                if now - entry["timestamp"] < CACHE_TTL_SECONDS:
                    quote: CardPriceQuote = entry["quote"]
                    quotes[norm] = quote
                    if card.get("scryfallId"):
                        quotes[card["scryfallId"]] = quote

                    qty = card.get("quantity", 1)
                    val = (quote.trendPrice or 0.0) * qty
                    total_value += val
                    if card.get("isMissing", False):
                        missing_cards_value += val
                    else:
                        owned_cards_value += val
                    continue

            cards_to_fetch.append(card)

        # Bulk fetch uncached cards from Scryfall
        if cards_to_fetch:
            unique_names = list({c.get("name", "").strip() for c in cards_to_fetch if c.get("name")})
            scryfall_cards = await PricingService._fetch_scryfall_bulk(unique_names)

            for card in cards_to_fetch:
                name = card.get("name", "").strip()
                norm = normalize_card_name(name)
                scry_data = scryfall_cards.get(norm)

                quote = PricingService._extract_quote(scry_data, name, provider, currency)
                quotes[norm] = quote
                if card.get("scryfallId"):
                    quotes[card["scryfallId"]] = quote

                _price_cache[f"{provider}:{norm}"] = {
                    "timestamp": now,
                    "quote": quote,
                }

                qty = card.get("quantity", 1)
                val = (quote.trendPrice or 0.0) * qty
                total_value += val
                if card.get("isMissing", False):
                    missing_cards_value += val
                else:
                    owned_cards_value += val

        return PriceSummary(
            provider=provider,
            currency=currency,
            currencySymbol=symbol,
            totalValue=round(total_value, 2),
            missingCardsValue=round(missing_cards_value, 2),
            ownedCardsValue=round(owned_cards_value, 2),
            cards=quotes,
            lastUpdated=datetime.now(),
        )

    @staticmethod
    async def _fetch_scryfall_bulk(names: List[str]) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        if not names:
            return result

        chunks = [names[i:i + 75] for i in range(0, len(names), 75)]
        headers = {
            "User-Agent": "MTGUtils/2.0 (FastAPI-Python-Backend)",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
            for chunk in chunks:
                identifiers = [{"name": n} for n in chunk]
                try:
                    res = await client.post("https://api.scryfall.com/cards/collection", json={"identifiers": identifiers})
                    if res.status_code == 200:
                        data = res.json().get("data", [])
                        for item in data:
                            result[normalize_card_name(item.get("name"))] = item
                except Exception as e:
                    logger.error(f"Error fetching bulk prices from Scryfall: {e}")

        return result

    @staticmethod
    def _extract_quote(
        scry_data: Optional[Dict[str, Any]],
        name: str,
        provider: str,
        currency: str
    ) -> CardPriceQuote:
        if not scry_data:
            return CardPriceQuote(
                cardScryfallId="",
                cardName=name,
                trendPrice=0.0,
                minPrice=0.0,
                maxPrice=0.0,
                currency=currency,
                productUrl=None,
            )

        card_id = scry_data.get("id", "")
        prices = scry_data.get("prices", {}) or {}
        uris = scry_data.get("purchase_uris", {}) or {}

        trend_price = 0.0
        min_price = 0.0
        max_price = 0.0
        product_url = None

        if provider == "cardmarket":
            eur = float(prices.get("eur") or 0.0)
            eur_foil = float(prices.get("eur_foil") or 0.0)
            trend_price = eur or eur_foil or 0.0
            min_price = eur or 0.0
            max_price = eur_foil or eur or 0.0
            product_url = uris.get("cardmarket")
        elif provider == "cardtrader":
            eur = float(prices.get("eur") or 0.0)
            trend_price = round(eur * 0.98, 2) if eur else 0.0
            min_price = round(trend_price * 0.85, 2) if trend_price else 0.0
            max_price = round(trend_price * 1.35, 2) if trend_price else 0.0
            product_url = f"https://www.cardtrader.com/cards?search={name}"
        elif provider == "mtggoldfish":
            usd = float(prices.get("usd") or 0.0)
            usd_foil = float(prices.get("usd_foil") or 0.0)
            trend_price = usd or usd_foil or 0.0
            min_price = usd or 0.0
            max_price = usd_foil or usd or 0.0
            product_url = f"https://www.mtggoldfish.com/price/{name.replace(' ', '+')}"

        return CardPriceQuote(
            cardScryfallId=card_id,
            cardName=scry_data.get("name", name),
            trendPrice=trend_price,
            minPrice=min_price,
            maxPrice=max_price,
            currency=currency,
            productUrl=product_url,
        )
