"""Purchase provenance and atomic offer confirmation. Every write is user-scoped."""
from uuid import uuid4
from datetime import timedelta, date
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException, Query
from src.core.auth import get_current_user_id
from src.core.db import db
from src.schemas.simulated_collections import SimulatedCollectionCreateRequest, OfferLinkImportRequest, SimulatedCollectionAnalysisResponse
from src.schemas.purchases import BatchInput, AssignInput, OfferUpdate, ConfirmOffer, Preferences, FunctionTargets
from src.services.card_utils import normalize_card_name
from src.services.simulated_collection_service import SimulatedCollectionService
from src.services.offer_card_selection import remaining_offer_text

router = APIRouter(tags=['purchases'])

BATCH_SELECT = '''SELECT id, set_code AS "setCode", kind, acquired_on::text AS "acquiredOn",
 cost_total::float AS "costTotal", shipping::float, currency, notes, origin, annulled_at AS "annulledAt", offer_id AS "offerId"
 FROM acquisition_batches WHERE user_id=$1'''

def require_manual_batch(batch):
    if batch['origin']!='manual' or batch.get('annulledAt'):
        raise HTTPException(409,'Esta compra conserva su composición original. Edítala o anúlala desde Gastos e inversión.')


async def snapshot_manual_batch(tx,batch_id,on_date,total):
    from src.services.acquisition_service import historical_prices
    from src.services.acquisition_finance import distribute_cost
    from decimal import Decimal
    items=await tx.query_raw('''SELECT i.id,i.printing_id,i.quantity,i.is_foil,COALESCE(i.card_name,c.card_name,cat.name) AS name
        FROM acquisition_batch_items i LEFT JOIN user_collections c ON c.id=i.collection_card_id
        LEFT JOIN card_printings p ON p.id=i.printing_id LEFT JOIN card_catalog cat ON cat.id=p.catalog_id
        WHERE i.batch_id=$1 ORDER BY i.id''',batch_id)
    history=await historical_prices(tx,[dict(printingId=i['printing_id'],isFoil=i['is_foil'],quantity=i['quantity']) for i in items],on_date)
    refs=[history.get((i['printing_id'],i['is_foil'])) for i in items]
    weights=[Decimal(str(h['price']))*i['quantity'] if h else None for i,h in zip(items,refs)]
    for item,h,amount in zip(items,refs,distribute_cost(total,weights)):
        await tx.execute_raw('''UPDATE acquisition_batch_items SET card_name=$2,reference_unit_price=$3::numeric,
           reference_observed_on=$4::date,cost_amount=$5::numeric,cost_basis='allocated',acquired_on=$6::date WHERE id=$1''',
           item['id'],item['name'],str(h['price']) if h else None,h['day'] if h else None,str(amount) if amount is not None else None,on_date.isoformat())


async def owned_batch(client, user_id, batch_id, lock=False):
    rows = await client.query_raw(BATCH_SELECT + ' AND id=$2' + (' FOR UPDATE' if lock else ''), user_id, batch_id)
    if not rows:
        raise HTTPException(404, 'Lote no encontrado')
    return rows[0]

@router.get('/me/preferences', response_model=Preferences)
async def preferences(user_id: str = Depends(get_current_user_id)):
    rows = await db.query_raw('SELECT price_provider AS "priceProvider" FROM user_preferences WHERE user_id=$1', user_id)
    return rows[0] if rows else Preferences()

@router.put('/me/preferences', response_model=Preferences)
async def put_preferences(payload: Preferences, user_id: str = Depends(get_current_user_id)):
    await db.execute_raw('INSERT INTO user_preferences(user_id,price_provider) VALUES ($1,$2) ON CONFLICT(user_id) DO UPDATE SET price_provider=EXCLUDED.price_provider', user_id, payload.priceProvider)
    return payload

@router.get('/acquisition-batches')
async def batches(setCode: str | None = None, user_id: str = Depends(get_current_user_id)):
    return await db.query_raw(BATCH_SELECT + (' AND (lower(set_code)=lower($2) OR EXISTS(SELECT 1 FROM acquisition_batch_items i JOIN card_printings p ON p.id=i.printing_id JOIN card_sets s ON s.id=p.set_id WHERE i.batch_id=acquisition_batches.id AND lower(s.code)=lower($2)))' if setCode else '') + ' ORDER BY acquired_on DESC', *([user_id, setCode] if setCode else [user_id]))

