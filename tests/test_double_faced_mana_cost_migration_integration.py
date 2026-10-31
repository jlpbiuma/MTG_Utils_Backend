"""The double-faced mana cost repair fills empty costs only, in an isolated schema."""
import json
import os
import re
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import pytest
import pytest_asyncio
from prisma import Prisma

pytestmark = pytest.mark.skipif(not os.getenv('MTG_TEST_DATABASE_URL'), reason='Requires isolated PostgreSQL test schema')

MIGRATIONS = Path(__file__).parents[1] / 'prisma/migrations'
TABLES = ('card_sets', 'card_catalog', 'card_printings', 'decks', 'deck_cards', 'user_collections', 'user_wants',
          'simulated_collections', 'simulated_cards')


@pytest_asyncio.fixture
async def database():
    url = os.environ['MTG_TEST_DATABASE_URL']
    schema = 'dfc_' + uuid4().hex
    admin = Prisma(datasource={'url': url})
    await admin.connect()
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query)); params['schema'] = schema
    client = Prisma(datasource={'url': urlunsplit(parts._replace(query=urlencode(params)))})
    try:
        await client.connect()
        baseline = (MIGRATIONS / '0_init/migration.sql').read_text()
        for table in TABLES:
            await client.execute_raw(re.search(r'CREATE TABLE "' + table + r'" \(.*?\);', baseline, re.S).group())
        yield client
    finally:
        if client.is_connected(): await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()


async def apply_repair(db):
    sql = (MIGRATIONS / '20261031_double_faced_mana_cost/migration.sql').read_text()
    for statement in sql.split('-- statement-break'):
        await db.execute_raw(statement)


@pytest.mark.asyncio
async def test_repair_fills_front_face_cost_and_is_idempotent(database):
    db = database
    faces = {'mana_cost': None, 'card_faces': [{'mana_cost': '{U}'}, {'mana_cost': ''}]}
    await db.execute_raw("INSERT INTO card_sets(id,code,name,set_type,search_uri,updated_at) VALUES('s','isd','Innistrad','expansion','x',NOW())")
    await db.execute_raw("""INSERT INTO card_catalog(id,name,normalized_name,mana_cost,details_es,updated_at) VALUES
        ('delver','Delver of Secrets // Insectile Aberration','delver of secrets',NULL,$1::jsonb,NOW()),
        ('bolt','Lightning Bolt','lightning bolt','{R}',$2::jsonb,NOW())""",
        json.dumps(faces), json.dumps({'mana_cost': '{R}', 'card_faces': [{'mana_cost': '{9}'}]}))
    await db.execute_raw("INSERT INTO card_printings(id,catalog_id,set_id,collector_number,updated_at) VALUES('delver-isd','delver','s','51',NOW())")
    await db.execute_raw("INSERT INTO decks(id,user_id,name,created_at,updated_at) VALUES('d','alice','d',NOW(),NOW())")
    await db.execute_raw("""INSERT INTO deck_cards(id,deck_id,card_scryfall_id,card_name,quantity,mana_cost) VALUES
        ('dc1','d','delver-isd','Delver of Secrets',1,NULL),('dc2','d','bolt','Lightning Bolt',1,'{R}')""")
    await db.execute_raw("""INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,updated_at)
        VALUES('uc1','alice','delver','Delver of Secrets',1,'2026-01-01')""")

    await apply_repair(db)
    await apply_repair(db)

    catalog = {row['id']: row for row in await db.query_raw('SELECT id, mana_cost, details_es FROM card_catalog')}
    assert catalog['delver']['mana_cost'] == '{U}'
    assert catalog['delver']['details_es']['mana_cost'] == '{U}'
    assert catalog['bolt']['mana_cost'] == '{R}'
    decks = {row['id']: row['mana_cost'] for row in await db.query_raw('SELECT id, mana_cost FROM deck_cards')}
    assert decks == {'dc1': '{U}', 'dc2': '{R}'}
    owned = await db.query_raw("SELECT mana_cost, updated_at::text AS updated FROM user_collections")
    assert owned[0]['mana_cost'] == '{U}'
    assert owned[0]['updated'].startswith('2026-01-01')
