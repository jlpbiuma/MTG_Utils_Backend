import pytest
from src.services.card_utils import is_art_card, is_playable_card
from src.services.scryfall_service import ScryfallService


def test_backend_is_art_card_rules():
    art_series = {
        "id": "18263a99-378d-45af-8bc1-7188ca2a83a5",
        "name": "Cloud, Ex-SOLDIER // Cloud, Ex-SOLDIER",
        "set": "afin",
        "set_type": "memorabilia",
        "layout": "art_series",
        "type_line": "Card // Card",
    }
    assert is_art_card(art_series) is True
    assert is_playable_card(art_series) is False

    playable = {
        "id": "07b4e4f8-6a31-4533-be51-668ce3ddc84f",
        "name": "Cloud, Ex-SOLDIER",
        "set": "fic",
        "set_type": "commander",
        "layout": "normal",
        "type_line": "Legendary Creature — Human Soldier Mercenary",
    }
    assert is_art_card(playable) is False
    assert is_playable_card(playable) is True


@pytest.mark.asyncio
async def test_backend_resolve_cards_in_bulk_filters_art_cards(monkeypatch):
    mock_scryfall_response = {
        "data": [
            {
                "id": "07b4e4f8-6a31-4533-be51-668ce3ddc84f",
                "name": "Cloud, Ex-SOLDIER",
                "layout": "normal",
                "set": "fic",
                "type_line": "Legendary Creature — Human Soldier Mercenary",
            },
            {
                "id": "18263a99-378d-45af-8bc1-7188ca2a83a5",
                "name": "Cloud, Ex-SOLDIER // Cloud, Ex-SOLDIER",
                "layout": "art_series",
                "set": "afin",
                "set_type": "memorabilia",
                "type_line": "Card // Card",
            },
        ]
    }

    class MockResponse:
        status_code = 200
        def json(self):
            return mock_scryfall_response

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: MockClient())

    results = await ScryfallService.resolve_cards_in_bulk([{"name": "Cloud, Ex-SOLDIER"}])
    assert len(results) == 1
    assert results[0]["id"] == "07b4e4f8-6a31-4533-be51-668ce3ddc84f"
    assert results[0]["layout"] == "normal"
