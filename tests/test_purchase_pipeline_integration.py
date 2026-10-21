"""Opt-in isolated database tests for money, provenance, and replay safety."""
import os
from pathlib import Path
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from prisma import Prisma
from fastapi import HTTPException
from src.services import acquisition_service
from src.routers import purchases, performance, wants, catalog
from src.schemas.purchases import BatchInput, AssignInput, Assignment, ConfirmOffer
from src.schemas.wants import WantBulkRequest, WantCardCreate
from src.schemas.simulated_collections import SimulatedCollectionAnalysisResponse, SimulatedCardAnalysisItem

URL = os.getenv('MTG_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='Set MTG_TEST_DATABASE_URL to a disposable database')

@pytest_asyncio.fixture
async def database(monkeypatch):
    import uuid
    schema = 'pipeline_' + uuid.uuid4().hex
    admin = Prisma(datasource={'url': URL})
    await admin.connect()
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    client = Prisma(datasource={'url': URL + ('&' if '?' in URL else '?') + 'schema=' + schema})
    try:
        await client.connect()
        assert (await client.query_raw('SELECT current_schema() AS name'))[0]['name'] == schema
        for migration in sorted((Path(__file__).parents[1]/'prisma/migrations').glob('*/migration.sql')):
            # The index extension belongs to the shared admin schema, not this fixture.
            sql = migration.read_text()
            statements = sql.split('-- statement-break') if '-- statement-break' in sql else sql.split(';')
            for statement in statements:
                statement = '\n'.join(line for line in statement.splitlines() if not line.strip().startswith('--')).strip()
                if statement and not statement.startswith('CREATE EXTENSION'):
                    await client.execute_raw(statement.replace("gin_trgm_ops", "public.gin_trgm_ops"))
        for module in [purchases, performance, wants, catalog,acquisition_service]:
            monkeypatch.setattr(module, 'db', client, raising=False)
        await client.execute_raw("INSERT INTO card_sets(id,code,name,set_type,card_count,released_at,search_uri,updated_at) VALUES('set','tst','Test','expansion',2,'2026-01-01','',NOW())")
        for cid, price in [('p1', 2.0), ('p2', 0.10)]:
            await client.execute_raw('INSERT INTO card_catalog(id,name,normalized_name,updated_at) VALUES($1,$2,$3,NOW())', 'cat'+cid, 'Card '+cid, 'card '+cid)
            await client.execute_raw('INSERT INTO card_printings(id,catalog_id,set_id,collector_number,price_eur,price_eur_foil,updated_at) VALUES($1,$2,\'set\',$1,$3,$4,NOW())', cid, 'cat'+cid, price, price*2)
        yield client
    finally:
        if client.is_connected():
            await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()

@pytest.mark.asyncio
async def test_batches_assignments_user_scope_and_roi(database):
    db = database
    await db.execute_raw("INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number) VALUES('c','alice','p1','Card p1',3,'tst','p1')")
    batch = await purchases.create_batch(BatchInput(setCode='tst', acquiredOn=date(2026,1,2), costTotal=Decimal('4.00'),shipping=Decimal('1.00')), 'alice')
    await purchases.assign(batch['id'], AssignInput(items=[Assignment(collectionCardId='c',quantity=2)]), 'alice')
    result = await performance.performance('tst', 'alice')
    assert result['lots'][0]['marketValue'] == 4
    assert result['lots'][0]['investment'] == 5
    assert result['lots'][0]['roi'] == -20
    other = await purchases.create_batch(BatchInput(setCode='tst', acquiredOn=date(2026,1,2),costTotal=Decimal('1')), 'alice')
    with pytest.raises(HTTPException) as error:
        await purchases.assign(other['id'],AssignInput(items=[Assignment(collectionCardId='c',quantity=2)]),'alice')
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await purchases.assign(batch['id'],AssignInput(items=[]),'bob')
    assert error.value.status_code == 404
    assert await performance.lot_values('bob') == []
    # Partial snapshot coverage must not fabricate a normalized batch curve.
    assert result['lots'][0]['points'] == []
    await purchases.assign(batch['id'], AssignInput(items=[], remove=['c']), 'alice')
    assert (await performance.performance('tst', 'alice'))['lots'][0]['marketValue'] == 0
    await purchases.delete_batch(batch['id'],'alice')
    assert (await db.collectioncard.find_unique(where={'id':'c'})).quantity == 3

@pytest.mark.asyncio
async def test_offer_confirm_replay_foil_wants_and_rollback(database, monkeypatch):
    db = database
    await db.execute_raw("INSERT INTO simulated_collections(id,user_id,name,raw_text,shipping,updated_at) VALUES('offer','alice','Vendor','table',1.25,NOW())")
    await db.execute_raw("INSERT INTO user_wants(id,user_id,card_scryfall_id,card_name,quantity,updated_at) VALUES('want','alice','p1','Card p1',3,NOW())")
    card = SimulatedCardAnalysisItem(cardName='Card p1',quantity=2,cardScryfallId='p1',selectedPrintingId='p1',setCode='tst',collectorNumber='p1',unitPrice=2,totalPrice=4)
    analysis = SimulatedCollectionAnalysisResponse(id='offer',name='Vendor',rawText='table',shipping=1.25,cards=[card],totalCards=2)
    monkeypatch.setattr(purchases.SimulatedCollectionService,'get_simulated_collection',AsyncMock(return_value=analysis))
    request = ConfirmOffer(acquiredOn=date(2026,2,1),paidTotal=Decimal('2.50'))
    first = await purchases.confirm_offer('offer',request,'alice')
    second = await purchases.confirm_offer('offer',request,'alice')
    assert first['batchId'] == second['batchId'] and second['alreadyConfirmed']
    assert (await db.collectioncard.find_many())[0].quantity == 2
    assert (await db.wantcard.find_unique(where={'id':'want'})).quantity == 1
    batch = await purchases.owned_batch(db,'alice',first['batchId'])
    assert batch['costTotal']==4 and batch['shipping']==0
    assert len(await db.acquisitionbatchitem.find_many()) == 1
    # Failure on a later unresolved printing rolls back inventory, wants and lot.
    await db.execute_raw("INSERT INTO simulated_collections(id,user_id,name,raw_text,updated_at) VALUES('bad','alice','Bad','table',NOW())")
    analysis.id='bad'; analysis.cards.append(SimulatedCardAnalysisItem(cardName='Unresolved',quantity=1))
    with pytest.raises(HTTPException):
        await purchases.confirm_offer('bad',request,'alice')
    assert len(await db.acquisitionbatch.find_many()) == 1
    assert (await db.collectioncard.find_many())[0].quantity == 2
    assert (await db.simulatedcollection.find_unique(where={'id':'bad'})).status == 'evaluating'

@pytest.mark.asyncio
async def test_bulk_wants_replay_and_expansion_valuation(database):
    payload=WantBulkRequest(cards=[WantCardCreate(cardName='Card p1',cardScryfallId='p1',quantity=2)])
    assert (await wants.bulk_wants(payload,'alice'))['added']==['p1']
    assert (await wants.bulk_wants(payload,'alice'))['existing']==['p1']
    assert (await database.wantcard.find_many())[0].quantity == 2
    sets=await catalog.list_sets(2000,'alice')
    assert sets[0]['totalValueEur']==2.1 and sets[0]['valuesAvailable']
    cards=await performance.card_performance('tst','alice')
    assert len(cards)==2 and cards[0]['decks']==[]
    await database.execute_raw("INSERT INTO decks(id,user_id,name,updated_at) VALUES('deck','alice','Deck',NOW())")
    await database.execute_raw("INSERT INTO deck_cards(id,deck_id,card_scryfall_id,card_name,quantity) VALUES('dc','deck','p1','Card p1',2)")
    await database.execute_raw("INSERT INTO cm_price_history(scryfall_id,finish,date,price_cents) VALUES('p1',0,CURRENT_DATE-1,200)")
    history=await performance.deck_value_history('deck','alice')
    assert history['points'][0]['value']==4
    with pytest.raises(HTTPException):
        await performance.deck_value_history('deck','bob')

@pytest.mark.asyncio
async def test_mixed_foil_purchase_and_concurrent_replay(database, monkeypatch):
    import asyncio
    from src.schemas.simulated_collections import PurchaseAnalysis, PurchaseCard
    await database.execute_raw("INSERT INTO simulated_collections(id,user_id,name,raw_text,updated_at) VALUES('mixed','alice','Mixed finishes','table',NOW())")
    await database.execute_raw("UPDATE simulated_collections SET origin='cardmarket' WHERE id='mixed'")
    analysis=SimulatedCollectionAnalysisResponse(origin='cardmarket',id='mixed',name='Mixed finishes',rawText='table',totalCards=4,
        cards=[SimulatedCardAnalysisItem(cardName='Card p1',quantity=4,selectedPrintingId='p1')],
        purchaseAnalysis=PurchaseAnalysis(cards=[
            PurchaseCard(purchaseUnitPrice=1,referenceKind='exact',marketUnitPrice=2,cardName='Card p1',quantity=1,referencePrintingId='p1'),
            PurchaseCard(purchaseUnitPrice=1,referenceKind='exact',marketUnitPrice=2,cardName='Card p1',quantity=2,referencePrintingId='p1'),
            PurchaseCard(purchaseUnitPrice=1,referenceKind='exact',marketUnitPrice=2,cardName='Card p1',quantity=1,referencePrintingId='p1',isFoil=True),
        ]))
    monkeypatch.setattr(purchases.SimulatedCollectionService,'get_simulated_collection',AsyncMock(return_value=analysis))
    request=ConfirmOffer(acquiredOn=date(2026,2,1),paidTotal=Decimal('4'))
    results=await asyncio.gather(purchases.confirm_offer('mixed',request,'alice'),purchases.confirm_offer('mixed',request,'alice'))
    assert results[0]['batchId']==results[1]['batchId']
    rows=await database.query_raw('SELECT quantity,is_foil FROM user_collections ORDER BY is_foil')
    assert rows==[{'quantity':3,'is_foil':False},{'quantity':1,'is_foil':True}]
    assert len(await database.acquisitionbatchitem.find_many())==2

@pytest.mark.asyncio
async def test_fixed_cohort_index_rebuild_and_collection_series(database):
    import importlib.util
    path=Path(__file__).parents[2]/'worker/src/services/set_price_index.py'
    spec=importlib.util.spec_from_file_location('set_index_under_test',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    await database.execute_raw("INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number) VALUES('owned','alice','p1','Card p1',2,'tst','p1')")
    await database.execute_raw("INSERT INTO cm_price_history(scryfall_id,finish,date,price_cents) VALUES('p1',0,'2026-01-01',200),('p1',0,'2026-01-12',300),('p2',0,'2026-01-01',10)")
    await module.refresh_set_indexes(database)
    members=await database.query_raw('SELECT printing_id FROM set_index_members')
    assert members==[{'printing_id':'p1'}]
    index=await database.query_raw('SELECT index_value FROM set_price_index ORDER BY captured_on')
    assert index[0]['index_value']==100 and index[-1]['index_value']==150
    # A later expensive card must not change the original basket or its denominator.
    await database.execute_raw("INSERT INTO cm_price_history(scryfall_id,finish,date,price_cents) VALUES('p2',0,'2026-01-12',10000)")
    await module.refresh_set_indexes(database)
    assert await database.query_raw('SELECT printing_id FROM set_index_members')==members
    response=await performance.performance('tst','alice')
    assert response['baselineDate']=='2026-01-01'
    assert response['ownedSeries'][0]['value']==100
    assert response['ownedSeries'][-1]['value']==150

@pytest.mark.asyncio
async def test_preferences_targets_and_global_search_scope(database):
    from src.schemas.purchases import Preferences, FunctionTargets
    await purchases.put_preferences(Preferences(priceProvider='mtggoldfish'),'alice')
    assert (await purchases.preferences('alice'))['priceProvider']=='mtggoldfish'
    assert (await purchases.preferences('bob')).priceProvider=='cardmarket'
    for owner in ['alice','bob']:
        await database.execute_raw('INSERT INTO decks(id,user_id,name,updated_at) VALUES($1,$2,\'Secret Deck\',NOW())',owner+'-deck',owner)
    result=await purchases.search('Secret','alice')
    assert [r['id'] for r in result if r['kind']=='Mazo']==['alice-deck']
    await purchases.set_function_targets('alice-deck',FunctionTargets(targets={'draw':8}),'alice')
    assert (await purchases.get_function_targets('alice-deck','alice'))['targets']=={'draw':8}
    with pytest.raises(HTTPException):
        await purchases.get_function_targets('alice-deck','bob')

@pytest.mark.asyncio
async def test_expansion_totals_resolve_inventory_once_and_preserve_finishes(database):
    """Exact identity wins; fallback never counts a row for two printings."""
    await database.execute_raw("INSERT INTO card_sets(id,code,name,set_type,card_count,search_uri,updated_at) VALUES('other-set','oth','Other','expansion',1,'',NOW())")
    for printing_id, set_id, number, price in [
        ('upper', 'set', 'A1', 3.0),
        ('lower', 'set', 'a1', 5.0),
        ('unpriced', 'set', '9', None),
        ('other-printing', 'other-set', 'p2', 10.0),
    ]:
        await database.execute_raw('INSERT INTO card_printings(id,set_id,collector_number,price_eur,updated_at) VALUES($1,$2,$3,$4,NOW())', printing_id, set_id, number, price)
    for row_id, owner, card_id, quantity, foil, set_code, number in [
        # The ID takes precedence even if stored set/collector metadata disagrees.
        ('exact', 'alice', 'p1', 2, False, 'oth', 'p2'),
        ('foil', 'alice', 'p1', 1, True, 'tst', 'p1'),
        # Catalog IDs / old unknown IDs resolve only through exact edition/number.
        ('reprint', 'alice', 'catp1', 1, False, 'tst', 'p1'),
        ('fallback', 'alice', 'catp2', 3, False, 'TST', 'P2'),
        ('case', 'alice', 'legacy-case', 2, False, 'TST', 'a1'),
        ('no-price', 'alice', 'unpriced', 1, False, None, None),
        ('unknown', 'alice', 'legacy-unknown', 100, False, None, None),
        ('foreign', 'bob', 'upper', 4, False, 'tst', 'A1'),
        ('foreign-other', 'bob', 'other-printing', 1, False, 'oth', 'p2'),
    ]:
        await database.execute_raw('INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil,set_code,collector_number) VALUES($1,$2,$3,$3,$4,$5,$6,$7)', row_id, owner, card_id, quantity, foil, set_code, number)

    sets = {row['code']: row for row in await catalog.list_sets(2000, 'alice')}
    assert sets['tst']['cardCount'] == 5
    assert sets['tst']['ownedCount'] == 4
    assert sets['tst']['completionPercentage'] == 80
    assert sets['tst']['totalValueEur'] == 10.1
    assert sets['tst']['ownedValueEur'] == 20.3
    assert sets['tst']['missingValueEur'] == 3.0
    assert sets['tst']['valuesAvailable'] is True
    assert sets['oth']['ownedCount'] == 0
    assert sets['oth']['ownedValueEur'] == 0
    assert sets['oth']['missingValueEur'] == 10.0
    # A missing inventory must not inherit other users' holdings.
    empty = {row['code']: row for row in await catalog.list_sets(2000, 'nobody')}
    assert empty['tst']['ownedCount'] == 0
    assert empty['tst']['missingValueEur'] == 10.1

@pytest.mark.asyncio
async def test_all_time_statistics_and_quantity_weighted_offer_total(database, monkeypatch):
    from src.services.price_statistics import get_history_statistics, enrich_purchase_history
    from src.schemas.simulated_collections import PurchaseAnalysis, PurchaseCard
    # Old observations must remain in statistics even with a short chart window.
    await database.execute_raw("INSERT INTO cm_price_history(scryfall_id,finish,date,price_cents) VALUES ('p1',0,'2020-01-01',100),('p1',0,CURRENT_DATE,300),('p1',1,'2020-01-01',500),('p1',1,CURRENT_DATE,900),('p2',0,'2020-01-01',900),('p2',0,CURRENT_DATE,700)")
    statistics, total = await get_history_statistics(database, [('p1', False, 1), ('p1', False, 1), ('p2', False, 1)], include_total=True)
    assert statistics[('p1', False)].mean == 2
    assert statistics[('p1', False)].standardDeviation == 1
    assert statistics[('p1', False)].observations == 2
    # Totals are 11 and 13: covariance matters, so sigma is 1 rather than 3.
    assert total.mean == 12
    assert total.standardDeviation == 1
    catalog.clear_price_history_cache()
    response = await catalog.card_price_history('catp1', 'cardmarket', 7)
    series = response.series[0]
    assert series.statistics.mean == 2
    assert series.statistics.observations == 2
    assert series.foilStatistics.mean == 7
    assert series.foilStatistics.standardDeviation == 2
    assert len(series.points) == 1
    offer = PurchaseAnalysis(cards=[
        PurchaseCard(cardName='Normal', quantity=2, historyPrintingId='p1'),
        PurchaseCard(cardName='Foil', quantity=1, historyPrintingId='p1', isFoil=True),
    ])
    await enrich_purchase_history(database, offer)
    assert offer.historyStatistics.mean == 11
    assert offer.historyStatistics.standardDeviation == 4  # totals 7, 15
    assert offer.historyCoveredCopies == 3
    offer.cards.append(PurchaseCard(cardName='Unknown', quantity=4))
    await enrich_purchase_history(database, offer)
    assert offer.historyStatistics.mean is None
    assert offer.historyStatistics.observations == 0
    assert offer.missingHistoryCopies == 4
    from src.services.simulated_collection_service import SimulatedCollectionService
    monkeypatch.setattr('src.services.simulated_collection_service.db', database)
    simple = await SimulatedCollectionService.analyze_raw_text('alice', '1 Card p1\n1 Card p2')
    assert simple.purchaseAnalysis is None
    assert simple.historyStatistics.mean == 10
    assert simple.historyStatistics.standardDeviation == 0
    assert simple.cards[0].historyStatistics.observations == 2


@pytest.mark.asyncio
async def test_statistics_use_daily_legacy_prices_and_require_common_dates(database):
    from src.services.price_statistics import get_history_statistics
    await database.execute_raw("INSERT INTO card_price_history(id,card_printing_id,provider,currency,trend_price,recorded_at) VALUES('old','p1','cardmarket','EUR',1,'2020-01-01 01:00'),('latest','p1','cardmarket','EUR',3,'2020-01-01 23:00'),('second','p1','cardmarket','EUR',7,'2020-01-02'),('wrong-currency','p1','cardmarket','USD',999,'2020-01-03')")
    stats, _ = await get_history_statistics(database, [('p1', False, 1), ('p1', True, 1)])
    assert stats[('p1', False)].mean == 5
    assert stats[('p1', False)].standardDeviation == 2
    assert stats[('p1', False)].observations == 2
    assert ('p1', True) not in stats
    # A genuine observed zero is included; current prices are never imputed.
    await database.execute_raw("INSERT INTO cm_price_history(scryfall_id,finish,date,price_cents) VALUES('p1',0,'2020-01-01',200),('p1',0,'2021-01-01',0),('p2',0,'2022-01-01',200)")
    stats, total = await get_history_statistics(database, [('p1', False, 1), ('p2', False, 1)], include_total=True)
    assert stats[('p1', False)].mean == 3  # observations 2 (MTGJSON), 7 (legacy), 0
    assert stats[('p1', False)].observations == 3
    assert total.mean is None
    assert total.observations == 0

@pytest.mark.asyncio
async def test_marvel_completion_uses_published_base_checklist(database):
    await database.execute_raw("UPDATE card_sets SET code='msc',card_count=866 WHERE id='set'")
    await database.execute_raw("UPDATE card_printings SET collector_number='1' WHERE id='p1'")
    await database.execute_raw("UPDATE card_printings SET collector_number='291' WHERE id='p2'")
    await database.execute_raw("INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number) VALUES('base','alice','p1','Card p1',2,'msc','1'),('extra','alice','p2','Card p2',3,'msc','291'),('other','bob','p1','Card p1',99,'msc','1')")
    result = (await catalog.list_sets(limit=2000, user_id='alice'))[0]
    assert (result['cardCount'], result['ownedCount'], result['completionPercentage']) == (1, 1, 100)
    assert result['totalValueEur'] == 2
    assert result['ownedValueEur'] == 4.3
    assert result['missingValueEur'] == 0
    cards = await catalog.set_cards('msc', 'alice')
    assert {c['id']: c['isBasePrinting'] for c in cards} == {'p1': True, 'p2': False}
    assert {c['id']: c['ownedQuantity'] for c in cards} == {'p1': 2, 'p2': 3}
    chart = await catalog.set_value_history('msc', days=7, user_id='alice')
    assert chart['currentTotalValue'] == 2
    assert chart['currentOwnedValue'] == 4.3

@pytest.mark.asyncio
async def test_completion_groups_alternate_printings_within_the_same_edition(database):
    await database.execute_raw("INSERT INTO card_printings(id,catalog_id,set_id,collector_number,price_eur,updated_at) VALUES('alternate','catp1','set','3',5,NOW())")
    await database.execute_raw("INSERT INTO card_sets(id,code,name,set_type,search_uri,updated_at) VALUES('foreign','oth','Other','expansion','',NOW())")
    await database.execute_raw("INSERT INTO card_printings(id,catalog_id,set_id,collector_number,updated_at) VALUES('foreign-version','catp2','foreign','1',NOW())")
    await database.execute_raw("INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number) VALUES('alt','alice','alternate','Card p1',4,'tst','3'),('foreign','alice','foreign-version','Card p2',1,'oth','1'),('bob','bob','p2','Card p2',1,'tst','p2')")
    summary = {s['code']: s for s in await catalog.list_sets(2000, 'alice')}['tst']
    assert (summary['ownedCount'], summary['cardCount'], summary['completionPercentage']) == (1, 2, 50)
    assert summary['alternativeVersionCount'] == 1
    # Owning both versions (including foil copies) must still count the card once.
    await database.execute_raw("INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil,set_code,collector_number) VALUES('base','alice','p1','Card p1',2,true,'tst','p1')")
    summary = {s['code']: s for s in await catalog.list_sets(2000, 'alice')}['tst']
    assert summary['ownedCount'] == 1
    assert summary['ownedValueEur'] == 28
    assert {s['code']: s for s in await catalog.list_sets(2000, 'nobody')}['tst']['ownedCount'] == 0
    # An alternate outside MSC's base range can cover a base card of MSC.
    await database.execute_raw("UPDATE card_sets SET code='msc' WHERE id='set'")
    await database.execute_raw("UPDATE card_printings SET collector_number='1' WHERE id='p1'")
    await database.execute_raw("UPDATE card_printings SET collector_number='291' WHERE id='alternate'")
    await database.execute_raw("DELETE FROM user_collections WHERE id='base'")
    summary = {s['code']: s for s in await catalog.list_sets(2000, 'alice')}['msc']
    assert (summary['ownedCount'], summary['cardCount'], summary['completionPercentage']) == (1, 1, 100)
    assert summary['alternativeVersionCount'] == 1

@pytest.mark.asyncio
async def test_selected_offer_acquisition_is_atomic_and_cannot_be_repeated(database, monkeypatch):
    raw = '7 Card p1\n2 Card p2'
    await database.execute_raw("INSERT INTO simulated_collections(id,user_id,name,raw_text,shipping,updated_at) VALUES('selected','alice','Partial',$1,1.25,NOW())", raw)
    analysis = SimulatedCollectionAnalysisResponse(id='selected', name='Partial', rawText=raw,shipping=1.25, cards=[
        SimulatedCardAnalysisItem(cardName='Card p1', quantity=7, selectedPrintingId='p1',unitPrice=1,totalPrice=7),
        SimulatedCardAnalysisItem(cardName='Card p2', quantity=2, selectedPrintingId='p2'),
    ])
    monkeypatch.setattr(purchases.SimulatedCollectionService, 'get_simulated_collection', AsyncMock(return_value=analysis))
    request = ConfirmOffer(acquiredOn=date(2026,10,3), paidTotal=7, cardNames=['Card p1'])
    result = await purchases.confirm_offer('selected', request, 'alice')
    inventory = await database.collectioncard.find_many()
    assert [(card.cardName, card.quantity) for card in inventory] == [('Card p1', 7)]
    remaining = await database.simulatedcollection.find_unique(where={'id': 'selected'})
    assert remaining.rawText == '2 Card p2' and remaining.status == 'evaluating'
    batch = await purchases.owned_batch(database, 'alice', result['batchId'])
    assert batch['costTotal'] == 7 and batch['shipping'] == 0
    # A concurrent request based on the old analysis must not add more copies.
    with pytest.raises(HTTPException) as error:
        await purchases.confirm_offer('selected', request, 'alice')
    assert error.value.status_code == 409
    assert (await database.collectioncard.find_many())[0].quantity == 7
    # If one selected version is unresolved, no selected copy or source row is changed.
    analysis.rawText = '2 Card p2\n1 Unresolved\n1 Other'
    analysis.cards = [SimulatedCardAnalysisItem(cardName='Card p2', quantity=2, selectedPrintingId='p2'),
                      SimulatedCardAnalysisItem(cardName='Unresolved', quantity=1),
                      SimulatedCardAnalysisItem(cardName='Other', quantity=1, selectedPrintingId='p1')]
    await database.simulatedcollection.update(where={'id': 'selected'}, data={'rawText': analysis.rawText})
    with pytest.raises(HTTPException):
        await purchases.confirm_offer('selected', ConfirmOffer(acquiredOn=date(2026,10,3), paidTotal=0, cardNames=['Card p2', 'Unresolved']), 'alice')
    assert len(await database.collectioncard.find_many()) == 1
    assert len(await database.acquisitionbatch.find_many()) == 1
    assert (await database.simulatedcollection.find_unique(where={'id': 'selected'})).rawText == analysis.rawText
