from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.manual_opportunity_store import read_saved_rows, save_import


async def test_persistence_replaces_only_users_link_atomically_and_returns_stored_timestamp():
    at = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
    record = NS(id=1, sourceUrl="https://manabox.app/decks/public", cardName="Sol Ring",
                setCode="c21", currency="EUR", subject="vende", importedAt=at,
                extra={"source": "manual", "quantity": 2})
    model = NS(delete_many=AsyncMock(), create_many=AsyncMock(), find_many=AsyncMock(return_value=[record]))
    tx = NS(manualopportunitycard=model, execute_raw=AsyncMock())
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=tx)
    context.__aexit__ = AsyncMock(return_value=False)
    database = NS(tx=MagicMock(return_value=context))
    row = {"card_name": "Sol Ring", "set_code": "c21", "currency": "EUR",
           "extra_json": '{"source":"manual","quantity":2}'}
    with patch("src.services.manual_opportunity_store.db", database):
        saved = await save_import("alice", record.sourceUrl, "vende", [row], at)
    model.delete_many.assert_awaited_once_with(where={"userId": "alice", "sourceUrl": record.sourceUrl})
    data = model.create_many.await_args.kwargs["data"][0]
    assert data["userId"] == "alice" and data["importedAt"] == at
    assert data["extra"].data == {"source": "manual", "quantity": 2}
    assert tx.execute_raw.await_count == 1
    context.__aexit__.assert_awaited_once_with(None, None, None)
    assert saved[0]["id"] == -1 and saved[0]["detected_at"] == at.isoformat()


async def test_read_saved_cards_filters_by_authenticated_user():
    model = NS(find_many=AsyncMock(return_value=[]))
    with patch("src.services.manual_opportunity_store.db", NS(manualopportunitycard=model)):
        assert await read_saved_rows("bob") == []
    model.find_many.assert_awaited_once_with(
        where={"userId": "bob"}, order=[{"importedAt": "desc"}, {"id": "asc"}])
