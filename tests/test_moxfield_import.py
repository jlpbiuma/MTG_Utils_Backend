from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.routers.import_cards import _fetch_moxfield_deck


@pytest.mark.asyncio
async def test_moxfield_import_falls_back_to_v2_when_v3_is_blocked():
    blocked = httpx.Response(403, request=httpx.Request("GET", "https://api2.moxfield.com/v3/decks/all/deck-1"))
    payload = {"name": "Test deck", "commanders": {"Frodo, Adventurous Hobbit": {"quantity": 1}}, "mainboard": {"Sol Ring": {"quantity": 1}}}
    success = httpx.Response(200, json=payload, request=httpx.Request("GET", "https://api2.moxfield.com/v2/decks/all/deck-1"))

    client = MagicMock()
    client.get = AsyncMock(side_effect=[blocked, success])
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)

    with patch("src.routers.import_cards.httpx.AsyncClient", return_value=context):
        result = await _fetch_moxfield_deck("deck-1")

    assert result == payload
    assert client.get.await_count == 2