@router.post('/acquisition-batches', status_code=201)
async def create_batch(payload: BatchInput, user_id: str = Depends(get_current_user_id)):
    from src.services.acquisition_service import user_lock,enforce_budget
    batch_id = str(uuid4())
    async with db.tx() as tx:
        await user_lock(tx,user_id)
        if payload.currency=='EUR':
            await enforce_budget(tx,user_id,dict(acquiredOn=payload.acquiredOn.isoformat(),total=str(payload.costTotal+payload.shipping),costBasis='paid'),payload.acknowledgeBudget)
        await tx.execute_raw('''INSERT INTO acquisition_batches(id,user_id,set_code,kind,acquired_on,cost_total,shipping,currency,notes,name)
         VALUES($1,$2,$3,$4,$5::date,$6::numeric,$7::numeric,$8,$9,$10)''', batch_id, user_id, payload.setCode.lower() if payload.setCode else None, payload.kind, payload.acquiredOn.isoformat(), str(payload.costTotal), str(payload.shipping), payload.currency, payload.notes,payload.notes or 'Compra')
    return await owned_batch(db, user_id, batch_id)

@router.put('/acquisition-batches/{batch_id}')
async def update_batch(batch_id: str, payload: BatchInput, user_id: str = Depends(get_current_user_id)):
    from src.services.acquisition_service import user_lock,enforce_budget
    async with db.tx() as tx:
        await user_lock(tx,user_id)
        batch=await owned_batch(tx,user_id,batch_id,True)
        require_manual_batch(batch)
        if payload.currency=='EUR':
            await enforce_budget(tx,user_id,dict(acquiredOn=payload.acquiredOn.isoformat(),total=str(payload.costTotal+payload.shipping),costBasis='paid'),payload.acknowledgeBudget,batch_id)
        await tx.execute_raw('''UPDATE acquisition_batches SET set_code=$3,kind=$4,acquired_on=$5::date,cost_total=$6::numeric,shipping=$7::numeric,currency=$8,notes=$9,name=$10,updated_at=NOW()
         WHERE id=$1 AND user_id=$2''', batch_id, user_id, payload.setCode.lower() if payload.setCode else None, payload.kind, payload.acquiredOn.isoformat(), str(payload.costTotal), str(payload.shipping), payload.currency, payload.notes,payload.notes or 'Compra')
        await snapshot_manual_batch(tx,batch_id,payload.acquiredOn,payload.costTotal+payload.shipping)
    return await owned_batch(db, user_id, batch_id)

@router.delete('/acquisition-batches/{batch_id}')
async def delete_batch(batch_id: str, user_id: str = Depends(get_current_user_id)):
    async with db.tx() as tx:
        batch = await owned_batch(tx, user_id, batch_id, True)
        require_manual_batch(batch)
        await tx.execute_raw('DELETE FROM acquisition_batches WHERE id=$1 AND user_id=$2', batch_id, user_id)
    return {'deleted': True}

@router.get('/acquisition-batches/{batch_id}/candidates')
async def candidates(batch_id: str, start: date | None = None, end: date | None = None, user_id: str = Depends(get_current_user_id)):
    batch = await owned_batch(db, user_id, batch_id)
    if start and end and start > end:
        raise HTTPException(422, 'El rango de fechas no es válido')
    # Dates only propose candidates. Assignment always requires an explicit selection.
    return await db.query_raw('''SELECT c.id, c.card_name AS "cardName", c.quantity,
      c.acquired_at::text AS "acquiredAt", c.is_foil AS "isFoil",
      COALESCE((SELECT SUM(i.quantity)::int FROM acquisition_batch_items i JOIN acquisition_batches b ON b.id=i.batch_id WHERE b.annulled_at IS NULL AND i.collection_card_id=c.id AND i.batch_id != $5),0) AS "assignedElsewhere",
      COALESCE((SELECT SUM(i.quantity)::int FROM acquisition_batch_items i WHERE i.collection_card_id=c.id AND i.batch_id = $5),0) AS "assignedHere"
      FROM user_collections c WHERE c.user_id=$1 AND ($2::text IS NULL OR lower(c.set_code)=lower($2))
      AND ($3::date IS NULL OR c.acquired_at::date >= $3::date) AND ($4::date IS NULL OR c.acquired_at::date <= $4::date)
      ORDER BY c.card_name''', user_id, batch['setCode'], start.isoformat() if start else None, end.isoformat() if end else None, batch_id)

