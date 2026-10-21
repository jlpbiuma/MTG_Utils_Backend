"""Acquisition reviews, immutable purchase snapshots and user-scoped expense ledger."""
import difflib
import hashlib
import json
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from src.core.db import db
from src.schemas.acquisitions import local_today
from src.services.acquisition_finance import cents, distribute_cost, purchase_totals, budget_summary, valuation_revision
from src.services.card_utils import normalize_card_name
from src.services.scryfall_service import fold_search_text
from src.services.price_statistics import DAILY_HISTORY


def json_data(value):
    return json.dumps(value, default=str, sort_keys=True)


def object_data(value):
    return json.loads(value) if isinstance(value, str) else value


def fingerprint(value):
    return hashlib.sha256(json_data(value).encode()).hexdigest()


def parse_purchase_list(raw_text):
    lines = []
    for number, raw in enumerate(raw_text.splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith('//') or text.startswith('#'):
            continue
        match = re.match(r'^(\d+)\s*x?\s+(.+)$', text, re.I)
        quantity, name = (int(match[1]), match[2].strip()) if match else (1, text)
        error = None
        if quantity < 1 or quantity > 99999 or re.match(r'^[-+\d][\d.,]*\s', name):
            error = 'Cantidad inválida'
        if len(name) > 300:
            error = 'Nombre demasiado largo'
        from src.services.import_service import parse_decklist_text
        entries = parse_decklist_text(text)
        if not entries and not error:
            continue
        entry = entries[0] if entries else None
        if entry and not error:
            name,quantity=entry.name,entry.quantity
        lines.append(dict(line=number, raw=raw, name=name, quantity=quantity, error=error,
                          setCode=entry.setCode if entry else None,collectorNumber=entry.collectorNumber if entry else None,
                          requestedFoil=True if entry and entry.isFoil else None))
    if len(lines) > 500:
        raise HTTPException(422, 'El listado admite como máximo 500 líneas')
    return lines


# Resolve catalog IDs to prints without confusing the two identities.
INVENTORY = '''SELECT c.id,c.card_name AS "cardName",c.quantity,c.is_foil AS "isFoil",
 COALESCE(c.set_code,s.code) AS "setCode",COALESCE(c.collector_number,p.collector_number) AS "collectorNumber",p.id AS "printingId",
 s.name AS "setName",cat.name AS "canonicalName",cat.name_es AS "nameEs",cat.details_es AS "detailsEs",
 CASE WHEN c.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END AS "currentPrice",
 p.prices_updated_at::text AS "priceObservedAt",
 COALESCE((SELECT SUM(i.quantity) FROM acquisition_batch_items i JOIN acquisition_batches b ON b.id=i.batch_id
 WHERE i.collection_card_id=c.id AND b.annulled_at IS NULL),0)::int AS "assignedQuantity"
 FROM user_collections c
 LEFT JOIN LATERAL(
   SELECT matches.* FROM (
     SELECT cp.*,true AS exact_id FROM card_printings cp WHERE cp.id=c.card_scryfall_id
     UNION ALL
     SELECT cp.*,false AS exact_id FROM card_sets cs JOIN card_printings cp ON cp.set_id=cs.id
       WHERE c.set_code IS NOT NULL AND c.collector_number IS NOT NULL
         AND lower(cs.code)=lower(c.set_code) AND lower(cp.collector_number)=lower(c.collector_number)
   ) matches ORDER BY exact_id DESC,id LIMIT 1
 ) p ON true
 LEFT JOIN card_sets s ON s.id=p.set_id LEFT JOIN card_catalog cat ON cat.id=COALESCE(p.catalog_id,c.card_scryfall_id)
 WHERE c.user_id=$1 ORDER BY c.card_name,c.is_foil,c.id'''


async def validate_list(user_id, raw_text, client=None):
    client = client or db
    rows = await client.query_raw(INVENTORY, user_id)
    aliases = defaultdict(list)
    for row in rows:
        details = object_data(row.get('detailsEs') or {})
        names = [row['cardName'], row.get('canonicalName'), row.get('nameEs'), details.get('printed_name')]
        names.extend(face.get('printed_name') for face in details.get('card_faces', []))
        for key in {fold_search_text(normalize_card_name(name)) for name in names if name}:
            aliases[key].append(row)
    lines = parse_purchase_list(raw_text)
    seen = defaultdict(int)
    for line in lines:
        key = fold_search_text(normalize_card_name(line['name']))
        matches = aliases.get(key, [])
        if line.get('setCode'):
            matches=[r for r in matches if (r['setCode'] or '').lower()==line['setCode'].lower()]
        if line.get('collectorNumber'):
            matches=[r for r in matches if (r['collectorNumber'] or '').lower()==line['collectorNumber'].lower()]
        if line.get('requestedFoil'):
            matches=[r for r in matches if r['isFoil']]
        canonical_names = {normalize_card_name(r.get('canonicalName') or r['cardName']) for r in matches}
        identity = next(iter(canonical_names)) if len(canonical_names)==1 else key
        seen[identity] += line['quantity']
        available = sum(max(0, r['quantity']-r['assignedQuantity']) for r in matches)
        usable = sum(max(0, r['quantity']-r['assignedQuantity']) for r in matches if r['printingId'])
        line['errorCode'] = 'invalid_input' if line['error'] else None
        line['ownedQuantity'] = sum(r['quantity'] for r in matches)
        line['availableQuantity'] = usable
        if not line['error']:
            if not matches:
                line['error'] = 'Nombre no encontrado en tu colección'
                line['errorCode'] = 'name_not_owned'
            elif len(canonical_names) > 1:
                line['error'] = 'Nombre ambiguo: selecciona la carta correcta'
                line['errorCode'] = 'ambiguous_name'
            elif available < seen[identity]:
                line['errorCode'] = 'copies_already_linked' if line['ownedQuantity'] >= seen[identity] else 'insufficient_quantity'
                line['error'] = f"Se necesitan {seen[identity]} copias; hay {usable} disponibles sin asociar a otra compra ({line['ownedQuantity']} en la colección)."
            elif usable < seen[identity]:
                line['errorCode'] = 'printing_unresolved'
                line['error'] = 'El nombre está reconocido, pero falta resolver la edición y el número de coleccionista. Selecciona la impresión en tu colección antes de registrar la compra.'
        line['candidates'] = [dict(collectionCardId=r['id'], cardName=r['cardName'], printingId=r['printingId'],
                                  isFoil=r['isFoil'], setCode=r['setCode'], collectorNumber=r['collectorNumber'],
                                  setName=r.get('setName'), availableQuantity=max(0,r['quantity']-r['assignedQuantity']),
                                  currentPrice=float(r['currentPrice']) if r['currentPrice'] is not None else None)
                              for r in matches]
        line['suggestions'] = sorted({r.get('canonicalName') or r['cardName'] for r in matches}) if len(canonical_names)>1 else []
        if not matches:
            for candidate in difflib.get_close_matches(key, aliases, n=3, cutoff=0.55):
                suggested = aliases[candidate][0]['cardName']
                if suggested not in line['suggestions']:
                    line['suggestions'].append(suggested)
    return dict(lines=lines, valid=bool(lines) and all(not line['error'] for line in lines))


async def historical_prices(client, items, on_date):
    if not items:
        return {}
    items = list({(i['printingId'],i['isFoil']):i for i in items}.values())
    ids = [i['printingId'] for i in items]
    finishes = [int(i['isFoil']) for i in items]
    quantities = [i['quantity'] for i in items]
    rows = await client.query_raw(DAILY_HISTORY + '''SELECT DISTINCT ON(printing_id,finish)
        printing_id,finish,price,day::text AS day FROM daily_prices WHERE day <= $4::date
        ORDER BY printing_id,finish,day DESC''', ids, finishes, quantities, on_date.isoformat())
    return {(r['printing_id'],bool(r['finish'])): r for r in rows}


async def save_quote(user_id, mode, data, client=None):
    client = client or db
    quote_id = str(uuid4())
    await client.execute_raw('INSERT INTO acquisition_quotes(id,user_id,mode,payload,fingerprint) VALUES($1,$2,$3,$4::jsonb,$5)',
                             quote_id, user_id, mode, json_data(data), fingerprint(data))
    return {'quoteId': quote_id, **data, 'budget': await budget_preview(user_id, data, client)}


async def create_historical_quote(user_id, payload):
    validation = await validate_list(user_id, payload.rawText)
    if not validation['valid']:
        raise HTTPException(422, detail={'message': 'Corrige los errores del listado', **validation})
    lines = {line['line']:line for line in validation['lines']}
    chosen = defaultdict(int)
    selected_per_line = defaultdict(int)
    selections = {}
    for selection in payload.selections:
        line = lines.get(selection.line)
        candidate = next((c for c in line['candidates'] if c['collectionCardId']==selection.collectionCardId), None) if line else None
        if not candidate or not candidate['printingId']:
            raise HTTPException(422, 'Selecciona una impresión válida de tu colección')
        chosen[selection.collectionCardId] += selection.quantity
        selected_per_line[selection.line] += selection.quantity
        if chosen[selection.collectionCardId] > candidate['availableQuantity']:
            raise HTTPException(409, 'La selección supera las copias disponibles')
        selections[selection.collectionCardId] = candidate
    if any(selected_per_line[n] != line['quantity'] for n,line in lines.items()):
        raise HTTPException(422, 'Selecciona exactamente las copias indicadas en cada línea')
    items = [dict(**selections[cid], quantity=quantity) for cid,quantity in sorted(chosen.items())]
    history = await historical_prices(db, items, payload.acquiredOn)
    for item in items:
        reference = history.get((item['printingId'],item['isFoil']))
        item['referenceUnitPrice'] = str(reference['price']) if reference else None
        item['referenceObservedOn'] = reference['day'] if reference else None
    allocations = distribute_cost(payload.total, [Decimal(i['referenceUnitPrice'])*i['quantity'] if i['referenceUnitPrice'] is not None else None for i in items])
    for item, amount in zip(items, allocations):
        item['costAmount'] = str(amount) if amount is not None else None
    data = dict(name=payload.name, kind=payload.kind, origin=payload.origin, seller=payload.seller, sourceUrl=payload.sourceUrl,
                acquiredOn=payload.acquiredOn.isoformat(), subtotal=str(cents(payload.total)), shipping='0.00', fee='0.00',
                total=str(cents(payload.total)), costBasis='allocated', items=items, rawText=payload.rawText)
    return await save_quote(user_id, 'history', data)


async def create_inventory_quote(user_id, payload):
    rows = await db.query_raw('''SELECT p.id,s.code,p.collector_number,cat.name,p.image_uri,cat.mana_cost,cat.type_line,
      CASE WHEN $2 THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END AS price,
      p.prices_updated_at::text AS observed_at FROM card_printings p JOIN card_sets s ON s.id=p.set_id
      JOIN card_catalog cat ON cat.id=p.catalog_id WHERE p.id=$1''', payload.printingId, payload.isFoil)
    if not rows or rows[0]['price'] is None:
        raise HTTPException(422, 'Selecciona una impresión con precio de tendencia disponible')
    p = rows[0]
    total = cents(Decimal(str(p['price'])) * payload.quantity)
    item = dict(printingId=p['id'], cardName=p['name'], quantity=payload.quantity, isFoil=payload.isFoil,
                setCode=p['code'], collectorNumber=p['collector_number'], imageUri=p['image_uri'], manaCost=p['mana_cost'],typeLine=p['type_line'],
                referenceUnitPrice=str(p['price']), referenceObservedOn=str(p['observed_at'])[:10] if p['observed_at'] else None,
                costAmount=str(total))
    return await save_quote(user_id, 'inventory', dict(name=payload.name,kind='singles',origin='other',seller=None,sourceUrl=None,
                           acquiredOn=payload.acquiredOn.isoformat(),subtotal=str(total),shipping='0.00',fee='0.00',total=str(total),costBasis='trend',items=[item]))


async def create_offer_quote(user_id, offer_id, payload):
    from src.services.simulated_collection_service import SimulatedCollectionService
    from src.services.offer_card_selection import remaining_offer_text
    analysis = await SimulatedCollectionService.get_simulated_collection(user_id, offer_id, 'cardmarket')
    if not analysis:
        raise HTTPException(404, 'Oferta no encontrada')
    if analysis.status in ('discarded','purchased'):
        raise HTTPException(409, 'La oferta ya no está disponible para comprar')
    if analysis.opportunitySubject == 'compra_busca' or not analysis.cards:
        raise HTTPException(422, 'La oferta no contiene una compra de cartas')
    if payload.valuationRevision and payload.valuationRevision != valuation_revision(analysis):
        raise HTTPException(409, 'La valoración ha cambiado. Actualiza la oferta y revisa los precios antes de confirmar.')
    names = {normalize_card_name(n) for n in payload.cardNames} if payload.cardNames is not None else {normalize_card_name(c.cardName) for c in analysis.cards}
    all_names = {normalize_card_name(c.cardName) for c in analysis.cards}
    if not names or not names.issubset(all_names):
        raise HTTPException(422, 'La selección contiene cartas que ya no están en la oferta')
    chosen = [c for c in analysis.cards if normalize_card_name(c.cardName) in names]
    selected_printings = {
        normalize_card_name(c.cardName): c.selectedPrintingId
        for c in chosen
        if c.selectedPrintingId
    }
    items = []
    if analysis.origin == 'cardmarket':
        if not analysis.purchaseAnalysis:
            raise HTTPException(422, 'Importa los precios por carta de Cardmarket')
        for n,c in enumerate(analysis.purchaseAnalysis.cards):
            if normalize_card_name(c.cardName) not in names:
                continue
            price = payload.purchasePrices.get(str(n), c.purchaseUnitPrice)
            if price is None:
                raise HTTPException(422, f'Introduce el precio pagado por {c.cardName}')
            # Cardmarket's set slug/collector number may not be enough to
            # reconstruct an exact printing (for example special products such
            # as Star Trek). Preserve the exact printing already selected for
            # the imported offer when the cart parser could not infer it.
            printing_id = c.referencePrintingId or selected_printings.get(normalize_card_name(c.cardName))
            if not printing_id:
                raise HTTPException(422, f'Selecciona la impresión exacta de {c.cardName}')
            items.append(dict(cardName=c.cardName,printingId=printing_id,quantity=c.quantity,isFoil=c.isFoil,
                              referenceUnitPrice=str(c.marketUnitPrice) if c.referenceKind=='exact' and c.marketUnitPrice is not None else None,
                              purchaseUnitPrice=str(price),purchaseAmount=str(cents(Decimal(str(price))*c.quantity)),costAmount=str(cents(Decimal(str(price))*c.quantity))))
    else:
        for c in chosen:
            if not c.selectedPrintingId:
                raise HTTPException(422, f'Selecciona la impresión exacta de {c.cardName}')
            items.append(dict(cardName=c.cardName,printingId=c.selectedPrintingId,quantity=c.quantity,isFoil=False,
                              referenceUnitPrice=str(c.unitPrice),costAmount=str(cents(Decimal(str(c.unitPrice))*c.quantity))))
    # A shared purchase key accounts for overhead only once across partial dumps.
    previous = await db.query_raw('SELECT COUNT(*)::int AS n,COALESCE(SUM(cost_total+shipping),0)::text AS base,COALESCE(SUM(fee),0)::text AS fee FROM acquisition_batches WHERE user_id=$1 AND purchase_key=$2',user_id,offer_id)
    has_previous = bool(previous[0]['n'])
    shipping = analysis.shipping if analysis.origin=='cardmarket' and not has_previous else 0
    totals = purchase_totals(analysis.origin, sum(Decimal(i['costAmount']) for i in items),shipping,charge_fixed_fee=not has_previous)
    if analysis.origin=='cardmarket':
        cumulative_fee = cents((Decimal(previous[0]['base'])+totals['subtotal']+totals['shipping'])*Decimal('0.05'))+Decimal('0.30')
        totals['fee'] = cumulative_fee-Decimal(previous[0]['fee'])
        totals['total'] = totals['subtotal']+totals['shipping']+totals['fee']
        overhead = distribute_cost(totals['shipping']+totals['fee'], [Decimal(i['costAmount']) for i in items] if totals['subtotal'] else [i['quantity'] for i in items])
        for item,extra in zip(items, overhead):
            item['costAmount'] = str(cents(Decimal(item['costAmount'])+(extra or 0)))
    printing_ids = sorted({i['printingId'] for i in items})
    printings = await db.query_raw('''SELECT p.id,s.code,p.collector_number,p.image_uri,cat.mana_cost,cat.type_line,
      CASE WHEN p.price_cardmarket_trend IS NOT NULL THEN p.price_cardmarket_trend ELSE p.price_eur END AS price,
      p.price_eur_foil AS foil_price,p.prices_updated_at::text AS observed_at
      FROM card_printings p JOIN card_sets s ON s.id=p.set_id LEFT JOIN card_catalog cat ON cat.id=p.catalog_id WHERE p.id=ANY($1::text[])''', printing_ids)
    by_id = {p['id']:p for p in printings}
    for item in items:
        p = by_id.get(item['printingId'])
        if not p:
            raise HTTPException(422, f"Impresión no encontrada: {item['cardName']}")
        price = p['foil_price'] if item['isFoil'] else p['price']
        if analysis.origin!='cardmarket' and price is None:
            raise HTTPException(422, f"No hay tendencia disponible para {item['cardName']}")
        item.update(setCode=p['code'],collectorNumber=p['collector_number'],imageUri=p['image_uri'],manaCost=p['mana_cost'],typeLine=p['type_line'],
                    referenceObservedOn=str(p['observed_at'])[:10] if p['observed_at'] else None)
    partial = names != all_names
    remaining_text = None
    if partial:
        source_names = names | {normalize_card_name(n) for c in chosen for n in c.sourceNames}
        try:
            remaining_text = remaining_offer_text(analysis.rawText or '\n'.join(f'{c.quantity} {c.cardName}' for c in analysis.cards), source_names)
        except ValueError as e:
            raise HTTPException(422,str(e))
    data = dict(name=analysis.name,kind='singles',origin=analysis.origin,seller=analysis.seller,sourceUrl=analysis.sourceUrl,
                acquiredOn=payload.acquiredOn.isoformat(),subtotal=str(totals['subtotal']),shipping=str(totals['shipping']),fee=str(totals['fee']),total=str(totals['total']),
                costBasis='paid' if analysis.origin=='cardmarket' else 'trend',items=items,offerId=offer_id,purchaseKey=offer_id,
                partial=partial,remainingText=remaining_text,selectedNames=[c.cardName for c in chosen],hasPrevious=has_previous,previousState=previous[0],
                sourceState=dict(rawText=analysis.rawText,origin=analysis.origin,printingOverrides=analysis.printingOverrides,shipping=analysis.shipping,seller=analysis.seller,sourceUrl=analysis.sourceUrl))
    return await save_quote(user_id, 'offer', data)


async def expense_rows(user_id, client=None):
    client = client or db
    return await client.query_raw('''SELECT b.id,COALESCE(b.name,b.notes,'Compra') AS name,b.kind,
      b.purchase_origin AS origin,b.seller,b.source_url AS "sourceUrl",b.acquired_on::text AS "acquiredOn",
      b.currency,b.cost_basis AS "costBasis",b.cost_total::float AS subtotal,b.shipping::float,b.fee::float,
      (b.cost_total+b.shipping+b.fee)::float AS investment,b.annulled_at::text AS "annulledAt",
      COUNT(i.id)::int AS "itemCount",COALESCE(SUM(i.quantity),0)::int AS copies,
      COUNT(i.id) FILTER(WHERE (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END) IS NOT NULL)::int AS "pricedItems",
      SUM(i.quantity*(CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END))::float AS "marketValue",
      COUNT(i.id) FILTER(WHERE i.reference_unit_price IS NOT NULL)::int AS "referenceItems",
      SUM(i.quantity*i.reference_unit_price)::float AS "initialValue"
      FROM acquisition_batches b LEFT JOIN acquisition_batch_items i ON i.batch_id=b.id
      LEFT JOIN card_printings p ON p.id=i.printing_id WHERE b.user_id=$1
      GROUP BY b.id ORDER BY b.acquired_on DESC,b.created_at DESC''',user_id)


def enrich_result(row):
    complete = row['itemCount'] > 0 and row['pricedItems']==row['itemCount']
    row['complete'] = complete
    row['profit'] = float(cents(Decimal(str(row['marketValue']))-Decimal(str(row['investment'])))) if complete and row['currency']=='EUR' else None
    row['roi'] = round(row['profit']/row['investment']*100,2) if row['profit'] is not None and row['investment']>0 else None
    row['marketChange'] = float(cents(Decimal(str(row['marketValue']))-Decimal(str(row['initialValue'])))) if complete and row['referenceItems']==row['itemCount'] else None
    return row


async def ledger(user_id, year=None):
    rows = [enrich_result(row) for row in await expense_rows(user_id)]
    year = year or local_today().year
    years = sorted({int(r['acquiredOn'][:4]) for r in rows} | {local_today().year}, reverse=True)
    return dict(year=year,years=years,budget=budget_summary(rows,year,local_today()),
                purchases=[r for r in rows if int(r['acquiredOn'][:4])==year])


async def budget_preview(user_id, data, client, excluding=None):
    rows = await expense_rows(user_id,client)
    if excluding:
        rows = [r for r in rows if r['id']!=excluding]
    rows.append(dict(acquiredOn=data['acquiredOn'],investment=float(data['total']),costBasis=data['costBasis'],currency='EUR'))
    summary = budget_summary(rows,int(data['acquiredOn'][:4]),local_today())
    month = summary['months'][int(data['acquiredOn'][5:7])-1]
    return {**summary, 'requiresAcknowledgement': summary['excess']>0 or month['spent']>50,
            'purchaseMonth':month['month'],'monthSpent':month['spent']}


async def user_lock(client,user_id):
    # Match inventory's acquisition lock to avoid lock-order inversions.
    await client.query_raw("SELECT pg_advisory_xact_lock(hashtextextended('mtg-acquisition:' || $1, 0))::text",user_id)


async def enforce_budget(client,user_id,data,acknowledge,excluding=None):
    preview = await budget_preview(user_id,data,client,excluding)
    if preview['requiresAcknowledgement'] and not acknowledge:
        raise HTTPException(409,detail={'code':'budget_acknowledgement_required','message':'La compra supera la referencia mensual o el presupuesto anual. Confirma el aviso para registrarla.','budget':preview})


async def commit_quote(user_id,payload):
    async with db.tx(timeout=timedelta(seconds=30)) as tx:
        await user_lock(tx,user_id)
        quotes = await tx.query_raw('SELECT * FROM acquisition_quotes WHERE id=$1 AND user_id=$2 FOR UPDATE',payload.quoteId,user_id)
        if not quotes:
            raise HTTPException(404,'Revisión de compra no encontrada')
        quote = quotes[0]
        if quote['consumed_batch_id']:
            return dict(batchId=quote['consumed_batch_id'],alreadyConfirmed=True)
        data = object_data(quote['payload'])
        from datetime import date
        if date.fromisoformat(data['acquiredOn']) > local_today():
            raise HTTPException(422,'La fecha de adquisición no puede ser posterior a hoy')
        offer = None
        if quote['mode']=='offer':
            offers = await tx.query_raw('SELECT * FROM simulated_collections WHERE id=$1 AND user_id=$2 FOR UPDATE',data['offerId'],user_id)
            if not offers:
                raise HTTPException(404,'Oferta no encontrada')
            offer = offers[0]
            if offer['status']=='purchased':
                return dict(batchId=offer['confirmed_batch_id'],alreadyConfirmed=True)
            overrides = await tx.simulatedcard.find_many(where={'simulatedCollectionId':data['offerId']})
            state = dict(rawText=offer['raw_text'],origin=offer['origin'],shipping=float(offer['shipping']),seller=offer['seller'],sourceUrl=offer['source_url'],
                         printingOverrides={normalize_card_name(c.cardName):c.selectedPrintingId for c in overrides if c.selectedPrintingId})
            previous = await tx.query_raw('SELECT COUNT(*)::int AS n,COALESCE(SUM(cost_total+shipping),0)::text AS base,COALESCE(SUM(fee),0)::text AS fee FROM acquisition_batches WHERE user_id=$1 AND purchase_key=$2',user_id,data['offerId'])
            if offer['status']=='discarded' or state != data['sourceState'] or previous[0] != data['previousState']:
                raise HTTPException(409,'La oferta ha cambiado. Revisa la compra antes de confirmar.')
        await enforce_budget(tx,user_id,data,payload.acknowledgeBudget)
        if quote['mode']=='history':
            for item in data['items']:
                rows = await tx.query_raw('SELECT * FROM user_collections WHERE id=$1 AND user_id=$2 FOR UPDATE',item['collectionCardId'],user_id)
                if not rows:
                    raise HTTPException(409,'Una carta ya no está en tu colección')
                card = rows[0]
                resolved = await tx.query_raw('''SELECT p.id FROM card_printings p JOIN card_sets s ON s.id=p.set_id
                  WHERE p.id=$1 OR (lower(s.code)=lower($2) AND lower(p.collector_number)=lower($3))
                  ORDER BY (p.id=$1) DESC,p.id LIMIT 1''',card['card_scryfall_id'],card['set_code'],card['collector_number'])
                if not resolved or resolved[0]['id'] != item['printingId'] or card['is_foil']!=item['isFoil']:
                    raise HTTPException(409,'La impresión o el acabado han cambiado. Revisa la compra.')
                used = await tx.query_raw('''SELECT COALESCE(SUM(i.quantity),0)::int AS n FROM acquisition_batch_items i
                    JOIN acquisition_batches b ON b.id=i.batch_id WHERE i.collection_card_id=$1 AND b.annulled_at IS NULL''',item['collectionCardId'])
                if used[0]['n']+item['quantity']>card['quantity']:
                    raise HTTPException(409,'Las copias ya están asociadas a otra compra o no están disponibles')
        batch_id = str(uuid4())
        codes = {i.get('setCode') for i in data['items'] if i.get('setCode')}
        await tx.execute_raw('''INSERT INTO acquisition_batches(id,user_id,name,kind,purchase_origin,seller,source_url,cost_basis,
            cost_total,shipping,fee,acquired_on,origin,offer_id,purchase_key,set_code)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::numeric,$10::numeric,$11::numeric,$12::date,$13,$14,$15,$16)''',
            batch_id,user_id,data['name'],data['kind'],data['origin'],data.get('seller'),data.get('sourceUrl'),data['costBasis'],
            data['subtotal'],data['shipping'],data['fee'],data['acquiredOn'],
            'historical' if quote['mode']=='history' else 'offer_selection' if data.get('partial') else 'offer_confirmed' if quote['mode']=='offer' else 'inventory_purchase',
            data.get('offerId') if not data.get('partial') else None,data.get('purchaseKey'),next(iter(codes)) if len(codes)==1 else None)
        for item in data['items']:
            cid = item.get('collectionCardId')
            if quote['mode']!='history':
                cards = await tx.query_raw('''INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil,
                    set_code,collector_number,mana_cost,type_line,image_uri,acquired_at,updated_at)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::date,NOW())
                    ON CONFLICT(user_id,card_scryfall_id,is_foil) DO UPDATE SET quantity=user_collections.quantity+EXCLUDED.quantity,
                    acquired_at=COALESCE(user_collections.acquired_at,EXCLUDED.acquired_at),updated_at=NOW() RETURNING id''',
                    str(uuid4()),user_id,item['printingId'],item['cardName'],item['quantity'],item['isFoil'],item.get('setCode'),item.get('collectorNumber'),
                    item.get('manaCost'),item.get('typeLine'),item.get('imageUri'),data['acquiredOn'])
                cid = cards[0]['id']
            await tx.execute_raw('''INSERT INTO acquisition_batch_items(id,batch_id,collection_card_id,printing_id,card_name,quantity,is_foil,
                reference_unit_price,reference_observed_on,reference_captured_at,cost_amount,cost_basis,acquired_on,metadata,purchase_amount)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8::numeric,$9::date,NOW(),$10::numeric,$11,$12::date,$13::jsonb,$14::numeric)
                ON CONFLICT(batch_id,collection_card_id) DO UPDATE SET purchase_amount=acquisition_batch_items.purchase_amount+EXCLUDED.purchase_amount,quantity=acquisition_batch_items.quantity+EXCLUDED.quantity,
                cost_amount=CASE WHEN acquisition_batch_items.cost_amount IS NULL OR EXCLUDED.cost_amount IS NULL THEN NULL
                ELSE acquisition_batch_items.cost_amount+EXCLUDED.cost_amount END''',
                str(uuid4()),batch_id,cid,item['printingId'],item['cardName'],item['quantity'],item['isFoil'],item.get('referenceUnitPrice'),
                item.get('referenceObservedOn'),item.get('costAmount'),data['costBasis'],data['acquiredOn'],json_data(item),item.get('purchaseAmount'))
            if quote['mode']=='offer':
                remaining = item['quantity']
                wants = await tx.wantcard.find_many(where={'userId':user_id})
                for want in wants:
                    if remaining<=0 or normalize_card_name(want.cardName)!=normalize_card_name(item['cardName']):
                        continue
                    used = min(remaining,want.quantity)
                    if used==want.quantity:
                        await tx.wantcard.delete(where={'id':want.id})
                    else:
                        await tx.wantcard.update(where={'id':want.id},data={'quantity':want.quantity-used})
                    remaining -= used
        if offer:
            if data['partial']:
                await tx.simulatedcard.delete_many(where={'simulatedCollectionId':data['offerId'],'cardName':{'in':data['selectedNames']}})
                await tx.execute_raw('UPDATE simulated_collections SET raw_text=$3,updated_at=NOW() WHERE id=$1 AND user_id=$2',data['offerId'],user_id,data['remainingText'])
            else:
                await tx.execute_raw("UPDATE simulated_collections SET status='purchased',paid_total=$3::numeric,confirmed_batch_id=$4,updated_at=NOW() WHERE id=$1 AND user_id=$2",data['offerId'],user_id,str(Decimal(data['total'])+Decimal(data['previousState']['base'])+Decimal(data['previousState']['fee'])),batch_id)
        await tx.execute_raw('UPDATE acquisition_quotes SET consumed_batch_id=$2 WHERE id=$1',payload.quoteId,batch_id)
        return dict(batchId=batch_id,alreadyConfirmed=False)


async def purchase_detail(user_id,batch_id):
    rows = [enrich_result(r) for r in await expense_rows(user_id) if r['id']==batch_id]
    if not rows:
        raise HTTPException(404,'Compra no encontrada')
    items = await db.query_raw('''SELECT i.id,i.card_name AS "cardName",i.printing_id AS "printingId",i.quantity,i.is_foil AS "isFoil",
      s.code AS "setCode",p.collector_number AS "collectorNumber",i.cost_amount::float AS "costAmount",i.purchase_amount::float AS "purchaseAmount",i.cost_basis AS "costBasis",
      i.reference_unit_price::float AS "referenceUnitPrice",i.reference_observed_on::text AS "referenceObservedOn",
      i.reference_captured_at::text AS "referenceCapturedAt",i.acquired_on::text AS "acquiredOn",
      CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END AS "currentPrice"
      FROM acquisition_batch_items i LEFT JOIN card_printings p ON p.id=i.printing_id LEFT JOIN card_sets s ON s.id=p.set_id
      WHERE i.batch_id=$1 ORDER BY i.card_name,i.id''',batch_id)
    for item in items:
        current = item['currentPrice']
        value = Decimal(str(current))*item['quantity'] if current is not None else None
        item['currentValue'] = float(cents(value)) if value is not None else None
        item['profit'] = float(cents(value-Decimal(str(item['costAmount'])))) if value is not None and item['costAmount'] is not None else None
        item['roi'] = round(item['profit']/item['costAmount']*100,2) if item['profit'] is not None and item['costAmount'] else None
    identities = defaultdict(int)
    for i in items:
        identities[(i['printingId'],i['isFoil'])] += i['quantity']
    points = await db.query_raw(DAILY_HISTORY + '''SELECT day::text AS date,SUM(h.price*r.quantity)::float AS value
       FROM daily_prices h JOIN requested r USING(printing_id,finish) WHERE day >= $4::date
       GROUP BY day HAVING COUNT(*)=(SELECT COUNT(*) FROM requested) ORDER BY day''',
       [key[0] for key in identities],[int(key[1]) for key in identities],list(identities.values()),rows[0]['acquiredOn']) if items else []
    return {**rows[0], 'items':items,'points':points}


async def edit_purchase(user_id,batch_id,payload):
    async with db.tx(timeout=timedelta(seconds=30)) as tx:
        await user_lock(tx,user_id)
        rows = await tx.query_raw('SELECT * FROM acquisition_batches WHERE id=$1 AND user_id=$2 FOR UPDATE',batch_id,user_id)
        if not rows or rows[0]['annulled_at']:
            raise HTTPException(404,'Compra no encontrada o anulada')
        batch = rows[0]
        for field in ('cost_total','shipping','fee'):
            batch[field]=Decimal(str(batch[field]))
        if batch['currency']!='EUR':
            raise HTTPException(422,'Este registro está en otra moneda y no se puede convertir automáticamente')
        items = await tx.query_raw('SELECT id,quantity,reference_unit_price,cost_amount,purchase_amount FROM acquisition_batch_items WHERE batch_id=$1 ORDER BY id',batch_id)
        real_cardmarket = batch['purchase_origin']=='cardmarket' and batch['origin']!='historical' and batch['cost_basis']=='paid'
        shipping = payload.shipping if payload.shipping is not None else batch['shipping']
        weights = [r['reference_unit_price']*r['quantity'] if r['reference_unit_price'] is not None else None for r in items]
        purchase_amounts = None
        if real_cardmarket:
            if payload.purchasePrices and set(payload.purchasePrices) != {r['id'] for r in items}:
                raise HTTPException(422,'Introduce el precio de todas las cartas de esta compra')
            purchase_amounts = [cents(payload.purchasePrices[r['id']]) if payload.purchasePrices else r['purchase_amount'] for r in items]
            if any(v is None for v in purchase_amounts):
                raise HTTPException(422,'Faltan precios pagados por carta')
            subtotal = sum(purchase_amounts)
            # Partial purchases share overhead; rebalance the entire purchase below after edits.
            siblings = await tx.query_raw('SELECT COUNT(*)::int AS n FROM acquisition_batches WHERE purchase_key=$1 AND user_id=$2 AND id<>$3',batch['purchase_key'],user_id,batch_id) if batch['purchase_key'] else [{'n':0}]
            totals = purchase_totals('cardmarket',subtotal,shipping)
            if siblings[0]['n']:
                totals['fee']=batch['fee']; totals['total']=subtotal+shipping+batch['fee']
            weights = purchase_amounts if subtotal else [r['quantity'] for r in items]
        else:
            totals = dict(subtotal=cents(payload.total),shipping=Decimal(0),fee=Decimal(0),total=cents(payload.total))
        data = dict(acquiredOn=payload.acquiredOn.isoformat(),total=str(totals['total']),costBasis=batch['cost_basis'])
        await enforce_budget(tx,user_id,data,payload.acknowledgeBudget,batch_id)
        allocations = distribute_cost(totals['total'],weights)
        if batch['origin']=='historical' and payload.acquiredOn.isoformat() != str(batch['acquired_on'])[:10]:
            identities = await tx.query_raw('SELECT id,printing_id,quantity,is_foil FROM acquisition_batch_items WHERE batch_id=$1 ORDER BY id',batch_id)
            history = await historical_prices(tx,[dict(printingId=i['printing_id'],isFoil=i['is_foil'],quantity=i['quantity']) for i in identities],payload.acquiredOn)
            weights = []
            for identity in identities:
                h=history.get((identity['printing_id'],identity['is_foil']))
                weights.append(Decimal(str(h['price']))*identity['quantity'] if h else None)
                await tx.execute_raw('UPDATE acquisition_batch_items SET reference_unit_price=$2::numeric,reference_observed_on=$3::date WHERE id=$1',identity['id'],str(h['price']) if h else None,h['day'] if h else None)
            allocations=distribute_cost(totals['total'],weights)
        for index,(item, amount) in enumerate(zip(items,allocations)):
            await tx.execute_raw('UPDATE acquisition_batch_items SET cost_amount=$2::numeric,acquired_on=$3::date,purchase_amount=$4::numeric WHERE id=$1',item['id'],str(amount) if amount is not None else None,data['acquiredOn'],str(purchase_amounts[index]) if purchase_amounts else None)
        await tx.execute_raw('''UPDATE acquisition_batches SET name=$3,seller=$4,cost_total=$5::numeric,fee=$6::numeric,
           acquired_on=$7::date,shipping=$8::numeric,updated_at=NOW() WHERE id=$1 AND user_id=$2''',batch_id,user_id,payload.name,payload.seller,str(totals['subtotal']),str(totals['fee']),data['acquiredOn'],str(totals['shipping']))
        if real_cardmarket and batch['purchase_key']:
            await rebalance_cardmarket_purchase(tx,user_id,batch['purchase_key'],payload.acknowledgeBudget)
    return await purchase_detail(user_id,batch_id)


async def rebalance_cardmarket_purchase(tx,user_id,key,acknowledge):
    batches = await tx.query_raw('SELECT * FROM acquisition_batches WHERE user_id=$1 AND purchase_key=$2 ORDER BY created_at,id',user_id,key)
    cumulative = Decimal(0)
    prior_fee = Decimal(0)
    for batch in batches:
        cumulative += Decimal(str(batch['cost_total']))+Decimal(str(batch['shipping']))
        all_fee=cents(cumulative*Decimal('0.05'))+Decimal('0.30')
        fee=all_fee-prior_fee;prior_fee=all_fee
        await tx.execute_raw('UPDATE acquisition_batches SET fee=$2::numeric,updated_at=NOW() WHERE id=$1',batch['id'],str(fee))
        items=await tx.query_raw('SELECT id,quantity,purchase_amount FROM acquisition_batch_items WHERE batch_id=$1 ORDER BY id',batch['id'])
        weights=[i['purchase_amount'] for i in items] if batch['cost_total'] else [i['quantity'] for i in items]
        amounts=distribute_cost(Decimal(str(batch['cost_total']))+Decimal(str(batch['shipping']))+fee,weights)
        for item,amount in zip(items,amounts):
            await tx.execute_raw('UPDATE acquisition_batch_items SET cost_amount=$2::numeric WHERE id=$1',item['id'],str(amount) if amount is not None else None)
    await tx.execute_raw("UPDATE simulated_collections SET paid_total=(SELECT SUM(cost_total+shipping+fee) FROM acquisition_batches WHERE user_id=$1 AND purchase_key=$2),updated_at=NOW() WHERE user_id=$1 AND id=$2 AND status='purchased'",user_id,key)
    for batch in batches:
        if not batch['annulled_at']:
            await enforce_budget(tx,user_id,dict(acquiredOn=str(batch['acquired_on'])[:10],total='0',costBasis='paid'),acknowledge)


async def annul_purchase(user_id,batch_id):
    async with db.tx() as tx:
        await user_lock(tx,user_id)
        rows = await tx.query_raw('UPDATE acquisition_batches SET annulled_at=COALESCE(annulled_at,NOW()),updated_at=NOW() WHERE id=$1 AND user_id=$2 RETURNING id',batch_id,user_id)
        if not rows:
            raise HTTPException(404,'Compra no encontrada')
    return dict(annulled=True)


async def provenance(user_id,collection_card_id):
    return await db.query_raw('''SELECT b.id,COALESCE(b.name,b.notes,'Compra') AS name,b.purchase_origin AS origin,b.seller,
       b.acquired_on::text AS "acquiredOn",i.quantity,i.cost_amount::float AS "costAmount",b.cost_basis AS "costBasis"
       FROM acquisition_batch_items i JOIN acquisition_batches b ON b.id=i.batch_id
       JOIN user_collections c ON c.id=i.collection_card_id WHERE c.id=$1 AND c.user_id=$2 AND b.user_id=$2 AND b.annulled_at IS NULL
       ORDER BY b.acquired_on,i.id''',collection_card_id,user_id)
