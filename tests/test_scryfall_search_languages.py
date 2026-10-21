"""Bilingual searches retain canonical names for decks and collections."""
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.routers.scryfall import router
from src.services.scryfall_service import ScryfallService


def client_for(cards):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": cards, "total_cards": len(cards), "has_more": False}
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["Relámpago", "RELAMPAGO", "Lightning Bolt", "Lightning"])
async def test_local_search_matches_both_names_without_network(query):
    database = MagicMock()
    database.query_raw = AsyncMock(return_value=[{
        "id": "bolt", "name": "Lightning Bolt", "name_es": "Relámpago",
        "type_line": "Instant", "set_code": "m10",
    }])
    database.cardprinting.find_many = AsyncMock(return_value=[])
    with patch("src.services.scryfall_service.db", database), \
         patch("src.services.scryfall_service.httpx.AsyncClient") as network:
        result = await ScryfallService.search_cards(query)
    assert result["source"] == "local"
    assert result["data"][0]["name"] == "Lightning Bolt"
    assert result["data"][0]["printed_name"] == "Relámpago"
    network.assert_not_called()


@pytest.mark.asyncio
async def test_spanish_exact_match_ranks_above_partial_matches():
    cards = [
        {"id": "a", "name": "A partial match", "printed_name": "Relámpago de ejemplo"},
        {"id": "b", "name": "Lightning Bolt", "printed_name": "Relámpago"},
    ]
    with patch.object(ScryfallService, "search_cards_local", AsyncMock(return_value=cards)), \
         patch("src.services.scryfall_service.httpx.AsyncClient") as network:
        result = await ScryfallService.search_cards("relampago")
    assert [card["id"] for card in result["data"]] == ["b", "a"]
    network.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["Relámp", "Lightning"])
async def test_live_name_search_includes_english_and_spanish(query):
    client = client_for([{
        "id": "bolt-es", "name": "Lightning Bolt", "printed_name": "Relámpago", "lang": "es",
    }])
    with patch.object(ScryfallService, "search_cards_local", AsyncMock(return_value=[])), \
         patch("src.services.scryfall_service.httpx.AsyncClient", client):
        result = await ScryfallService.search_cards(query, page=2)
    params = client.return_value.get.await_args.kwargs["params"]
    assert "(lang:en OR lang:es)" in params["q"]
    assert "game:paper -is:digital -set_type:alchemy" in params["q"]
    assert params["page"] == 2
    assert result["data"][0]["name"] == "Lightning Bolt"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["lang:ja t:creature", 'name:"Sol Ring"', "t:creature OR t:artifact"])
async def test_advanced_search_keeps_explicit_syntax(query):
    client = client_for([])
    with patch.object(ScryfallService, "search_cards_local", AsyncMock(return_value=[])), \
         patch("src.services.scryfall_service.httpx.AsyncClient", client):
        await ScryfallService.search_cards(query)
    assert client.return_value.get.await_args.kwargs["params"]["q"] == (
        f"({query}) game:paper -is:digital -set_type:alchemy"
    )


@pytest.mark.asyncio
async def test_spanish_autocomplete_falls_back_to_bilingual_search():
    client = client_for([])
    cards = [{"id": "a", "name": "Lightning Bolt"}, {"id": "b", "name": "Lightning Bolt"}]
    with patch.object(ScryfallService, "autocomplete_local", AsyncMock(return_value=[])), \
         patch.object(ScryfallService, "search_cards", AsyncMock(return_value={"data": cards})) as search, \
         patch("src.services.scryfall_service.httpx.AsyncClient", client):
        names = await ScryfallService.autocomplete_cards("Relámp")
    assert names == ["Lightning Bolt"]
    search.assert_awaited_once_with("Relámp", prefer_local=True)


@pytest.mark.asyncio
async def test_live_results_rank_spanish_name_and_merge_local_without_duplicates():
    cards = [
        {"id": "partial", "name": "A partial match", "printed_name": "Relámpago de ejemplo"},
        {"id": "bolt-es", "name": "Lightning Bolt", "printed_name": "Relámpago"},
    ]
    local = [{"id": "bolt-local", "name": "Lightning Bolt", "printed_name": "Relámpago"}]
    with patch.object(ScryfallService, "search_cards_local", AsyncMock(return_value=local)), \
         patch("src.services.scryfall_service.httpx.AsyncClient", client_for(cards)):
        result = await ScryfallService.search_cards('name:"Relámpago"')
    # Name operators are not ranking needles; use a plain name on a local miss.
    with patch.object(ScryfallService, "search_cards_local", AsyncMock(return_value=[])), \
         patch("src.services.scryfall_service.httpx.AsyncClient", client_for(cards)):
        ranked = await ScryfallService.search_cards("Relámpago")
    assert len(result["data"]) == 2
    assert [card["id"] for card in ranked["data"]] == ["bolt-es", "partial"]


@pytest.mark.asyncio
async def test_english_autocomplete_keeps_fast_path():
    with patch.object(ScryfallService, "autocomplete_local", AsyncMock(return_value=[])), \
         patch.object(ScryfallService, "search_cards", AsyncMock()) as search, \
         patch("src.services.scryfall_service.httpx.AsyncClient", client_for(["Lightning Bolt"])):
        names = await ScryfallService.autocomplete_cards("Light")
    assert names == ["Lightning Bolt"]
    search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["Relámpago", "relampago", "Lightning Bolt"])
async def test_search_endpoint_accepts_both_languages(query):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    database = MagicMock()
    database.query_raw = AsyncMock(return_value=[{
        "id": "bolt", "name": "Lightning Bolt", "name_es": "Relámpago", "type_line": "Instant",
    }])
    database.cardprinting.find_many = AsyncMock(return_value=[])
    with patch("src.services.scryfall_service.db", database), \
         patch("src.services.scryfall_service.httpx.AsyncClient") as network:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/api/scryfall/search", params={"q": query})
    assert result.status_code == 200
    assert result.json()["data"][0]["id"] == "bolt"
    assert result.json()["data"][0]["name"] == "Lightning Bolt"
    network.assert_not_called()
