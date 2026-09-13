"""Opt-in tests against a disposable schema, never application collections."""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from prisma import Prisma
from src.services.bulk_import import import_text, import_status, prepare

URL=os.getenv('MTG_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='Set MTG_TEST_DATABASE_URL for isolated PostgreSQL integration tests')


@pytest_asyncio.fixture
async def database():
    schema='test_import_'+uuid.uuid4().hex
    admin=Prisma(datasource={'url':URL+'?schema=public'})
    await admin.connect()
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    client=Prisma(datasource={'url':URL+'?schema='+schema})
    try:
        for table in ('user_collections','card_catalog'):
            await admin.execute_raw(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
        await client.connect()
        assert (await client.query_raw('SELECT current_schema() AS name'))[0]['name']==schema
        # LIKE INCLUDING ALL renames unique indexes to *_idx; reproduce the
        # original production index name before exercising its migration.
        indexes=await client.query_raw("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename='user_collections'")
        for index in indexes:
            if index['indexdef'].endswith('(user_id, card_scryfall_id)') and index['indexname'] != 'user_collections_user_id_card_scryfall_id_key':
                await client.execute_raw('ALTER INDEX "'+index['indexname']+'" RENAME TO user_collections_user_id_card_scryfall_id_key')
        sql=(Path(__file__).parents[1]/'prisma/bulk_import.sql').read_text()
        for statement in sql.split(';'):
            statement='\n'.join(l for l in statement.splitlines() if not l.strip().startswith('--')).strip()
            if statement and statement not in ('BEGIN','COMMIT'):
                await client.execute_raw(statement)
        yield client
    finally:
        if client.is_connected(): await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()


@pytest.mark.asyncio
async def test_5000_rows_and_replay(database):
    names=json.loads((Path(__file__).parents[1]/'benchmarks/card_names.json').read_text())[:1000]
    raw='\n'.join('1 '+n for _ in range(5) for n in names)
    started=time.perf_counter()
    result=await import_text(database,'user-a',raw,'request-1')
    seconds=time.perf_counter()-started
    rows=await database.query_raw('SELECT card_name,quantity FROM user_collections')
    assert len(rows)==1000 and all(r['quantity']==5 for r in rows)
    assert result['uniqueCards']==1000 and result['importedCount']==5000
    assert await import_text(database,'user-a',raw,'request-1')==result
    assert (await database.query_raw('SELECT sum(quantity)::int AS n FROM user_collections'))[0]['n']==5000
    progress=await import_status(database,'user-a',result['importId'])
    assert progress['pending']==1000
    with pytest.raises(HTTPException) as error:
        await import_status(database,'user-b',result['importId'])
    assert error.value.status_code==404
    print(f'Production import 5000 lines/1000 identities: {seconds:.3f}s')


@pytest.mark.asyncio
async def test_concurrent_same_request_applied_once(database):
    results=await asyncio.gather(*(import_text(database,'user','2 Sol Ring','same') for _ in range(4)))
    assert len({r['importId'] for r in results})==1
    rows=await database.query_raw('SELECT quantity FROM user_collections')
    assert rows==[{'quantity':2}]
    with pytest.raises(HTTPException) as error:
        await import_text(database,'user','3 Sol Ring','same')
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_distinct_requests_atomic_increments_and_foil_editions(database):
    raw='1 Sol Ring (c21) 263\n2 Sol Ring (c21) 263 *F*\n3 Sol Ring (c20) 252'
    await asyncio.gather(*(import_text(database,'user',raw,str(i)) for i in range(3)))
    rows=await database.query_raw('SELECT quantity,is_foil,set_code FROM user_collections ORDER BY set_code,is_foil')
    assert rows==[{'quantity':9,'is_foil':False,'set_code':'c20'},{'quantity':3,'is_foil':False,'set_code':'c21'},{'quantity':6,'is_foil':True,'set_code':'c21'}]
    assert len(await database.query_raw('SELECT key FROM enrichment_jobs'))==2


@pytest.mark.asyncio
async def test_invalid_input_never_writes(database):
    with pytest.raises(HTTPException):
        await import_text(database,'user','1 Sol Ring\n0 Lightning Bolt','bad')
    assert not await database.query_raw('SELECT id FROM collection_imports')
    assert not await database.query_raw('SELECT id FROM user_collections')


@pytest.mark.asyncio
async def test_complete_local_card_does_not_schedule_remote_work(database):
    await database.execute_raw('''INSERT INTO card_catalog(id,name,normalized_name,type_line,image_uri,updated_at)
        VALUES('real-sol-ring','Sol Ring','sol ring','Artifact','http://localhost:8080/images/sol.webp',NOW())''')
    result=await import_text(database,'user','1 Sol Ring','cached')
    progress=await import_status(database,'user',result['importId'])
    assert progress['completed']==1 and progress['pending']==0
    rows=await database.query_raw('SELECT card_scryfall_id,image_uri FROM user_collections')
    assert rows[0]['card_scryfall_id']=='real-sol-ring'


@pytest.mark.asyncio
async def test_import_accumulates_on_existing_user_collection_card(database):
    # User already has a specific printing in their collection
    await database.execute_raw('''INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil,set_code,collector_number,type_line,image_uri,updated_at)
        VALUES('existing-col-1','user-x','custom-printing-123','Counterspell',2,false,'mh2','267','Instant','http://localhost:8080/images/cs.webp',NOW())''')
    # Import a decklist without set code specifying 3 Counterspell
    result=await import_text(database,'user-x','3 Counterspell','import-accum')
    rows=await database.query_raw("SELECT card_scryfall_id,card_name,quantity,set_code,collector_number FROM user_collections WHERE user_id='user-x'")
    # Must accumulate on the existing printing: 2 + 3 = 5 copies, no duplicate rows
    assert len(rows)==1
    assert rows[0]['card_scryfall_id']=='custom-printing-123'
    assert rows[0]['quantity']==5
    assert rows[0]['set_code']=='mh2'