@router.post('/acquisition-batches/{batch_id}/assign')
async def assign(batch_id: str, payload: AssignInput, user_id: str = Depends(get_current_user_id)):
    if len({i.collectionCardId for i in payload.items}) != len(payload.items):
        raise HTTPException(422, 'Hay cartas duplicadas en la selección')
    async with db.tx() as tx:
        from src.services.acquisition_service import user_lock
        await user_lock(tx,user_id)
        batch=await owned_batch(tx, user_id, batch_id, True)
        require_manual_batch(batch)
        if set(payload.remove) & {i.collectionCardId for i in payload.items}:
            raise HTTPException(422, 'Una carta no puede asignarse y desasignarse a la vez')
        for card_id in payload.remove:
            await tx.execute_raw('DELETE FROM acquisition_batch_items WHERE batch_id=$1 AND collection_card_id=$2', batch_id, card_id)
        for item in sorted(payload.items, key=lambda i: i.collectionCardId):
            cards = await tx.query_raw('SELECT * FROM user_collections WHERE id=$1 AND user_id=$2 FOR UPDATE', item.collectionCardId, user_id)
            if not cards:
                raise HTTPException(404, 'Carta no encontrada en tu colección')
            card = cards[0]
            used = await tx.query_raw('SELECT COALESCE(SUM(i.quantity),0)::int AS n FROM acquisition_batch_items i JOIN acquisition_batches b ON b.id=i.batch_id WHERE b.annulled_at IS NULL AND i.collection_card_id=$1 AND i.batch_id != $2', item.collectionCardId, batch_id)
            if used[0]['n'] + item.quantity > card['quantity']:
                raise HTTPException(409, 'La cantidad supera las copias disponibles sin asignar')
            # Resolve exact printing, never pass a catalog ID off as a printing.
            printings = await tx.query_raw('''SELECT p.id FROM card_printings p JOIN card_sets s ON s.id=p.set_id
             WHERE p.id=$1 OR (lower(s.code)=lower($2) AND lower(p.collector_number)=lower($3))
             ORDER BY (p.id=$1) DESC LIMIT 1''', card['card_scryfall_id'], card['set_code'], card['collector_number'])
            if not printings:
                raise HTTPException(422, 'Selecciona una impresión de catálogo antes de asignar')
            await tx.execute_raw('''INSERT INTO acquisition_batch_items(id,batch_id,collection_card_id,printing_id,quantity,is_foil)
             VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(batch_id,collection_card_id)
             DO UPDATE SET quantity=EXCLUDED.quantity,printing_id=EXCLUDED.printing_id,is_foil=EXCLUDED.is_foil''', str(uuid4()), batch_id, item.collectionCardId, printings[0]['id'], item.quantity, card['is_foil'])
        await snapshot_manual_batch(tx,batch_id,date.fromisoformat(str(batch['acquiredOn'])[:10]),batch['costTotal']+batch['shipping'])
    return {'assignedCount': len(payload.items)}

@router.delete('/acquisition-batches/{batch_id}/assign/{card_id}')
async def unassign(batch_id: str, card_id: str, user_id: str = Depends(get_current_user_id)):
    async with db.tx() as tx:
        from src.services.acquisition_service import user_lock
        await user_lock(tx,user_id)
        batch=await owned_batch(tx,user_id,batch_id,True)
        require_manual_batch(batch)
        await tx.execute_raw('DELETE FROM acquisition_batch_items WHERE batch_id=$1 AND collection_card_id=$2',batch_id,card_id)
        await snapshot_manual_batch(tx,batch_id,date.fromisoformat(str(batch['acquiredOn'])[:10]),batch['costTotal']+batch['shipping'])
    return {'deleted': True}

@router.post('/offers', status_code=201)
async def create_offer(payload: "SimulatedCollectionCreateRequest", provider: str = Query("cardmarket"), user_id: str = Depends(get_current_user_id)):
    return await SimulatedCollectionService.create_simulated_collection(user_id=user_id, name=payload.name.strip(), description=payload.description, raw_text=payload.rawText, provider=provider, printing_overrides=payload.printingOverrides, origin=payload.origin)

@router.get('/offers')
async def list_offers(provider: str = Query("cardmarket"), user_id: str = Depends(get_current_user_id)):
    return await SimulatedCollectionService.list_simulated_collections(user_id=user_id, provider=provider)

@router.patch('/offers/{offer_id}')
async def update_offer(offer_id: str, payload: OfferUpdate, user_id: str = Depends(get_current_user_id)):
    rows = await db.query_raw('''UPDATE simulated_collections SET status=$3,seller=$4,shipping=$5::numeric,source_url=CASE WHEN $6::text IS NULL AND source_url NOT LIKE 'https://%' THEN source_url ELSE $6::text END,paid_total=$7::numeric,origin=COALESCE($8,origin),updated_at=NOW()
     WHERE id=$1 AND user_id=$2 AND status != 'purchased' RETURNING id''', offer_id, user_id, payload.status, payload.seller, str(payload.shipping), str(payload.sourceUrl) if payload.sourceUrl else None, str(payload.paidTotal) if payload.paidTotal is not None else None, payload.origin)
    if not rows:
        raise HTTPException(409, 'Oferta no encontrada o ya comprada')
    return {'id': offer_id, **payload.model_dump(mode='json')}

