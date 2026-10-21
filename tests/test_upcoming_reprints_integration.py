"""Execute the preview SQL against an isolated PostgreSQL schema."""
import json
import os
import uuid
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import pytest
import pytest_asyncio
from prisma import Prisma
from src.services.upcoming_reprints import upcoming_reprints_by_name

URL = os.getenv('MTG_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='Set MTG_TEST_DATABASE_URL for isolated PostgreSQL reprint tests')


def schema_url(schema):
    parts = urlsplit(URL)
    params = dict(parse_qsl(parts.query))
    params['schema'] = schema
    return urlunsplit(parts._replace(query=urlencode(params)))


@pytest_asyncio.fixture
async def database():
    schema = 'test_reprints_' + uuid.uuid4().hex
    admin = Prisma(datasource={'url': schema_url('public')})
    client = Prisma(datasource={'url': schema_url(schema)})
    await admin.connect()
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    try:
        await client.connect()
        assert (await client.query_raw('SELECT current_schema() AS name'))[0]['name'] == schema
        await client.execute_raw('CREATE TABLE card_sets(code text PRIMARY KEY, name text, released_at timestamp, is_digital bool, set_type text)')
        await client.execute_raw('CREATE TABLE scryfall_bulk_state(kind text PRIMARY KEY, generation text)')
        await client.execute_raw('CREATE TABLE scryfall_bulk_cards(generation text, name_key text, set_code text, payload jsonb)')
        await client.execute_raw('CREATE INDEX ON scryfall_bulk_cards(generation,name_key)')
        await client.execute_raw("INSERT INTO card_sets VALUES ('fdc','Foundations Commander','2024-11-15',false,'commander'), ('digital','Digital','2027-01-01',true,'expansion'), ('art','Art','2027-01-01',false,'art_series'), ('missing','Unknown',NULL,false,'expansion')")
        await client.execute_raw("INSERT INTO scryfall_bulk_state VALUES ('cards','current')")
        for name, code, generation, reprint, games, layout in [
            ('taurean mauler','fdc','current',True,['paper'],'normal'),
            ('hulking raptor','fdc','current',True,['paper'],'normal'),
            ('excluded','fdc','stale',True,['paper'],'normal'),
            ('excluded','digital','current',True,['paper'],'normal'),
            ('excluded','art','current',True,['paper'],'normal'),
            ('excluded','fdc','current',False,['paper'],'normal'),
            ('excluded','fdc','current',True,['arena'],'normal'),
            ('excluded','fdc','current',True,['paper'],'art_series'),
            ('excluded','missing','current',True,['paper'],'normal'),
        ]:
            await client.execute_raw('INSERT INTO scryfall_bulk_cards VALUES ($1,$2,$3,$4::jsonb)', generation, name, code, json.dumps({'reprint': reprint, 'games': games, 'layout': layout}))
        yield client
    finally:
        if client.is_connected():
            await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()


@pytest.mark.asyncio
async def test_current_bulk_preview_filters_reprints_dates_paper_and_stale_generations(database):
    names = ['Taurean Mauler', 'Hulking Raptor', 'Excluded']
    upcoming = await upcoming_reprints_by_name(names, [], database, today=date(2026, 10, 1))
    assert set(upcoming) == {'taurean mauler', 'hulking raptor'}
    assert all(value == [{'setCode': 'fdc', 'setName': 'Foundations Commander', 'releaseDate': '2026-10-02'}] for value in upcoming.values())
    assert await upcoming_reprints_by_name(names, [], database, today=date(2026, 10, 2)) == {}
