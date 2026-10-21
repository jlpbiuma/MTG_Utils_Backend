import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi import HTTPException
from src.services.offer_name_resolver import _fetch_exact_spanish_card
from src.services.scryfall_service import fold_search_text
from src.services.simulated_collection_service import SimulatedCollectionService
from src.schemas.price_statistics import PriceHistoryStatistics
from tests import test_spanish_offers as spanish

FIXTURES = Path(__file__).parent / 'fixtures/offers'
CARDS = json.loads((FIXTURES / 'toph-spanish-catalog.json').read_text())
RAW = (FIXTURES / 'toph-spanish-offer.txt').read_text()


@pytest.mark.asyncio
async def test_screenshot_offer_resolves_missing_translations_and_can_change_toph_print(monkeypatch):
    monkeypatch.setattr(spanish, 'CATALOG', CARDS)
    db = spanish.database()
    db.cardcatalog.update_many = AsyncMock(return_value=1)
    async def local(sql, keys_json, *args):
        if 'a.alias_name' not in sql:
            return []
        keys = set(json.loads(keys_json))
        return [dict(card, name_es=None, alias_name=card['name']) for card in CARDS if fold_search_text(card['name']) in keys]
    db.query_raw = AsyncMock(side_effect=local)
    async def remote(name):
        card = next((card for card in CARDS if card['name_es'] and fold_search_text(card['name_es']) == fold_search_text(name)), None)
        return {'id': 'spanish-print', 'name': card['name'], 'printed_name': card['name_es'], 'lang': 'es', 'type_line': card['type_line'], 'games': ['paper']} if card else None
    with patch('src.services.offer_name_resolver._fetch_exact_spanish_card', AsyncMock(side_effect=remote)) as lookup, patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        result = await SimulatedCollectionService.analyze_raw_text('owner', RAW)
        assert result.uniqueCards == 7 and result.benefitedDecksCount == 1
        assert all(card.cardScryfallId and card.unitPrice > 0 for card in result.cards)
        assert {card.cardName for card in result.cards} == {card['name'] for card in CARDS}
        assert lookup.await_count == 5
        assert result.cards[0].sourceNames == ['Toph, la primera maestra del metal']
        # The selectable versions share the canonical catalog name, not the Spanish source name.
        original_find = db.cardprinting.find_many.side_effect
        printings = await original_find(where={'catalog': {'normalizedName': {'in': ['toph, the first metalbender']}}})
        toph = printings[0]
        selected = NS(**vars(toph))
        selected.id = 'aae3e063-472e-445a-8440-dc191089d81e'
        selected.collectorNumber = '353'
        selected.priceCardmarketTrend = selected.priceEur = 1.98
        async def versions(**kwargs):
            return (await original_find(**kwargs)) + [selected]
        db.cardprinting.find_many.side_effect = versions
        changed = await SimulatedCollectionService.analyze_raw_text('owner', RAW, printing_overrides={'toph, the first metalbender': selected.id})
        assert changed.cards[0].selectedPrintingId == selected.id
        assert changed.cards[0].collectorNumber == '353' and changed.cards[0].unitPrice == 1.98
        assert changed.rawText == RAW and changed.totalCards == 7
    db.cardcatalog.update_many.assert_any_await(where={'normalizedName': 'toph, the first metalbender', 'OR': [{'nameEs': None}, {'nameEs': 'Toph, the First Metalbender'}]}, data={'nameEs': 'Toph, la primera maestra del metal'})


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [
    [{'name': 'Toph, the First Metalbender', 'printed_name': 'Toph, la primera maestra del metal', 'type_line': 'Legendary Creature', 'games': ['paper']}],
    [{'name': 'Toph, Stubborn Teacher', 'printed_name': 'Toph, profesora testaruda', 'type_line': 'Legendary Creature', 'games': ['paper']}],
    [{'name': 'Toph, the First Metalbender', 'printed_name': 'Toph, la primera maestra del metal', 'layout': 'art_series', 'type_line': 'Card', 'set': 'atla', 'games': ['paper']}],
])
async def test_exact_spanish_remote_lookup_accepts_only_the_requested_playable_card(data):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'data': data})
    original_client = httpx.AsyncClient
    with patch('src.services.offer_name_resolver.httpx.AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs)):
        card = await _fetch_exact_spanish_card('Toph, la primera maestra del metal')
    assert (card is not None) == (data[0]['name'] == 'Toph, the First Metalbender' and data[0].get('layout') != 'art_series')
    assert 'lang:es' in requests[0].url.params['q']


@pytest.mark.asyncio
async def test_art_series_is_not_accepted_as_an_offer_print(monkeypatch):
    monkeypatch.setattr(spanish, 'CATALOG', CARDS)
    db = spanish.database()
    art = NS(id='art', catalog=NS(normalizedName='toph, the first metalbender'), set=NS(code='atla', setType='art_series'), collectorNumber='49')
    db.cardprinting.find_many = AsyncMock(return_value=[art])
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        with pytest.raises(HTTPException) as error:
            await SimulatedCollectionService.analyze_raw_text('owner', RAW, printing_overrides={'toph, the first metalbender': 'art'})
    assert error.value.status_code == 422

@pytest.mark.asyncio
async def test_legacy_offer_saved_with_spanish_names_accepts_canonical_print_selection(monkeypatch):
    monkeypatch.setattr(spanish, 'CATALOG', CARDS)
    db = spanish.database()
    coll = NS(id='saved', userId='owner', name='Oferta', description=None, rawText=RAW, status='evaluating',
              cards=[NS(cardName=card['name_es'] or card['name'], selectedPrintingId=None, acquiredAt=None) for card in CARDS])
    db.simulatedcollection.find_unique = AsyncMock(return_value=coll)
    db.simulatedcard.update_many = AsyncMock()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        result = await SimulatedCollectionService.update_card_version('owner', 'saved', 'Toph, the First Metalbender', CARDS[0]['printing_id'])
    assert result.cards[0].selectedPrintingId == CARDS[0]['printing_id']
    db.simulatedcard.update_many.assert_awaited_once_with(where={'simulatedCollectionId': 'saved', 'cardName': {'in': ['Toph, la primera maestra del metal']}}, data={'selectedPrintingId': CARDS[0]['printing_id']})
