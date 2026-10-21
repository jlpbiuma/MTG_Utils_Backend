"""Exercise every inventory write through the database invariant in an isolated schema."""
import asyncio
import os
import re
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import pytest
import pytest_asyncio
from prisma import Prisma
from src.services.deck_assignment_service import DeckAssignmentService

pytestmark = pytest.mark.skipif(not os.getenv('MTG_TEST_DATABASE_URL'), reason='Requires isolated PostgreSQL test schema')


@pytest_asyncio.fixture
async def database(monkeypatch):
    url = os.environ['MTG_TEST_DATABASE_URL']
    schema = 'assignment_' + uuid4().hex
    admin = Prisma(datasource={'url': url})
    await admin.connect()
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query)); params['schema'] = schema
    scoped_url = urlunsplit(parts._replace(query=urlencode(params)))
    client = Prisma(datasource={'url': scoped_url})
    try:
        await client.connect()
        baseline = (Path(__file__).parents[1] / 'prisma/migrations/0_init/migration.sql').read_text()
        for table in ('decks', 'deck_cards', 'user_collections'):
            await client.execute_raw(re.search(r'CREATE TABLE "' + table + r'" \(.*?\);', baseline, re.S).group())
        await client.execute_raw('CREATE UNIQUE INDEX inventory_printing_finish ON user_collections(user_id,card_scryfall_id,is_foil)')
        await client.execute_raw('ALTER TABLE user_collections ADD COLUMN acquired_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP')
        sql = (Path(__file__).parents[1] / 'prisma/migrations/20261004_deck_assignment_notices/migration.sql').read_text()
        for statement in sql.split('-- statement-break'):
            await client.execute_raw(statement)
        monkeypatch.setattr('src.services.deck_assignment_service.db', client)
        yield client, scoped_url
    finally:
        if client.is_connected(): await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()


async def deck(db, did='a', user='alice', quantity=2, assigned=0, archived=False, name='Sol Ring', sideboard=False):
    await db.execute_raw('INSERT INTO decks(id,user_id,name,is_archived,created_at,updated_at) VALUES($1,$2,$1,$3,\'2026-01-01\',NOW())', did, user, archived)
    await db.execute_raw('INSERT INTO deck_cards(id,deck_id,card_scryfall_id,card_name,quantity,assigned_quantity,is_sideboard) VALUES($1,$2,$3,$4,$5,$6,$7)', 'card-'+did, did, 'printing-'+did, name, quantity, assigned, sideboard)


async def acquire(db, cid='inventory', quantity=1, name='Sol Ring', foil=False):
    await db.execute_raw('''INSERT INTO user_collections(id,user_id,card_scryfall_id,card_name,quantity,is_foil)
      VALUES($1,'alice',$1,$2,$3,$4) ON CONFLICT (user_id,card_scryfall_id,is_foil)
      DO UPDATE SET quantity=user_collections.quantity+EXCLUDED.quantity''', cid, name, quantity, foil)


@pytest.mark.asyncio
async def test_stacked_notices_allocation_and_persistent_user_scoped_accept(database):
    db, _ = database
    await deck(db, quantity=1)
    await deck(db, 'b', quantity=2)
    await deck(db, 'archived', archived=True)
    await deck(db, 'other', user='bob')
    await acquire(db, quantity=4, name='  SOL   RING // Back Face', foil=True)
    rows = await db.query_raw('SELECT id,assigned_quantity FROM deck_cards ORDER BY id')
    assert {r['id']:r['assigned_quantity'] for r in rows} == {'card-a':1,'card-b':2,'card-archived':0,'card-other':0}
    notices = await DeckAssignmentService.pending('alice')
    assert sorted(n['quantity'] for n in notices) == [1,2]
    assert not await DeckAssignmentService.accept('bob', notices[0]['id'])
    assert await DeckAssignmentService.accept('alice', notices[0]['id'])
    assert await DeckAssignmentService.accept('alice', notices[0]['id'])
    assert len(await DeckAssignmentService.pending('alice')) == 1
    await db.execute_raw("UPDATE user_collections SET quantity=quantity WHERE id='inventory'")
    assert len(await DeckAssignmentService.pending('alice')) == 1
    assert (await db.query_raw('SELECT SUM(assigned_quantity)::int AS total FROM deck_cards'))[0]['total'] == 3


