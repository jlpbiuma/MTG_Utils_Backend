from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from src.schemas.whatsapp_deals import WhatsAppDealMatch, WantMatchInfo, CollectionMatchInfo
from src.services.opportunity_valuation import value_matches


def match(id=1, **kwargs):
    return WhatsAppDealMatch(id=id, card_name="Sol Ring", normalized_name="sol ring",
        subject="vende", whatsapp_url="https://manabox.app/decks/public", source_phone="",
        direct_whatsapp_link="", **kwargs)


async def test_link_prices_preserve_currency_and_quantity_is_capped_by_list():
    card = match(price="1,50", currency="USD", extra={"quantity": 2},
                 matched_want=WantMatchInfo(want_id="w", quantity_wanted=4))
    with patch("src.services.opportunity_valuation.PricingService.get_latest_quotes_batch", AsyncMock(return_value={"sol ring": NS(unitPrice=NS(trend=2.75))})) as prices:
        await value_matches([card])
    prices.assert_awaited_once_with([{"name": "Sol Ring"}], "cardmarket", "EUR")
    assert card.trend_unit_price == 2.75
    assert card.unit_price == 1.5 and card.price_currency == "USD" and card.price_source == "link"
    assert card.trade_quantity == 2


async def test_manual_reference_prices_and_only_free_sale_copies():
    card = match(extra={"reference_price": "0.25", "quantity": 9}, currency="EUR",
        matched_collection=CollectionMatchInfo(collection_id="c", quantity_owned=10, available_quantity=3))
    await value_matches([card])
    assert card.unit_price == 0.25 and card.trade_quantity == 3


async def test_market_prices_use_one_batch_and_names_not_catalogue_ids():
    cards = [match(catalog_card={"card_id": "catalog-id", "name": "Sol Ring"}), match(id=2)]
    with patch("src.services.opportunity_valuation.PricingService.get_latest_quotes_batch",
               AsyncMock(return_value={"sol ring": NS(unitPrice=NS(trend=2.75))})) as prices:
        await value_matches(cards)
    prices.assert_awaited_once_with([{"name": "Sol Ring"}], "cardmarket", "EUR")
    assert all(card.unit_price == 2.75 and card.price_source == "cardmarket" for card in cards)


async def test_missing_or_invalid_prices_stay_unknown_and_zero_price_is_valid():
    cards = [match(price="NaN"), match(id=2, price="-1"), match(id=3, price="0")]
    with patch("src.services.opportunity_valuation.PricingService.get_latest_quotes_batch", AsyncMock(return_value={})):
        await value_matches(cards)
    assert cards[0].unit_price is None and cards[1].unit_price is None
    assert cards[2].unit_price == 0 and cards[2].price_source == "link"


async def test_price_lookup_failure_does_not_remove_opportunities():
    cards = [match(matched_want=WantMatchInfo(want_id="w", quantity_wanted=2))]
    with patch("src.services.opportunity_valuation.PricingService.get_latest_quotes_batch", AsyncMock(side_effect=RuntimeError())):
        await value_matches(cards)
    assert cards[0].unit_price is None and cards[0].trade_quantity == 2
