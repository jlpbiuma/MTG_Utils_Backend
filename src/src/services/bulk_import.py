"""Transactional imports and a durable outbox shared with the worker."""
from datetime import timedelta
import hashlib
import json
import uuid
from collections import defaultdict

from fastapi import HTTPException
from src.services.import_service import parse_decklist_text
from src.services.image_resolver import safe_image_uri
from src.services.card_utils import normalize_card_name

BATCH_SIZE = 500
MAX_LINES = 20000


def name_key(name):
    return ' '.join(name.strip().lower().split())


def job_key(identifier):
    return hashlib.sha256(json.dumps(identifier, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prepare(raw_text):
    if len(raw_text.encode()) > 2_000_000 or len(raw_text.splitlines()) > MAX_LINES:
        raise HTTPException(413, 'La colección admite hasta 20.000 líneas y 2 MB por subida.')
    parsed = parse_decklist_text(raw_text)
    if not parsed:
        raise HTTPException(422, 'No hay cartas válidas en la lista.')
    groups = {}
    for item in parsed:
        if not item.name.strip() or item.quantity < 1 or item.quantity > 1_000_000:
            raise HTTPException(422, 'Cada cantidad debe estar entre 1 y 1.000.000.')
        identity = (name_key(item.name), item.setCode or '', item.collectorNumber or '', item.isFoil)
        if identity in groups:
            groups[identity].quantity += item.quantity
        else:
            groups[identity] = item.model_copy()
    if sum(i.quantity for i in groups.values()) > 2_000_000_000:
        raise HTTPException(422, 'La cantidad total es demasiado grande.')
    return list(groups.values())


async def import_text(db, user_id, raw_text, request_key=None):
    items = prepare(raw_text)  # Validate everything before starting a transaction.
    request_key = request_key or str(uuid.uuid4())
    if not request_key or len(request_key) > 128:
        raise HTTPException(422, 'Identificador de importación inválido.')
    digest = hashlib.sha256(raw_text.encode()).hexdigest()
    import_id = str(uuid.uuid4())
    catalog = {}
    names = list({normalize_card_name(i.name) for i in items if not i.setCode})
    for start in range(0, len(names), BATCH_SIZE):
        rows = await db.query_raw('''SELECT id,name,normalized_name,mana_cost,type_line,image_uri,set_code,collector_number
            FROM card_catalog WHERE normalized_name IN (SELECT jsonb_array_elements_text($1::jsonb))''', json.dumps(names[start:start+BATCH_SIZE]))
        catalog.update({name_key(r['name']): r for r in rows})

    user_catalog = {}
    if names:
        for start in range(0, len(names), BATCH_SIZE):
            user_rows = await db.query_raw('''SELECT card_scryfall_id, card_name, set_code, collector_number, mana_cost, type_line, image_uri, is_foil
                FROM user_collections WHERE user_id=$1 AND NOT card_scryfall_id LIKE 'pending:%'
                AND lower(btrim(card_name)) IN (SELECT jsonb_array_elements_text($2::jsonb))''',
                user_id, json.dumps([name_key(n) for n in names[start:start+BATCH_SIZE]]))
            for r in user_rows:
                k = (name_key(r['card_name']), bool(r.get('is_foil', False)))
                if k not in user_catalog:
                    user_catalog[k] = {
                        'id': r['card_scryfall_id'],
                        'name': r['card_name'],
                        'set_code': r.get('set_code'),
                        'collector_number': r.get('collector_number'),
                        'mana_cost': r.get('mana_cost'),
                        'type_line': r.get('type_line'),
                        'image_uri': r.get('image_uri'),
                    }

    missing = [n for n in names if n not in [normalize_card_name(k) for k in catalog.keys()] and n not in [normalize_card_name(k[0]) for k in user_catalog.keys()]]
    if missing:
        try:
            bulk_rows = await db.query_raw('''SELECT b.payload FROM scryfall_bulk_cards b JOIN scryfall_bulk_state s
                ON s.generation=b.generation AND s.kind='cards'
                WHERE b.lang='en' AND b.name_key IN (SELECT jsonb_array_elements_text($1::jsonb))''',
                json.dumps([name_key(n) for n in missing]))
            for row in bulk_rows:
                p = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
                nk = name_key(p.get('name', ''))
                if nk not in catalog:
                    catalog[nk] = {
                        'id': p['id'],
                        'name': p['name'],
                        'set_code': p.get('set'),
                        'collector_number': p.get('collector_number'),
                        'mana_cost': p.get('mana_cost'),
                        'type_line': p.get('type_line'),
                        'image_uri': (p.get('image_uris') or {}).get('normal'),
                    }
        except Exception:
            pass

    entries, jobs = [], {}
    for item in items:
        user_existing = user_catalog.get((name_key(item.name), bool(item.isFoil))) if not item.setCode else None
        cached = user_existing or (catalog.get(name_key(item.name)) if not item.setCode else None)
        if cached:
            identifier = {'id': cached['id']}
        elif item.setCode and item.collectorNumber:
            identifier = {'set': item.setCode.lower(), 'collector_number': item.collectorNumber}
        else:
            identifier = {'name': item.name.strip()}
            if item.setCode:
                identifier['set'] = item.setCode.lower()
        # Canonical names make overlap/case variants share one job.
        if 'name' in identifier:
            identifier['name'] = name_key(identifier['name'])
        key = job_key(identifier)
        card = None
        complete = bool(cached and cached.get('type_line') and safe_image_uri(cached.get('image_uri')))
        if complete:
            card = {'id': cached['id'], 'name': cached['name'], 'set': cached.get('set_code'),
                    'collector_number': cached.get('collector_number'), 'mana_cost': cached.get('mana_cost'),
                    'type_line': cached.get('type_line'), '_local_image': safe_image_uri(cached.get('image_uri'))}
        jobs[key] = {'key': key, 'identifier': identifier, 'status': 'done' if complete else 'queued', 'card': card}
        entries.append((item, key, cached))
    async with db.tx(timeout=timedelta(seconds=120)) as tx:
        inserted = await tx.query_raw('''INSERT INTO collection_imports(id,user_id,request_key,payload_hash,imported_count,unique_cards)
            VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(user_id,request_key) DO NOTHING RETURNING id''',
            import_id,user_id,request_key,digest,sum(i.quantity for i in items),len(items))
        if not inserted:
            prior = (await tx.query_raw('SELECT * FROM collection_imports WHERE user_id=$1 AND request_key=$2',user_id,request_key))[0]
            if prior['payload_hash'] != digest:
                raise HTTPException(409, 'Este identificador ya se usó con otra lista.')
            return response(prior)
        resolved_jobs = {}
        ordered = sorted(jobs.values(), key=lambda j: j['key'])
        for start in range(0,len(ordered),BATCH_SIZE):
            # Locks shared job rows before collection writes; the resolver uses the same order.
            rows = await tx.query_raw('''INSERT INTO enrichment_jobs(key,identifier,status,card)
                SELECT key,identifier,status,card FROM jsonb_to_recordset($1::jsonb)
                AS x(key text,identifier jsonb,status text,card jsonb) ORDER BY key
                ON CONFLICT(key) DO UPDATE SET identifier=enrichment_jobs.identifier
                RETURNING key,card''',json.dumps(ordered[start:start+BATCH_SIZE]))
            resolved_jobs.update({r['key']: r['card'] for r in rows})
        writes = {}
        for item,key,cached in entries:
            card = resolved_jobs.get(key)
            if isinstance(card,str):
                card = json.loads(card)
            sid = card['id'] if card else (cached['id'] if cached else 'pending:' + key)
            identity = (sid,item.isFoil)
            if identity in writes:
                writes[identity]['quantity'] += item.quantity
                continue
            writes[identity] = {'id':str(uuid.uuid4()),'user_id':user_id,'card_scryfall_id':sid,
                'card_name':card.get('name',item.name) if card else (cached['name'] if cached else item.name),'quantity':item.quantity,'is_foil':item.isFoil,
                'set_code':item.setCode or (card.get('set') if card else (cached.get('set_code') if cached else None)),
                'collector_number':item.collectorNumber or (card.get('collector_number') if card else (cached.get('collector_number') if cached else None)),
                'mana_cost':card.get('mana_cost') if card else (cached.get('mana_cost') if cached else None),
                'type_line':card.get('type_line') if card else (cached.get('type_line') if cached else None),
                'image_uri':safe_image_uri(card.get('_local_image')) if card else (safe_image_uri(cached.get('image_uri')) if cached else None),
                'enrichment_key':key}
        values = sorted(writes.values(),key=lambda r:(r['card_scryfall_id'],r['is_foil']))
        for start in range(0,len(values),BATCH_SIZE):
            await tx.execute_raw('''INSERT INTO user_collections
                (id,user_id,card_scryfall_id,card_name,quantity,is_foil,set_code,collector_number,mana_cost,type_line,image_uri,enrichment_key,updated_at)
                SELECT id,user_id,card_scryfall_id,card_name,quantity,is_foil,set_code,collector_number,mana_cost,type_line,image_uri,enrichment_key,NOW()
                FROM jsonb_to_recordset($1::jsonb) AS x(id text,user_id text,card_scryfall_id text,card_name text,quantity int,is_foil boolean,
                set_code text,collector_number text,mana_cost text,type_line text,image_uri text,enrichment_key text)
                ON CONFLICT(user_id,card_scryfall_id,is_foil) DO UPDATE SET
                quantity=user_collections.quantity+EXCLUDED.quantity,updated_at=NOW(),
                enrichment_key=EXCLUDED.enrichment_key''',json.dumps(values[start:start+BATCH_SIZE]))
        for start in range(0,len(ordered),BATCH_SIZE):
            await tx.execute_raw('''INSERT INTO collection_import_items(import_id,job_key)
                SELECT $1,jsonb_array_elements_text($2::jsonb) ON CONFLICT DO NOTHING''',import_id,json.dumps([j['key'] for j in ordered[start:start+BATCH_SIZE]]))
        await tx.execute_raw('UPDATE collection_imports SET unique_cards=$2 WHERE id=$1',import_id,len(values))

        # Auto-reconciliation against user_wants
        user_wants = await tx.query_raw('SELECT id, card_scryfall_id, card_name, quantity FROM user_wants WHERE user_id=$1', user_id)
        resolved_wants = []
        if user_wants:
            imported_by_norm = defaultdict(int)
            imported_by_id = defaultdict(int)
            for v in values:
                imported_by_norm[normalize_card_name(v['card_name'])] += v['quantity']
                if v['card_scryfall_id'] and not v['card_scryfall_id'].startswith('pending:'):
                    imported_by_id[v['card_scryfall_id']] += v['quantity']

            for want in user_wants:
                want_norm = normalize_card_name(want['card_name'])
                want_id = want['card_scryfall_id']
                want_qty = want['quantity']
                avail = imported_by_id.get(want_id, 0) or imported_by_norm.get(want_norm, 0)
                if avail > 0:
                    resolved_qty = min(want_qty, avail)
                    remaining = want_qty - resolved_qty
                    if remaining <= 0:
                        await tx.execute_raw('DELETE FROM user_wants WHERE id=$1', want['id'])
                    else:
                        await tx.execute_raw('UPDATE user_wants SET quantity=$1, updated_at=NOW() WHERE id=$2', remaining, want['id'])
                    resolved_wants.append({
                        'cardName': want['card_name'],
                        'quantityResolved': resolved_qty,
                    })

    return {
        'status': 'success',
        'importId': import_id,
        'importedCount': sum(i.quantity for i in items),
        'uniqueCards': len(values),
        'resolvedWants': resolved_wants,
        'resolvedWantsCount': len(resolved_wants),
    }



def response(row):
    return {'status':'success','importId':row['id'],'importedCount':row['imported_count'],'uniqueCards':row['unique_cards']}


async def import_status(db,user_id,import_id):
    rows = await db.query_raw('SELECT * FROM collection_imports WHERE id=$1 AND user_id=$2',import_id,user_id)
    if not rows:
        raise HTTPException(404,'Importación no encontrada.')
    counts = await db.query_raw('''SELECT j.status,j.phase,count(*)::int AS count FROM collection_import_items i
        JOIN enrichment_jobs j ON j.key=i.job_key WHERE i.import_id=$1 GROUP BY j.status,j.phase''',import_id)
    progress = {'completed':0,'pending':0,'notFound':0,'failed':0,'ambiguous':0,'enriching':0}
    for row in counts:
        key = {'done':'completed','not_found':'notFound','failed':'failed','ambiguous':'ambiguous'}.get(row['status'],'pending')
        progress[key] += row['count']
        if key == 'pending' and row['phase'] == 'enrich':
            progress['enriching'] += row['count']
    return {**response(rows[0]),**progress}


async def retry_import(db,user_id,import_id):
    await import_status(db,user_id,import_id)  # Ownership check before mutation.
    await db.execute_raw("""UPDATE enrichment_jobs SET status='queued',attempts=0,next_attempt_at=NOW(),last_error=NULL
        WHERE status IN ('failed','not_found') AND key IN (SELECT job_key FROM collection_import_items WHERE import_id=$1)""",import_id)
    return await import_status(db,user_id,import_id)
