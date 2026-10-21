"""Public API tests against a disposable PostgreSQL schema; no live pricing calls."""
import asyncio
import importlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from prisma import Prisma
from src.core.auth import get_current_user_id
from src.routers import acquisitions,purchases,collection,wants,simulated_collections
from src.services import acquisition_service,simulated_collection_service,collection_service,pricing_service

URL=os.getenv('MTG_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='Requires disposable PostgreSQL via MTG_TEST_DATABASE_URL')


@pytest_asyncio.fixture
async def api(monkeypatch):
    schema='finance_'+uuid4().hex
    admin=Prisma(datasource={'url':URL})
    await admin.connect()
    await admin.execute_raw('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public')
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    parts=urlsplit(URL); params=dict(parse_qsl(parts.query));params['schema']=schema
    client=Prisma(datasource={'url':urlunsplit(parts._replace(query=urlencode(params)))})
    try:
        await client.connect()
        for migration in sorted((Path(__file__).parents[1]/'prisma/migrations').glob('*/migration.sql')):
            sql=migration.read_text()
            for statement in sql.split('-- statement-break') if '-- statement-break' in sql else sql.split(';'):
                statement='\n'.join(line for line in statement.splitlines() if not line.strip().startswith('--')).strip()
                if statement and not statement.startswith('CREATE EXTENSION'):
                    await client.execute_raw(statement.replace('gin_trgm_ops','public.gin_trgm_ops'))
        from src.core.db import db as original
        for module in list(sys.modules.values()):
            if module and getattr(module,'__name__','').startswith('src.') and getattr(module,'db',None) is original:
                monkeypatch.setattr(module,'db',client)
        await client.execute_raw("INSERT INTO card_sets(id,code,name,set_type,card_count,search_uri,updated_at) VALUES('set','tst','Test','expansion',3,'',NOW())")
        for pid,name,es,price in [('p1','Sol Ring','Anillo solar',10),('p2','Island','Isla',30),('p3','Arcane Signet','Sello arcano',None)]:
            await client.execute_raw('INSERT INTO card_catalog(id,name,name_es,normalized_name,type_line,updated_at) VALUES($1,$2,$3,$4,\'Artifact\',NOW())','cat'+pid,name,es,name.lower())
            await client.execute_raw('INSERT INTO card_printings(id,catalog_id,set_id,collector_number,price_cardmarket_trend,price_eur_foil,prices_updated_at,updated_at) VALUES($1,$2,\'set\',$1,$3,$4,NOW(),NOW())',pid,'cat'+pid,price,price*2 if price else None)
            if price:
                await client.execute_raw("INSERT INTO cm_price_history(scryfall_id,date,finish,price_cents) VALUES($1,'2025-01-01',0,$2),($1,'2025-01-01',1,$3)",pid,price*100,price*200)
        app=FastAPI();app.include_router(acquisitions.router,prefix='/api');app.include_router(purchases.router,prefix='/api');app.include_router(collection.router,prefix='/api');app.include_router(wants.router,prefix='/api');app.include_router(simulated_collections.router,prefix='/api')
        app.dependency_overrides[get_current_user_id]=lambda:'alice'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as http:
            yield http,client,app
    finally:
        if client.is_connected():await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()


async def own(db,pid,quantity=1,foil=False,cid=None):
    name={'p1':'Sol Ring','p2':'Island','p3':'Arcane Signet'}[pid]
    # Existing inventory is a correction, not a new acquisition.
    async with db.tx() as tx:
        await tx.query_raw("SELECT set_config('mtg.suppress_acquisition','on',true)")
        await tx.execute_raw('INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil) VALUES($1,\'alice\',$2,$3,$4,$5)',cid or pid,pid,name,quantity,foil)


async def history(http,total='40.00',date='2025-01-02',raw='1 Sol Ring\n1 Island',selections=None):
    return await http.post('/api/acquisitions/historical/quote',json=dict(rawText=raw,name='Precon antiguo',kind='precon',origin='other',seller='Tienda local',
        total=total,acquiredOn=date,selections=selections or [dict(line=1,collectionCardId='p1',quantity=1),dict(line=2,collectionCardId='p2',quantity=1)]))


@pytest.mark.asyncio
async def test_old_purchase_captures_history_provenance_and_never_adds_inventory(api):
    http,db,_=api
    await own(db,'p1');await own(db,'p2')
    validation=await http.post('/api/acquisitions/validate-list',json={'rawText':'1 Anillo solar\n1 Isla'})
    assert validation.json()['valid']
    quote=await history(http);assert quote.status_code==200,quote.text
    data=quote.json();assert data['total']=='40.00'
    assert [i['costAmount'] for i in data['items']]==['10','30']
    result=await http.post('/api/acquisitions/confirm',json={'quoteId':data['quoteId']});assert result.status_code==200,result.text
    replay=await http.post('/api/acquisitions/confirm',json={'quoteId':data['quoteId']})
    assert replay.json()['alreadyConfirmed']
    batch=await http.get('/api/acquisitions/'+result.json()['batchId'])
    assert batch.json()['investment']==40 and batch.json()['profit']==0
    assert batch.json()['items'][0]['referenceObservedOn']=='2025-01-01'
    provenance=await http.get('/api/acquisitions/provenance/p1')
    assert provenance.json()[0]['seller']=='Tienda local'
    # Prices may change while the acquisition reference remains immutable.
    await db.execute_raw("UPDATE card_printings SET price_cardmarket_trend=20 WHERE id='p1'")
    detail=(await http.get('/api/acquisitions/'+result.json()['batchId'])).json()
    assert detail['profit']==10 and detail['initialValue']==40
    assert detail['marketChange']==10
    duplicate=await history(http);assert duplicate.status_code==422
    assert (await db.query_raw('SELECT SUM(quantity)::int AS n FROM user_collections'))[0]['n']==2


@pytest.mark.asyncio
async def test_validation_errors_missing_fields_unknown_history_and_budget_warning(api):
    http,db,_=api
    await own(db,'p1')
    invalid=await http.post('/api/acquisitions/validate-list',json={'rawText':'0 Sol Ring\n1 Sol Rign\n-2 Island'})
    assert not invalid.json()['valid']
    assert invalid.json()['lines'][1]['suggestions']==['Sol Ring']
    for missing in ('total','acquiredOn'):
        payload=dict(rawText='1 Sol Ring',name='Antigua',total=100,acquiredOn='2026-01-01',selections=[dict(line=1,collectionCardId='p1',quantity=1)])
        del payload[missing]
        assert (await http.post('/api/acquisitions/historical/quote',json=payload)).status_code==422
    quote=await history(http,total='601',raw='1 Sol Ring',selections=[dict(line=1,collectionCardId='p1',quantity=1)],date='2024-01-01')
    assert quote.json()['items'][0]['referenceUnitPrice'] is None
    assert quote.json()['items'][0]['costAmount'] is None
    rejected=await http.post('/api/acquisitions/confirm',json={'quoteId':quote.json()['quoteId']})
    assert rejected.status_code==409
    accepted=await http.post('/api/acquisitions/confirm',json={'quoteId':quote.json()['quoteId'],'acknowledgeBudget':True})
    assert accepted.status_code==200
    ledger=(await http.get('/api/acquisitions/expenses?year=2024')).json()
    assert ledger['budget']['excess']==1
    annulled=await http.post('/api/acquisitions/'+accepted.json()['batchId']+'/annul');assert annulled.status_code==200
    assert (await http.get('/api/acquisitions/expenses?year=2024')).json()['budget']['spent']==0
    assert (await http.post('/api/acquisitions/validate-list',json={'rawText':'1 Sol Ring'})).json()['valid']

async def offer(api,monkeypatch,origin='other',prices=(10,30),shipping=0):
    from src.schemas.simulated_collections import SimulatedCollectionAnalysisResponse,SimulatedCardAnalysisItem,PurchaseAnalysis,PurchaseCard
    from src.services.acquisition_finance import valuation_revision
    _,db,_=api
    await db.simulatedcollection.create(data={'id':'offer','userId':'alice','name':'Compra nueva','rawText':'1 Sol Ring\n1 Island','origin':origin,'seller':'Tienda','shipping':shipping})
    cards=[]; paid=[]
    for pid,name,price in [('p1','Sol Ring',prices[0]),('p2','Island',prices[1])]:
        await db.simulatedcard.create(data={'simulatedCollectionId':'offer','cardScryfallId':pid,'cardName':name,'quantity':1,'selectedPrintingId':pid})
        cards.append(SimulatedCardAnalysisItem(cardName=name,quantity=1,selectedPrintingId=pid,unitPrice=price,totalPrice=price))
        paid.append(PurchaseCard(cardName=name,quantity=1,referencePrintingId=pid,referenceKind='exact',marketUnitPrice=price,purchaseUnitPrice=price))
    analysis=SimulatedCollectionAnalysisResponse(id='offer',name='Compra nueva',rawText='1 Sol Ring\n1 Island',origin=origin,seller='Tienda',shipping=shipping,cards=cards,purchaseAnalysis=PurchaseAnalysis(cards=paid),printingOverrides={'sol ring':'p1','island':'p2'})
    analysis.valuationRevision=valuation_revision(analysis)
    async def read(*args):return analysis
    monkeypatch.setattr(simulated_collection_service.SimulatedCollectionService,'get_simulated_collection',read)
    return analysis


@pytest.mark.asyncio
async def test_inventory_purchase_freezes_trend_is_atomic_idempotent_and_user_scoped(api):
    http,db,app=api
    quote=(await http.post('/api/acquisitions/inventory/quote',json={'printingId':'p1','quantity':2,'isFoil':True,'acquiredOn':'2026-01-01'})).json()
    assert quote['total']=='40.00'
    await db.execute_raw("UPDATE card_printings SET price_eur_foil=25 WHERE id='p1'")
    responses=await asyncio.gather(*[http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']}) for _ in range(2)])
    assert all(r.status_code==200 for r in responses)
    assert len({r.json()['batchId'] for r in responses})==1
    batch_id=responses[0].json()['batchId']
    detail=(await http.get('/api/acquisitions/'+batch_id)).json()
    assert detail['investment']==40 and detail['initialValue']==40 and detail['profit']==10 and detail['roi']==25
    assert (await db.query_raw('SELECT quantity FROM user_collections'))[0]['quantity']==2
    app.dependency_overrides[get_current_user_id]=lambda:'bob'
    assert (await http.get('/api/acquisitions/'+batch_id)).status_code==404
    assert (await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']})).status_code==404
    assert (await http.get('/api/acquisitions/expenses?year=2026')).json()['purchases']==[]
    assert (await http.patch('/api/acquisitions/'+batch_id,json={'name':'Bad','total':1,'acquiredOn':'2026-01-01'})).status_code==404


@pytest.mark.asyncio
async def test_offer_revision_stale_state_and_cardmarket_overhead_and_edit(api,monkeypatch):
    http,db,_=api
    analysis=await offer(api,monkeypatch,'cardmarket',shipping=5)
    rejected=await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01','valuationRevision':'stale'})
    assert rejected.status_code==409
    quote=(await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01','valuationRevision':analysis.valuationRevision})).json()
    assert quote['total']=='47.55' and quote['fee']=='2.55'
    await db.execute_raw("UPDATE simulated_collections SET seller='Changed' WHERE id='offer'")
    assert (await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']})).status_code==409
    assert (await db.query_raw('SELECT COUNT(*)::int AS n FROM user_collections'))[0]['n']==0
    await db.execute_raw("UPDATE simulated_collections SET seller='Tienda' WHERE id='offer'")
    result=await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']});assert result.status_code==200,result.text
    batch_id=result.json()['batchId'];detail=(await http.get('/api/acquisitions/'+batch_id)).json()
    assert sum(i['costAmount'] for i in detail['items'])==pytest.approx(47.55)
    assert detail['profit']==-7.55 and detail['roi']==-15.88
    revised={i['id']:'5.00' for i in detail['items']}
    edit=await http.patch('/api/acquisitions/'+batch_id,json={'name':'Corregida','acquiredOn':'2026-01-01','total':'10','shipping':'2','purchasePrices':revised})
    assert edit.status_code==200,edit.text
    assert edit.json()['investment']==12.9 and edit.json()['initialValue']==40
    assert sum(i['purchaseAmount'] for i in edit.json()['items'])==10
    assert (await db.query_raw('SELECT SUM(quantity)::int AS n FROM user_collections'))[0]['n']==2


@pytest.mark.asyncio
async def test_cardmarket_offer_uses_imported_prices_and_saved_printing_when_cart_identity_is_incomplete(api,monkeypatch):
    http,_,_=api
    analysis=await offer(api,monkeypatch,'cardmarket',prices=(1.25,2.50))
    # Simulates Cardmarket set slugs that cannot be matched to the local set
    # name; the imported offer still has its explicitly selected exact prints.
    for row in analysis.purchaseAnalysis.cards:
        row.referencePrintingId=None
    quote=await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01'})
    assert quote.status_code==200,quote.text
    data=quote.json()
    assert [item['printingId'] for item in data['items']]==['p1','p2']
    assert [item['purchaseAmount'] for item in data['items']]==['1.25','2.50']
    assert data['subtotal']=='3.75'


@pytest.mark.asyncio
async def test_historical_date_edit_does_not_invent_baseline_or_recharge_cardmarket(api):
    http,db,_=api
    await own(db,'p1')
    response=await history(http,total='0',raw='1 Sol Ring',selections=[dict(line=1,collectionCardId='p1',quantity=1)])
    result=(await http.post('/api/acquisitions/confirm',json={'quoteId':response.json()['quoteId']})).json()
    detail=(await http.get('/api/acquisitions/'+result['batchId'])).json()
    assert detail['roi'] is None and detail['profit']==10
    edit=await http.patch('/api/acquisitions/'+result['batchId'],json={'name':'Anterior','acquiredOn':'2024-01-01','total':40})
    assert edit.status_code==200,edit.text
    assert edit.json()['initialValue'] is None and edit.json()['items'][0]['costAmount'] is None
    assert edit.json()['marketChange'] is None
    aliases=await http.post('/api/acquisitions/validate-list',json={'rawText':'1 Sol Ring\n1 Anillo solar'})
    assert not aliases.json()['valid']

@pytest.mark.asyncio
async def test_partial_cardmarket_import_charges_shipping_and_fixed_fee_once_and_rebalances_edits(api,monkeypatch):
    from src.schemas.simulated_collections import SimulatedCardAnalysisItem
    http,db,_=api
    analysis=await offer(api,monkeypatch,'cardmarket',prices=(.01,.09),shipping=.01)
    first=(await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01','cardNames':['Sol Ring']})).json()
    assert first['shipping']=='0.01' and first['fee']=='0.30'
    result=await http.post('/api/acquisitions/confirm',json={'quoteId':first['quoteId']});assert result.status_code==200,result.text
    first_id=result.json()['batchId']
    analysis.cards=[analysis.cards[1]];analysis.purchaseAnalysis.cards=[analysis.purchaseAnalysis.cards[1]]
    analysis.rawText='1 Island';analysis.printingOverrides={'island':'p2'}
    second=(await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01'})).json()
    assert second['shipping']=='0.00' and second['fee']=='0.01'
    result=await http.post('/api/acquisitions/confirm',json={'quoteId':second['quoteId']});assert result.status_code==200,result.text
    all_rows=(await http.get('/api/acquisitions/expenses?year=2026')).json()['purchases']
    assert sum(p['shipping'] for p in all_rows)==.01
    assert sum(p['fee'] for p in all_rows)==pytest.approx(.31)
    assert (await db.simulatedcollection.find_unique(where={'id':'offer'})).paidTotal==__import__('decimal').Decimal('0.42')
    # Editing one slice recalculates the same purchase's cumulative commission, including its siblings.
    detail=(await http.get('/api/acquisitions/'+first_id)).json()
    edited=await http.patch('/api/acquisitions/'+first_id,json={'name':'Corrección','acquiredOn':'2026-01-01','total':1,'shipping':.01,'purchasePrices':{detail['items'][0]['id']:'1.00'}})
    assert edited.status_code==200,edited.text
    all_rows=(await http.get('/api/acquisitions/expenses?year=2026')).json()['purchases']
    assert sum(p['fee'] for p in all_rows)==pytest.approx(.36)


@pytest.mark.asyncio
async def test_purchase_transaction_rolls_back_inventory_wants_lot_and_notices(api,monkeypatch):
    from prisma.errors import RawQueryError
    http,db,_=api
    await offer(api,monkeypatch,'whatsapp')
    await db.execute_raw("INSERT INTO user_wants(id,user_id,card_scryfall_id,card_name,quantity,updated_at) VALUES('want','alice','p1','Sol Ring',1,NOW())")
    await db.execute_raw("INSERT INTO decks(id,user_id,name,updated_at) VALUES('deck','alice','Deck',NOW())")
    await db.execute_raw("INSERT INTO deck_cards(id,deck_id,card_scryfall_id,card_name,quantity) VALUES('dc','deck','p1','Sol Ring',1)")
    quote=(await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01'})).json()
    await db.execute_raw("ALTER TABLE user_collections ADD CONSTRAINT reject_second CHECK(card_scryfall_id <> 'p2')")
    with pytest.raises(RawQueryError):
        await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']})
    assert (await db.query_raw('SELECT COUNT(*)::int AS n FROM acquisition_batches'))[0]['n']==0
    assert (await db.query_raw('SELECT COUNT(*)::int AS n FROM user_collections'))[0]['n']==0
    assert (await db.wantcard.find_unique(where={'id':'want'})).quantity==1
    assert (await db.query_raw('SELECT COUNT(*)::int AS n FROM deck_assignment_notices'))[0]['n']==0
    assert (await db.query_raw('SELECT consumed_batch_id FROM acquisition_quotes'))[0]['consumed_batch_id'] is None
    await db.execute_raw('ALTER TABLE user_collections DROP CONSTRAINT reject_second')
    assert (await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']})).status_code==200
    assert (await db.query_raw('SELECT COUNT(*)::int AS n FROM deck_assignment_notices'))[0]['n']==1


@pytest.mark.asyncio
async def test_two_historical_reviews_cannot_claim_the_same_physical_copy(api):
    http,db,_=api
    await own(db,'p1')
    selections=[dict(line=1,collectionCardId='p1',quantity=1)]
    quotes=await asyncio.gather(history(http,total=10,raw='1 Sol Ring',selections=selections),history(http,total=10,raw='1 Sol Ring',selections=selections))
    responses=await asyncio.gather(*(http.post('/api/acquisitions/confirm',json={'quoteId':q.json()['quoteId']}) for q in quotes))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert (await db.query_raw('SELECT COUNT(*)::int AS n FROM acquisition_batches'))[0]['n']==1

@pytest.mark.asyncio
async def test_historical_list_resolves_explicit_printing_foil_and_rejects_blank_product(api):
    http,db,_=api
    await own(db,'p1',foil=True,cid='foil');await own(db,'p1',foil=False,cid='normal')
    validation=await http.post('/api/acquisitions/validate-list',json={'rawText':'Deck\n1x Anillo solar (TST) p1 *F*'})
    assert validation.json()['valid']
    assert [c['collectionCardId'] for c in validation.json()['lines'][0]['candidates']]==['foil']
    response=await http.post('/api/acquisitions/historical/quote',json={'rawText':'1 Sol Ring','name':'  ','total':10,'acquiredOn':'2026-01-01','selections':[{'line':1,'collectionCardId':'normal','quantity':1}]})
    assert response.status_code==422

@pytest.mark.asyncio
@pytest.mark.parametrize('origin',['other','whatsapp','in_person'])
async def test_non_cardmarket_offer_always_uses_visible_trend_without_overhead(api,monkeypatch,origin):
    http,db,_=api
    analysis=await offer(api,monkeypatch,origin,shipping=7)
    result=await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01','purchasePrices':{'0':999}})
    assert result.status_code==200,result.text
    quote=result.json()
    assert quote['costBasis']=='trend' and quote['total']=='40.00' and quote['shipping']=='0.00' and quote['fee']=='0.00'
    await db.execute_raw('UPDATE card_printings SET price_cardmarket_trend=100')
    saved=await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']});assert saved.status_code==200,saved.text
    detail=(await http.get('/api/acquisitions/'+saved.json()['batchId'])).json()
    assert detail['investment']==40 and detail['initialValue']==40


@pytest.mark.asyncio
async def test_annulled_copies_can_be_linked_to_legacy_lots_and_composition_is_preserved(api):
    http,db,_=api
    await own(db,'p1')
    quote=await history(http,total=10,raw='1 Sol Ring',selections=[dict(line=1,collectionCardId='p1',quantity=1)])
    batch=(await http.post('/api/acquisitions/confirm',json={'quoteId':quote.json()['quoteId']})).json()['batchId']
    assert (await http.post('/api/acquisition-batches/'+batch+'/assign',json={'items':[]})).status_code==409
    await http.post('/api/acquisitions/'+batch+'/annul')
    manual=await http.post('/api/acquisition-batches',json={'acquiredOn':'2025-01-02','costTotal':10})
    assert manual.status_code==201,manual.text
    assigned=await http.post('/api/acquisition-batches/'+manual.json()['id']+'/assign',json={'items':[{'collectionCardId':'p1','quantity':1}]})
    assert assigned.status_code==200,assigned.text


@pytest.mark.asyncio
async def test_multiple_lots_show_provenance_and_survive_inventory_deletion(api):
    http,db,_=api
    batches=[]
    for quantity in (1,2):
        quote=(await http.post('/api/acquisitions/inventory/quote',json={'printingId':'p1','quantity':quantity,'acquiredOn':'2026-01-01'})).json()
        saved=await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']});assert saved.status_code==200,saved.text
        batches.append(saved.json()['batchId'])
    collection_id=(await db.query_raw('SELECT id FROM user_collections'))[0]['id']
    provenance=(await http.get('/api/acquisitions/provenance/'+collection_id)).json()
    assert sorted(p['quantity'] for p in provenance)==[1,2]
    await db.execute_raw('DELETE FROM user_collections WHERE id=$1',collection_id)
    details=[(await http.get('/api/acquisitions/'+batch)).json() for batch in batches]
    assert sorted(d['copies'] for d in details)==[1,2]
    assert sum(d['investment'] for d in details)==30

@pytest.mark.asyncio
async def test_tmnt_precon_reports_unresolved_printings_instead_of_silently_blocking_selection(api):
    http,db,_=api
    raw=(Path(__file__).parent/'fixtures/acquisitions/tmnt-precon.txt').read_text()
    # Representative imported inventory: canonical split name and a pending edition.
    for number,line in enumerate(raw.splitlines(),1):
        quantity,name=line.split(' ',1)
        canonical='Double Jump // Flying Kick' if name=='Double Jump/Flying Kick' else name
        pid=f'tmnt-{number}'
        if name!='Foot Chopper':
            await db.execute_raw('INSERT INTO card_catalog(id,name,normalized_name,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(name) DO NOTHING','cat-'+pid,canonical,canonical.lower())
            await db.execute_raw('INSERT INTO card_printings(id,catalog_id,set_id,collector_number,updated_at) VALUES($1,$2,\'set\',$1,NOW())',pid,(await db.query_raw('SELECT id FROM card_catalog WHERE name=$1',canonical))[0]['id'])
        await db.execute_raw('INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil) VALUES($1,\'alice\',$2,$3,$4,false)',pid,pid if name!='Foot Chopper' else 'pending:foot-chopper',canonical,int(quantity))
    result=await http.post('/api/acquisitions/validate-list',json={'rawText':raw})
    assert result.status_code==200,result.text
    data=result.json()
    assert len(data['lines'])==93 and sum(line['quantity'] for line in data['lines'])==100
    assert data['lines'][23]['name']=='Double Jump/Flying Kick' and data['lines'][23]['error'] is None
    problems=[line for line in data['lines'] if line['error']]
    assert [(line['line'],line['name'],line.get('errorCode')) for line in problems]==[(35,'Foot Chopper','printing_unresolved')]
    assert not data['valid']

@pytest.mark.asyncio
async def test_validation_distinguishes_catalog_pending_print_short_stock_and_linked_copies(api):
    http,db,_=api
    await own(db,'p1',cid='print')
    await db.execute_raw("INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number,is_foil) VALUES('catalog','alice','catp2','Island',1,'tst','p2',false),('pending','alice','catp3','Arcane Signet',1,NULL,NULL,false)")
    response=(await http.post('/api/acquisitions/validate-list',json={'rawText':'1 Isla\n1 Sello arcano\n2 Sol Ring\n1 Inventada'})).json()
    assert [line['errorCode'] for line in response['lines']]==[None,'printing_unresolved','insufficient_quantity','name_not_owned']
    assert response['lines'][0]['candidates'][0]['printingId']=='p2'
    assert response['lines'][2]['ownedQuantity']==1 and response['lines'][2]['availableQuantity']==1
    quote=await history(http,total=10,raw='1 Sol Ring',selections=[dict(line=1,collectionCardId='print',quantity=1)])
    saved=await http.post('/api/acquisitions/confirm',json={'quoteId':quote.json()['quoteId']});assert saved.status_code==200
    line=(await http.post('/api/acquisitions/validate-list',json={'rawText':'1 Sol Ring'})).json()['lines'][0]
    assert line['errorCode']=='copies_already_linked' and line['availableQuantity']==0

@pytest.mark.asyncio
async def test_tmnt_fixture_reproduces_the_observed_local_inventory_diagnostics(api):
    import json
    http,db,_=api
    fixtures=Path(__file__).parent/'fixtures/acquisitions'
    raw=(fixtures/'tmnt-precon.txt').read_text()
    expected=json.loads((fixtures/'tmnt-inventory-diagnostics.json').read_text())
    absent={p['name'] for p in expected['problems'] if p['errorCode']=='name_not_owned'}
    for number,line in enumerate(raw.splitlines(),1):
        qty,name=line.split(' ',1)
        if name in absent:continue
        canonical='Double Jump // Flying Kick' if name=='Double Jump/Flying Kick' else name
        pending=name=='Double Jump/Flying Kick'
        pid=f'observed-{number}'
        await db.execute_raw('INSERT INTO card_catalog(id,name,normalized_name,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(name) DO NOTHING','cat-'+pid,canonical,canonical.lower())
        catalog_id=(await db.query_raw('SELECT id FROM card_catalog WHERE name=$1',canonical))[0]['id']
        if not pending:
            await db.execute_raw('INSERT INTO card_printings(id,catalog_id,set_id,collector_number,updated_at) VALUES($1,$2,\'set\',$1,NOW())',pid,catalog_id)
        await db.execute_raw('INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil) VALUES($1,\'alice\',$2,$3,$4,false)',pid,catalog_id if pending else pid,canonical,int(qty))
    response=await http.post('/api/acquisitions/validate-list',json={'rawText':raw})
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data['lines'])==expected['lines'] and sum(i['quantity'] for i in data['lines'])==expected['copies']
    problems=[{key:line[key] for key in ('line','name','errorCode')} for line in data['lines'] if line['error']]
    assert problems==expected['problems']
    assert len([line for line in data['lines'] if not line['error']])==88
    # A missing price does not block linking an otherwise fully resolved printing.
    assert data['lines'][34]['name']=='Foot Chopper' and data['lines'][34]['error'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('origin',['other','whatsapp','in_person','cardmarket'])
@pytest.mark.parametrize('selected_only',[False,True],ids=['whole-offer','selected-card'])
async def test_evaluate_offer_collection_commit_reconciles_wants_and_replay_once(api,monkeypatch,origin,selected_only):
    http,db,app=api
    await offer(api,monkeypatch,origin)
    # A want may reference a catalog card or another printing of the same name.
    await db.execute_raw("""INSERT INTO user_wants(id,user_id,card_scryfall_id,card_name,quantity,updated_at) VALUES
      ('ring','alice','catp1','Sol Ring',1,NOW()),
      ('island','alice','p2','Island',2,NOW()),
      ('unselected','alice','p3','Arcane Signet',1,NOW()),
      ('other-user','bob','p1','Sol Ring',1,NOW())""")

    async def desired():
        result=await http.get('/api/wants')
        assert result.status_code==200,result.text
        return {card['cardName']:card['quantity'] for card in result.json()}

    before={'Arcane Signet':1,'Island':2,'Sol Ring':1}
    assert await desired()==before
    request={'acquiredOn':'2026-01-01'}
    if selected_only:request['cardNames']=['Sol Ring']
    review=await http.post('/api/acquisitions/offers/offer/quote',json=request)
    assert review.status_code==200,review.text
    # Evaluating/reviewing an offer is not an acquisition yet.
    assert await desired()==before
    saved=await http.post('/api/acquisitions/confirm',json={'quoteId':review.json()['quoteId']})
    assert saved.status_code==200,saved.text
    expected={'Arcane Signet':1,'Island':2 if selected_only else 1}
    assert await desired()==expected
    inventory=await http.get('/api/collection')
    assert inventory.status_code==200,inventory.text
    assert {card['cardName']:card['quantity'] for card in inventory.json()}==(
        {'Sol Ring':1} if selected_only else {'Sol Ring':1,'Island':1})
    replay=await http.post('/api/acquisitions/confirm',json={'quoteId':review.json()['quoteId']})
    assert replay.status_code==200 and replay.json()['alreadyConfirmed']
    assert replay.json()['batchId']==saved.json()['batchId']
    assert await desired()==expected
    assert (await http.get('/api/collection')).json()==inventory.json()
    app.dependency_overrides[get_current_user_id]=lambda:'bob'
    assert await desired()=={'Sol Ring':1}
    assert (await http.get('/api/collection')).json()==[]


@pytest.mark.asyncio
async def test_offer_listing_refreshes_inventory_wants_and_prices_between_requests(api,monkeypatch):
    http,db,app=api
    await offer(api,monkeypatch)
    await db.execute_raw("INSERT INTO user_wants(id,user_id,card_scryfall_id,card_name,quantity,updated_at) VALUES('listed-want','alice','p1','Sol Ring',1,NOW())")
    initial=await http.get('/api/simulated-collections')
    assert initial.status_code==200,initial.text
    assert len(initial.json())==1 and initial.json()[0]['totalEconomicValue']==40
    assert initial.json()[0]['alreadyOwnedCardsCount']==0
    quote=(await http.post('/api/acquisitions/offers/offer/quote',json={'acquiredOn':'2026-01-01','cardNames':['Sol Ring']})).json()
    saved=await http.post('/api/acquisitions/confirm',json={'quoteId':quote['quoteId']})
    assert saved.status_code==200,saved.text
    assert (await http.get('/api/wants')).json()==[]
    # A subsequent read must reflect updated price and the remaining offer,
    # rather than holding a cached user/catalog snapshot from the initial read.
    await db.execute_raw("UPDATE card_printings SET price_cardmarket_trend=35 WHERE id='p2'")
    fresh=await http.get('/api/simulated-collections')
    assert fresh.status_code==200,fresh.text
    assert fresh.json()[0]['totalCards']==1 and fresh.json()[0]['totalEconomicValue']==35
    app.dependency_overrides[get_current_user_id]=lambda:'bob'
    assert (await http.get('/api/simulated-collections')).json()==[]