@pytest.mark.asyncio
async def test_quantity_increase_preferred_deck_and_transaction_rollback(database):
    db, _ = database
    await deck(db, quantity=3)
    await deck(db, 'b', quantity=3)
    async with db.tx() as tx:
        await tx.query_raw("SELECT set_config('mtg.preferred_deck', 'b', true)")
        await acquire(tx)
    assert (await db.query_raw("SELECT assigned_quantity AS qty FROM deck_cards WHERE id='card-b'"))[0]['qty'] == 1
    await db.execute_raw("UPDATE user_collections SET quantity=quantity+1 WHERE id='inventory'")
    assert len(await DeckAssignmentService.pending('alice')) == 2
    with pytest.raises(RuntimeError):
        async with db.tx() as tx:
            await acquire(tx, cid='new', quantity=3)
            raise RuntimeError('rollback')
    assert len(await DeckAssignmentService.pending('alice')) == 2
    assert (await db.query_raw('SELECT SUM(quantity)::int AS qty FROM user_collections'))[0]['qty'] == 2


@pytest.mark.asyncio
async def test_sideboard_and_existing_assignments_consume_physical_copies(database):
    db, _ = database
    await deck(db, quantity=2, assigned=1)
    await deck(db, 'b', quantity=2, sideboard=True)
    # Simulate an existing assigned copy before enabling the trigger.
    async with db.tx() as tx:
        await tx.query_raw("SELECT set_config('mtg.suppress_acquisition', 'on', true)")
        await acquire(tx)
    await acquire(db, quantity=3)
    rows = await db.query_raw('SELECT SUM(assigned_quantity)::int AS qty FROM deck_cards')
    assert rows[0]['qty'] == 4
    assert sum(n['quantity'] for n in await DeckAssignmentService.pending('alice')) == 3


@pytest.mark.asyncio
async def test_transfer_suppression_and_simultaneous_acquisitions(database):
    db, url = database
    await deck(db, quantity=2)
    async with db.tx() as tx:
        await tx.query_raw("SELECT set_config('mtg.suppress_acquisition', 'on', true)")
        await acquire(tx, quantity=2)
    assert await DeckAssignmentService.pending('alice') == []
    second = Prisma(datasource={'url': url})
    await second.connect()
    try:
        await asyncio.gather(acquire(db, cid='one'), acquire(second, cid='two'))
    finally:
        await second.disconnect()
    assert sum(n['quantity'] for n in await DeckAssignmentService.pending('alice')) == 2
    assert (await db.query_raw('SELECT assigned_quantity AS qty FROM deck_cards'))[0]['qty'] == 2


@pytest.mark.asyncio
async def test_version_merge_never_creates_an_acquisition(database, monkeypatch):
    from src.services.collection_service import CollectionService
    db, _ = database
    monkeypatch.setattr('src.services.collection_service.db', db)
    await deck(db, quantity=5)
    async with db.tx() as tx:
        await tx.query_raw("SELECT set_config('mtg.suppress_acquisition', 'on', true)")
        await acquire(tx, cid='old', quantity=1)
        await acquire(tx, cid='new', quantity=2)
    merged = await CollectionService.update_card_version('alice', 'old', 'new')
    assert merged.quantity == 3
    assert await DeckAssignmentService.pending('alice') == []
    await acquire(db, cid='new')
    assert sum(n['quantity'] for n in await DeckAssignmentService.pending('alice')) == 1


@pytest.mark.asyncio
async def test_deck_add_missing_assigns_only_once_to_the_requested_deck(database, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.services.deck_service import DeckService
    db, _ = database
    await deck(db, 'a', quantity=7)
    await deck(db, 'b', quantity=7)
    monkeypatch.setattr('src.services.deck_service.db', db)
    detail_card = SimpleNamespace(id='card-b', missingCount=1)
    monkeypatch.setattr(DeckService, 'get_deck_detail', AsyncMock(return_value=SimpleNamespace(cards=[detail_card])))
    result = await DeckService.add_missing_card_to_collection('card-b', 'alice')
    assert result['addedCount'] == 1
    quantities = {r['id']:r['assigned_quantity'] for r in await db.query_raw('SELECT id,assigned_quantity FROM deck_cards')}
    assert quantities == {'card-a': 0, 'card-b': 1}
    notices = await DeckAssignmentService.pending('alice')
    assert len(notices) == 1 and notices[0]['deckId'] == 'b' and notices[0]['quantity'] == 1
