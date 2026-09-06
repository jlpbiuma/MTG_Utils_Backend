from src.services.pricing_service import PricingService

def test_extract_quote_cardmarket():
    scry_data = {
        "id": "card-1",
        "name": "Sol Ring",
        "prices": {"eur": "1.50", "eur_foil": "4.00"},
        "purchase_uris": {"cardmarket": "https://cardmarket.com/sol-ring"},
    }
    quote = PricingService._extract_quote(scry_data, "Sol Ring", "cardmarket", "EUR")
    assert quote.trendPrice == 1.50
    assert quote.minPrice == 1.50
    assert quote.maxPrice == 4.00
    assert quote.currency == "EUR"
    assert quote.productUrl == "https://cardmarket.com/sol-ring"

def test_extract_quote_mtggoldfish():
    scry_data = {
        "id": "card-2",
        "name": "Arcane Signet",
        "prices": {"usd": "1.25", "usd_foil": "3.50"},
    }
    quote = PricingService._extract_quote(scry_data, "Arcane Signet", "mtggoldfish", "USD")
    assert quote.trendPrice == 1.25
    assert quote.currency == "USD"
    assert "Arcane+Signet" in (quote.productUrl or "")

def test_extract_quote_missing_data():
    quote = PricingService._extract_quote(None, "Unknown Card", "cardmarket", "EUR")
    assert quote.trendPrice == 0.0
    assert quote.minPrice == 0.0
    assert quote.maxPrice == 0.0
