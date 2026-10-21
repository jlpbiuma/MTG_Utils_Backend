from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import pytest
from src.services.cardmarket_cart_parser import parse_cardmarket_cart
from src.schemas.simulated_collections import SimulatedCollectionCreateRequest, OfferLinkImportRequest
from src.services.offer_import_service import import_link_offer


@pytest.mark.parametrize('origin', ['whatsapp', 'cardmarket', 'other'])
@pytest.mark.parametrize('escaped', [False, True])
def test_supplied_cardmarket_order_is_independent_of_origin(origin, escaped):
    text = (Path(__file__).parent / 'fixtures/cardmarket/order-clipboard.txt').read_text()
    if escaped:
        text = text.replace('\n', '\\\n').replace('#', '\\#').replace('\t', '&#x9;')
    payload = SimulatedCollectionCreateRequest(name='Pedido', rawText=text, origin=origin)
    result = parse_cardmarket_cart(payload.rawText)
    assert len(result.entries) == 24
    assert sum(row.unit_price * row.quantity for row in result.entries) == Decimal('22.54')
    assert result.entries[5].name == 'Kyler, Sigardian Emissary'
    assert result.entries[12].name == 'Éowyn, Fearless Knight'
    assert result.entries[-1].name == 'The Legend of Kyoshi // Avatar Kyoshi'
    assert result.entries[-1].collector_number == '186'
    assert result.entries[0].condition == 'GD'
    assert result.warnings == []
    assert all(row.set_name is None for row in result.entries)  # seller codes are not edition IDs


def test_legacy_request_keeps_default_and_plain_lists_are_not_forced_into_carts():
    payload = SimulatedCollectionCreateRequest(name='Legacy', rawText='7 Sol Ring\n1 Forest')
    assert payload.origin == 'other'
    assert parse_cardmarket_cart(payload.rawText) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['whatsapp', 'cardmarket', 'other'])
async def test_import_saves_full_source_not_only_matches_and_keeps_metadata(origin):
    rows = [dict(id=-1, url='https://manabox.app/decks/x', subject='vende', card_name='Sol Ring', set_code='cmm', extra_json='{"quantity": 7, "collector_number": "1", "source": "manual"}'),
            dict(id=-2, url='https://manabox.app/decks/x', subject='vende', card_name='Unmatched', set_code=None, extra_json='{"quantity": 2, "source": "manual"}')]
    database = NS(simulatedcollection=NS(find_first=AsyncMock(return_value=None), update=AsyncMock()))
    create = AsyncMock(return_value=NS(id='offer'))
    detail = AsyncMock(return_value=NS(id='offer', origin=origin))
    with patch('src.services.offer_import_service.db', database), \
         patch('src.services.offer_import_service.compare_link', AsyncMock()) as compare, \
         patch('src.services.offer_import_service.read_saved_rows', AsyncMock(return_value=rows)) as read, \
         patch('src.services.offer_import_service.SimulatedCollectionService.create_simulated_collection', create), \
         patch('src.services.offer_import_service.SimulatedCollectionService.get_simulated_collection', detail):
        result = await import_link_offer('owner', OfferLinkImportRequest(url=rows[0]['url'], origin=origin))
    assert result.origin == origin
    compare.assert_awaited_once()
    read.assert_awaited_once_with('owner')
    assert '7 Sol Ring (cmm) 1' in create.await_args.args[3]
    assert '2 Unmatched' in create.await_args.args[3]
    assert create.await_args.kwargs['origin'] == origin
    assert database.simulatedcollection.update.await_args.kwargs['data']['sourceUrl'] == rows[0]['url']
    assert database.simulatedcollection.find_first.await_args.kwargs['where']['userId'] == 'owner'


@pytest.mark.asyncio
async def test_opening_saved_opportunity_reuses_offer_and_never_fetches_url():
    row = dict(url='https://manabox.app/decks/x', subject='vende')
    database = NS(simulatedcollection=NS(find_first=AsyncMock(return_value=NS(id='existing'))))
    with patch('src.services.offer_import_service.db', database), \
         patch('src.services.offer_import_service.read_saved_rows', AsyncMock(return_value=[row])), \
         patch('src.services.offer_import_service.compare_link', AsyncMock()) as fetch, \
         patch('src.services.offer_import_service.SimulatedCollectionService.get_simulated_collection', AsyncMock(return_value=NS(id='existing'))) as detail:
        result = await import_link_offer('owner', OfferLinkImportRequest(url=row['url'], savedOnly=True))
    assert result.id == 'existing'
    fetch.assert_not_awaited()
    detail.assert_awaited_once_with('owner', 'existing')


