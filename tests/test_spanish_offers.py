"""Spanish offer names must match canonical inventory, decks, wants and printings."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.card_utils import normalize_card_name
from src.services.scryfall_service import fold_search_text
from src.services.offer_name_resolver import resolve_offer_names
from src.services.simulated_collection_service import SimulatedCollectionService
from src.services.offer_card_selection import remaining_offer_text
from src.schemas.price_statistics import PriceHistoryStatistics

FIXTURES = Path(__file__).parent / 'fixtures/offers'
CATALOG = json.loads((FIXTURES / 'spanish-catalog.json').read_text())
RAW = (FIXTURES / 'spanish-decklist.txt').read_text()


def database():
    db = MagicMock()
    async def resolve(sql, keys_json, *args):
        if "a.alias_name" not in sql:
            return []
        keys = set(json.loads(keys_json))
        return [dict(row, alias_name=alias) for row in CATALOG
                for alias in (row['name'], row['name_es'], row['name'].split(' // ')[0], (row['name_es'] or '').split(' // ')[0])
                if fold_search_text(alias) in keys]
    db.query_raw = AsyncMock(side_effect=resolve)
    cards = [NS(id=row['printing_id'], deckId='deck', cardName=row['name'], quantity=7, assignedQuantity=0,
                isSideboard=False, typeLine=row['type_line'], manaCost=None, imageUri=None, cardScryfallId=row['printing_id']) for row in CATALOG]
    db.deck.find_many = AsyncMock(return_value=[NS(id='deck', name='Mazo', cards=cards)])
    db.collectioncard.find_many = AsyncMock(return_value=[NS(cardName='Sol Ring', quantity=1)])
    db.wantcard.find_many = AsyncMock(return_value=[NS(id='want', cardName='Swords to Plowshares', quantity=3)])
    printings = [NS(id=row['printing_id'], catalog=NS(normalizedName=normalize_card_name(row['name']), name=row['name'], manaCost=None,
                   typeLine=row['type_line'], imageUri=None), set=NS(code=row['set_code'], name='Example'),
                   collectorNumber=row['collector_number'], priceCardmarketTrend=row['price'], priceEur=row['price'], priceEurFoil=row['price']*2,
                   imageUri=None, imageUriLarge=None, imageUriSmall=None) for row in CATALOG]
    async def find_printings(**kwargs):
        norms = kwargs['where']['catalog']['normalizedName']['in']
        return [printing for printing in printings if printing.catalog.normalizedName in norms]
    db.cardprinting.find_many = AsyncMock(side_effect=find_printings)
    db.simulatedcollection.create = AsyncMock(return_value=NS(id='saved'))
    db.simulatedcard.create = AsyncMock()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [RAW, RAW.lower(), RAW.replace('Relámpago', 'RELAMPAGO'), RAW.replace('Fuego // Hielo', 'Fuego')])
async def test_fixture_in_spanish_matches_inventory_decks_wants_and_prices(raw):
    db = database()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        result = await SimulatedCollectionService.analyze_raw_text('owner', raw)
    assert result.totalCards == 13 and result.uniqueCards == 6
    assert {card.cardName for card in result.cards} == {row['name'] for row in CATALOG}
    assert result.rawText == raw
    assert result.totalEconomicValue == 17.85
    ring = next(card for card in result.cards if card.cardName == 'Sol Ring')
    assert ring.quantity == 7 and ring.copiesOwnedReal == 1 and ring.usefulCopies == 0
    swords = next(card for card in result.cards if card.cardName == 'Swords to Plowshares')
    assert swords.inWants and swords.candidateDeckCount == 1 and swords.usefulCopies == 1
    assert result.globalCompletionBefore == 33.3 and result.globalCompletionAfter == 100 and result.globalNetGain == 66.7
    assert {match.card_name for match in result.opportunityMatches.wants_matches} == {'Arcane Signet', 'Lightning Bolt', 'Swords to Plowshares', 'Fire // Ice'}
    assert all(card.cardScryfallId for card in result.cards)
    assert 'Anillo solar' in RAW and 'Relámpago' in RAW  # Regression fixture is Spanish, not an English input.
    db.collectioncard.create.assert_not_called()


@pytest.mark.asyncio
async def test_english_and_spanish_names_of_the_same_card_share_one_identity():
    db = database()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        result = await SimulatedCollectionService.analyze_raw_text('owner', '1 Relámpago\n7 Lightning Bolt\n2 RELAMPAGO', printing_overrides={'relámpago': 'bolt'})
    assert result.uniqueCards == 1 and result.totalCards == 10
    card = result.cards[0]
    assert card.cardName == 'Lightning Bolt' and card.selectedPrintingId == 'bolt' and card.usefulCopies == 1
    assert set(card.sourceNames) == {'Relámpago', 'Lightning Bolt', 'RELAMPAGO'}
    assert result.printingOverrides == {'lightning bolt': 'bolt'}


@pytest.mark.asyncio
async def test_cardmarket_offer_in_spanish_retains_asking_prices_and_want_matches():
    db = database()
    raw = (FIXTURES / 'spanish-cardmarket.txt').read_text()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        result = await SimulatedCollectionService.analyze_raw_text('owner', raw, printing_overrides={'Espadas en guadañas': 'swords'})
    purchase = result.purchaseAnalysis
    assert purchase.totalPurchaseCost == 4.22 and purchase.totalMarketValue == 15.5
    assert [(card.cardName, card.quantity) for card in purchase.cards] == [('Sol Ring', 7), ('Swords to Plowshares', 1)]
    assert purchase.cards[1].inWants and purchase.cards[1].referencePrintingId == 'swords'
    assert result.opportunityMatches.wants_matches[0].card_name == 'Swords to Plowshares'
    assert result.opportunityMatches.wants_matches[0].unit_price == 0.3


@pytest.mark.asyncio
async def test_saved_offer_keeps_spanish_source_but_persists_canonical_card_names():
    db = database()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        saved = await SimulatedCollectionService.create_simulated_collection('owner', 'Oferta española', None, RAW)
    assert saved.id == 'saved'
    assert db.simulatedcollection.create.await_args.kwargs['data']['rawText'] == RAW
    assert {call.kwargs['data']['cardName'] for call in db.simulatedcard.create.await_args_list} == {row['name'] for row in CATALOG}
    # Selected acquisition consumes the Spanish source rows using their retained aliases.
    selected = next(card for card in saved.cards if card.cardName == 'Lightning Bolt')
    remaining = remaining_offer_text(RAW, {normalize_card_name(selected.cardName)} | {normalize_card_name(source) for source in selected.sourceNames})
    assert 'Relámpago' not in remaining and 'Espadas en guadañas' in remaining


@pytest.mark.asyncio
async def test_no_fuzzy_match_for_partial_unknown_or_ambiguous_spanish_names():
    db = database()
    assert await resolve_offer_names(['Relámp', 'Una carta inventada', 'Lightning Bolt'], db) == {'lightningbolt': 'Lightning Bolt'}
    db.query_raw = AsyncMock(return_value=[dict(name='Card A', alias_name='Nombre repetido'), dict(name='Card B', alias_name='Nombre repetido')])
    assert await resolve_offer_names(['Nombre repetido'], db) == {}


@pytest.mark.asyncio
async def test_localized_details_and_face_aliases_resolve_without_network():
    db = NS(query_raw=AsyncMock(return_value=[dict(name='Fire // Ice', alias_name='Hielo', type_line='Instant // Instant', set_code='mh2'),
                                            dict(name='Lightning Bolt', alias_name='Relámpago', type_line='Instant', set_code='m10'),
                                            dict(name='Art Card', alias_name='Carta de arte', type_line='Card', set_code='atmt')]))
    result = await resolve_offer_names(['Hielo', 'Relámpago', 'Carta de arte'], db)
    assert result == {'hielo': 'Fire // Ice', 'relampago': 'Lightning Bolt'}
    db.query_raw.assert_awaited_once()
    assert "details_es->'card_faces'" in db.query_raw.await_args.args[0]

@pytest.mark.asyncio
async def test_analysis_http_contract_accepts_the_spanish_fixture():
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from src.routers.simulated_collections import router
    from src.core.auth import get_current_user_id
    app = FastAPI()
    app.include_router(router, prefix='/api')
    app.dependency_overrides[get_current_user_id] = lambda: 'owner'
    db = database()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/simulated-collections/analyze-raw', json={'rawText': RAW, 'provider': 'cardmarket'})
    assert response.status_code == 200
    data = response.json()
    assert data['globalCompletionAfter'] == 100
    assert data['cards'][0]['cardName'] == 'Sol Ring' and data['cards'][0]['sourceNames'] == ['Anillo solar']
    assert data['opportunityMatches']['total_wants_matches'] == 4


@pytest.mark.asyncio
async def test_removing_a_card_from_spanish_source_keeps_other_version_selections():
    db = database()
    saved_cards = [NS(cardName=row['name'], selectedPrintingId=row['printing_id'], acquiredAt=None) for row in CATALOG]
    coll = NS(id='saved', userId='owner', name='Oferta española', description=None, rawText=RAW, cards=saved_cards, status='evaluating')
    db.simulatedcollection.find_unique = AsyncMock(return_value=coll)
    db.simulatedcollection.update = AsyncMock()
    db.simulatedcard.delete_many = AsyncMock()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        updated = await SimulatedCollectionService.mutate_card('owner', 'saved', 'Lightning Bolt', 'cardmarket', 'remove')
    assert 'Relámpago' not in updated.rawText
    assert 'Sello arcano' in updated.rawText
    assert 'lightning bolt' not in updated.printingOverrides
    assert updated.printingOverrides['arcane signet'] == 'signet'
    assert next(card for card in updated.cards if card.cardName == 'Arcane Signet').selectedPrintingId == 'signet'

@pytest.mark.asyncio
async def test_acquiring_selection_from_spanish_offer_consumes_the_original_source_rows():
    from src.services import acquisition_service
    from src.schemas.acquisitions import OfferQuoteInput
    from datetime import date
    db = database()
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        analysis = await SimulatedCollectionService.analyze_raw_text('owner', RAW, printing_overrides={'Relámpago': 'bolt'})
    analysis.origin='other'
    analysis_db=NS(query_raw=AsyncMock(side_effect=[[{'n':0,'base':'0','fee':'0'}],[{'id':'bolt','code':'m10','collector_number':'1','image_uri':None,'mana_cost':None,'type_line':None,'price':1,'foil_price':2,'observed_at':None}]]))
    capture=AsyncMock(side_effect=lambda user,mode,data:data)
    with patch.object(acquisition_service,'db',analysis_db),patch.object(SimulatedCollectionService,'get_simulated_collection',AsyncMock(return_value=analysis)),patch.object(acquisition_service,'save_quote',capture):
        quote=await acquisition_service.create_offer_quote('owner','offer',OfferQuoteInput(acquiredOn=date(2026,10,3),cardNames=['Lightning Bolt']))
    assert quote['items'][0]['quantity']==2
    assert 'Relámpago' not in quote['remainingText'] and 'Espadas en guadañas' in quote['remainingText'] and 'Anillo solar' in quote['remainingText']
