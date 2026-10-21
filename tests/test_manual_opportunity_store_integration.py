"""Persistence checks against an explicitly configured, isolated test database."""
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import uuid

import pytest
import pytest_asyncio
from prisma import Prisma

from src.services.manual_opportunity_store import save_import, read_saved_rows

URL = os.getenv("MTG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MTG_TEST_DATABASE_URL is not configured")


@pytest_asyncio.fixture
async def database(monkeypatch):
    from src.services import manual_opportunity_store
    schema = "manual_opportunities_" + uuid.uuid4().hex
    admin = Prisma(datasource={"url": URL})
    await admin.connect()
    client = None
    try:
        await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
        parts = urlsplit(URL)
        query = dict(parse_qsl(parts.query))
        query["schema"] = schema
        client = Prisma(datasource={"url": urlunsplit(parts._replace(query=urlencode(query)))})
        await client.connect()
        assert (await client.query_raw("SELECT current_schema() AS name"))[0]["name"] == schema
        sql = (Path(__file__).parents[1] / "prisma/migrations/20261001_manual_opportunities/migration.sql").read_text()
        for statement in sql.split(";"):
            if statement.strip():
                await client.execute_raw(statement)
        monkeypatch.setattr(manual_opportunity_store, "db", client)
        yield client
    finally:
        if client and client.is_connected():
            await client.disconnect()
        await admin.execute_raw(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.disconnect()


ROW = {"card_name": "Sol Ring", "set_code": "c21", "currency": "EUR",
       "extra_json": '{"source":"manual","quantity":2}'}
LINK = "https://manabox.app/decks/public"
AT = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)


async def test_saved_import_survives_reload_and_reimport_is_user_scoped(database):
    await save_import("alice", LINK, "vende", [ROW], AT)
    await save_import("bob", LINK, "vende", [ROW], AT)
    alice = await read_saved_rows("alice")
    assert len(alice) == 1 and alice[0]["id"] < 0
    assert datetime.fromisoformat(alice[0]["detected_at"]) == AT
    later = datetime(2026, 10, 2, 15, 45, tzinfo=timezone.utc)
    await save_import("alice", LINK, "compra_busca", [{**ROW, "card_name": "Lightning Bolt"}], later)
    alice = await read_saved_rows("alice")
    bob = await read_saved_rows("bob")
    assert len(alice) == len(bob) == 1
    assert alice[0]["card_name"] == "Lightning Bolt" and alice[0]["subject"] == "compra_busca"
    assert datetime.fromisoformat(alice[0]["detected_at"]) == later
    assert bob[0]["card_name"] == "Sol Ring" and datetime.fromisoformat(bob[0]["detected_at"]) == AT
    assert await read_saved_rows("other") == []


async def test_failed_reimport_rolls_back_previous_cards(database):
    await save_import("alice", LINK, "vende", [ROW], AT)
    before = await read_saved_rows("alice")
    with pytest.raises(Exception):
        await save_import("alice", LINK, "invalid", [ROW], AT)
    assert await read_saved_rows("alice") == before