@router.post('/offers/{offer_id}/confirm')
async def confirm_offer(offer_id: str, payload: ConfirmOffer, user_id: str = Depends(get_current_user_id)):
    from src.services.acquisition_service import create_offer_quote, commit_quote
    from src.schemas.acquisitions import OfferQuoteInput, CommitAcquisitionInput
    offers = await db.query_raw('SELECT status,confirmed_batch_id FROM simulated_collections WHERE id=$1 AND user_id=$2',offer_id,user_id)
    if not offers:
        raise HTTPException(404,'Oferta no encontrada')
    if offers[0]['status']=='purchased':
        return dict(batchId=offers[0]['confirmed_batch_id'],alreadyConfirmed=True)
    quote_id = payload.quoteId
    if quote_id:
        quotes = await db.query_raw("SELECT payload FROM acquisition_quotes WHERE id=$1 AND user_id=$2 AND mode='offer'",quote_id,user_id)
        from src.services.acquisition_service import object_data
        if not quotes or object_data(quotes[0]['payload']).get('offerId')!=offer_id:
            raise HTTPException(404,'Revisión de esta oferta no encontrada')
    if not quote_id:
        quote = await create_offer_quote(user_id, offer_id, OfferQuoteInput(acquiredOn=payload.acquiredOn, cardNames=payload.cardNames))
        quote_id = quote['quoteId']
    return await commit_quote(user_id, CommitAcquisitionInput(quoteId=quote_id, acknowledgeBudget=payload.acknowledgeBudget))

@router.get('/search')
async def search(q: str = Query('', max_length=200), user_id: str = Depends(get_current_user_id)):
    sections = [('decks','Mazos','/decks'),('inventory','Inventario','/collection/inventory'),('expansions','Expansiones','/collection/expansions'),('needs','Necesito','/buy/needs'),('list','Lista de compra','/buy/list'),('offers','Evaluar oferta','/buy/offers'),('budget','Golden Wants','/buy/budget'),('expenses','Gastos e inversiones','/buy/expenses')]
    results = [{'id': key, 'name': name, 'kind': 'Sección', 'href': href} for key,name,href in sections if q.casefold() in name.casefold()]
    if len(q.strip()) < 2:
        return results
    # Escape LIKE wildcards so pasted names remain literal.
    pattern = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    decks = await db.query_raw('SELECT id,name FROM decks WHERE user_id=$1 AND name ILIKE $2 ORDER BY name LIMIT 10', user_id, pattern)
    cards = await db.query_raw('SELECT DISTINCT ON (c.name) p.id,c.name FROM card_catalog c JOIN card_printings p ON p.catalog_id=c.id WHERE c.name ILIKE $1 ORDER BY c.name,p.id LIMIT 15', pattern)
    return results + [{'id': d['id'], 'name': d['name'], 'kind': 'Mazo', 'href': '/decks/'+d['id']} for d in decks] + [{'id': c['id'], 'name': c['name'], 'kind': 'Carta'} for c in cards]


async def owned_deck(user_id, deck_id):
    deck = await db.deck.find_unique(where={'id':deck_id})
    if not deck or deck.userId != user_id:
        raise HTTPException(404, 'Mazo no encontrado')

@router.get('/decks/{deck_id}/function-targets', response_model=FunctionTargets)
async def get_function_targets(deck_id: str, user_id: str = Depends(get_current_user_id)):
    await owned_deck(user_id, deck_id)
    rows = await db.query_raw('SELECT targets FROM deck_function_targets WHERE deck_id=$1', deck_id)
    return rows[0] if rows else {'targets':{}}

@router.put('/decks/{deck_id}/function-targets', response_model=FunctionTargets)
async def set_function_targets(deck_id: str, payload: FunctionTargets, user_id: str = Depends(get_current_user_id)):
    import json
    await owned_deck(user_id, deck_id)
    await db.execute_raw('INSERT INTO deck_function_targets(deck_id,targets) VALUES($1,$2::jsonb) ON CONFLICT(deck_id) DO UPDATE SET targets=EXCLUDED.targets', deck_id, json.dumps(payload.targets))
    return payload


@router.post('/offers/import-link', response_model=SimulatedCollectionAnalysisResponse, status_code=201)
async def import_offer_link(payload: OfferLinkImportRequest, user_id: str = Depends(get_current_user_id)):
    from src.services.offer_import_service import import_link_offer
    try:
        return await import_link_offer(user_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/offers/discovered')
async def discovered_offers(user_id: str = Depends(get_current_user_id)):
    from src.services.offer_import_service import discover_offers
    return await discover_offers(user_id)
