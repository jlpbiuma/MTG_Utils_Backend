"""Exercise the actual bilingual catalog SQL in an isolated PostgreSQL schema."""
import os
import uuid
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from prisma import Prisma
import pytest
import pytest_asyncio

from src.services.scryfall_service import ScryfallService


URL = os.getenv("MTG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Set MTG_TEST_DATABASE_URL for isolated PostgreSQL tests")


def schema_url(schema):
    parts = urlsplit(URL)
    params = dict(parse_qsl(parts.query))
    params["schema"] = schema
    return urlunsplit(parts._replace(query=urlencode(params)))


@pytest_asyncio.fixture
async def database():
    schema = "test_search_" + uuid.uuid4().hex
    admin = Prisma(datasource={"url": schema_url("public")})
    client = Prisma(datasource={"url": schema_url(schema)})
    await admin.connect()
    await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
    try:
        await client.connect()
        assert (await client.query_raw("SELECT current_schema() AS name"))[0]["name"] == schema
        await client.execute_raw("""CREATE TABLE card_catalog (
            id text PRIMARY KEY, name text, name_es text, mana_cost text,
            type_line text, image_uri text, set_code text, collector_number text
        )""")
        await client.execute_raw("""INSERT INTO card_catalog (id, name, name_es, type_line, set_code)
            VALUES ('bolt', 'Lightning Bolt', 'Relámpago', 'Instant', 'm10'),
                   ('ring', 'Sol Ring', 'Anillo solar', 'Artifact', 'cmm'),
                   ('english', 'English Only', NULL, 'Artifact', 'cmm')""")
        yield client
    finally:
        if client.is_connected():
            await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected", [
    ("Relámpago", "bolt"), ("RELAMPAGO", "bolt"), ("relamp", "bolt"),
    ("Lightning Bolt", "bolt"), ("anillo", "ring"), ("English Only", "english"),
])
async def test_catalog_sql_finds_both_languages(database, query, expected):
    with patch("src.services.scryfall_service.db", database):
        rows = await ScryfallService._search_catalog_rows(query, 15)
        suggestions = await ScryfallService.autocomplete_local(query)
    assert [row["id"] for row in rows] == [expected]
    assert suggestions == [rows[0]["name"]]

@pytest.mark.asyncio
async def test_offer_name_resolution_uses_real_spanish_aliases_and_localized_faces(database):
    import json
    from pathlib import Path
    from src.services.offer_name_resolver import resolve_offer_names
    from src.services.scryfall_service import fold_search_text
    await database.execute_raw('ALTER TABLE card_catalog ADD COLUMN details_es jsonb')
    fixture = json.loads((Path(__file__).parent / 'fixtures/offers/spanish-catalog.json').read_text())
    for card in fixture:
        await database.execute_raw('''INSERT INTO card_catalog(id,name,name_es,type_line,set_code,collector_number)
            VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(id) DO UPDATE SET name_es=EXCLUDED.name_es''',
            card['printing_id'], card['name'], card['name_es'], card['type_line'], card['set_code'], card['collector_number'])
    resolved = await resolve_offer_names([card['name_es'] for card in fixture] + ['RELAMPAGO', 'Fuego', 'Relámp'], database)
    assert all(resolved[fold_search_text(card['name_es'])] == card['name'] for card in fixture)
    assert resolved['relampago'] == 'Lightning Bolt' and resolved['fuego'] == 'Fire // Ice'
    assert 'relamp' not in resolved
    await database.execute_raw("UPDATE card_catalog SET name_es=NULL,details_es=$1::jsonb WHERE id='fire-ice'",
                               json.dumps({'card_faces': [{'printed_name': 'Fuego'}, {'printed_name': 'Hielo'}]}))
    assert await resolve_offer_names(['Hielo'], database) == {'hielo': 'Fire // Ice'}