@pytest.mark.asyncio
async def test_missing_discovery_does_not_create_an_offer():
    with patch('src.services.offer_import_service.read_saved_rows', AsyncMock(return_value=[])), \
         patch('src.services.offer_import_service._get_sqlite_path', return_value=None), \
         patch('src.services.offer_import_service.SimulatedCollectionService.create_simulated_collection', AsyncMock()) as create:
        with pytest.raises(ValueError, match='ya no está disponible'):
            await import_link_offer('owner', OfferLinkImportRequest(url='https://manabox.app/decks/x', savedOnly=True))
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_discovery_analyzes_the_whole_list_without_persisting():
    from src.services.offer_import_service import discover_offers
    from src.schemas.simulated_collections import SimulatedCollectionAnalysisResponse
    from src.schemas.whatsapp_deals import WhatsAppMatchesResponse, WhatsAppDealMatch
    row = dict(id=1, url='https://manabox.app/decks/x', subject='vende', card_name='Useful', set_code=None, extra_json='{"quantity": 1, "source": "manual"}', detected_at='2026-10-03T12:00:00+00:00')
    unmatched = {**row, 'id': 2, 'card_name': 'Unmatched', 'extra_json': '{"quantity": 7, "source": "manual"}'}
    match = WhatsAppDealMatch(id=1, card_name='Useful', normalized_name='useful', subject='vende', whatsapp_url=row['url'], source_phone='', direct_whatsapp_link='')
    database = NS(simulatedcollection=NS(find_many=AsyncMock(return_value=[]), create=AsyncMock()))
    analyzed = SimulatedCollectionAnalysisResponse(name='Oferta', totalCards=8, totalEconomicValue=10, sellableCardsCount=7)
    with patch('src.services.offer_import_service.db', database), \
         patch('src.services.offer_import_service.WhatsAppDealsService.get_matches', AsyncMock(return_value=WhatsAppMatchesResponse(wants_matches=[match]))), \
         patch('src.services.offer_import_service.read_saved_rows', AsyncMock(return_value=[row, unmatched])), \
         patch('src.services.offer_import_service._get_sqlite_path', return_value=None), \
         patch('src.services.offer_import_service.SimulatedCollectionService._prepare_context', AsyncMock(return_value=None)), \
         patch('src.services.offer_import_service.SimulatedCollectionService.analyze_raw_text', AsyncMock(return_value=analyzed)) as analyze, \
         patch('src.services.offer_import_service.SimulatedCollectionService._attach_opportunities', AsyncMock()):
        result = await discover_offers('owner')
    assert len(result) == 1 and result[0].totalCards == 8 and result[0].totalEconomicValue == 10
    assert result[0].origin == 'other' and result[0].id.startswith('discovery:')
    assert '7 Unmatched' in analyze.await_args.args[1]
    database.simulatedcollection.create.assert_not_awaited()
    database.simulatedcollection.find_many.assert_awaited_once_with(where={'userId': 'owner'})


@pytest.mark.asyncio
@pytest.mark.parametrize('raw,expected', [('1 A', 0.5), ('7 A', 0.5), ('1 B', 1.0), ('7 B', 1.0)])
async def test_offer_analysis_uses_presence_and_average_of_decks(raw, expected):
    from src.services.simulated_collection_service import SimulatedCollectionService
    from src.schemas.price_statistics import PriceHistoryStatistics
    from unittest.mock import MagicMock
    decks = []
    for deck_id in ('a', 'b'):
        cards = [NS(id=f'{deck_id}-{i}', deckId=deck_id, cardName=f'Card {i}', quantity=7, assignedQuantity=0, isSideboard=False,
                    typeLine='Creature', manaCost=None, imageUri=None, cardScryfallId=None) for i in range(100)]
        cards[0].cardName = 'A' if deck_id == 'a' else 'Other'
        cards[1].cardName = 'B'
        cards.append(NS(id='side', deckId=deck_id, cardName='A', quantity=99, assignedQuantity=0, isSideboard=True,
                        typeLine='Creature', manaCost=None, imageUri=None, cardScryfallId=None))
        decks.append(NS(id=deck_id, name=deck_id, cards=cards))
    database = MagicMock()
    database.deck.find_many = AsyncMock(return_value=decks)
    database.collectioncard.find_many = AsyncMock(return_value=[])
    database.wantcard.find_many = AsyncMock(return_value=[])
    database.cardprinting.find_many = AsyncMock(return_value=[])
    database.query_raw = AsyncMock(return_value=[])
    with patch('src.services.simulated_collection_service.db', database), \
         patch('src.services.simulated_collection_service.get_history_statistics', AsyncMock(return_value=({}, PriceHistoryStatistics()))):
        result = await SimulatedCollectionService.analyze_raw_text('owner', raw)
    assert result.globalNetGain == expected
    assert result.globalCompletionBefore == 0 and result.globalCompletionAfter == expected
    assert result.cards[0].netCompletionGain == expected
    assert result.opportunityMatches.wants_matches[0].completion_gain == expected
    assert result.cards[0].usefulCopies == 1
    assert all(deck.potentialGain == 1 for deck in result.cards[0].candidateDecks)
    assert all(deck.totalCards == 100 and deck.ownedCards == 0 for deck in result.cards[0].candidateDecks)
