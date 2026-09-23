import pytest
from src.services.card_utils import (
    is_digital_or_arena_card,
    is_playable_card,
    is_catalog_record_playable,
    is_arena_or_digital_set_code,
)
from src.services.scryfall_service import ScryfallService


def test_is_arena_or_digital_set_code():
    assert is_arena_or_digital_set_code("ana") is True
    assert is_arena_or_digital_set_code("anb") is True
    assert is_arena_or_digital_set_code("hbg") is True
    assert is_arena_or_digital_set_code("j21") is True
    assert is_arena_or_digital_set_code("y22") is True
    assert is_arena_or_digital_set_code("ydmu") is True
    assert is_arena_or_digital_set_code("yblb") is True
    assert is_arena_or_digital_set_code("ha1") is True
    assert is_arena_or_digital_set_code("ea1") is True
    assert is_arena_or_digital_set_code("aa1") is True
    assert is_arena_or_digital_set_code("me1") is True
    assert is_arena_or_digital_set_code("tpr") is True
    # Paper sets
    assert is_arena_or_digital_set_code("tmp") is False
    assert is_arena_or_digital_set_code("mrd") is False
    assert is_arena_or_digital_set_code("mh3") is False
    assert is_arena_or_digital_set_code("ltr") is False
    assert is_arena_or_digital_set_code("dmu") is False


def test_is_digital_or_arena_card_by_name():
    card_a_vivi = {
        "id": "111",
        "name": "A-Vivi Ornitier",
        "set": "mh3",
        "collector_number": "A-123",
        "layout": "normal",
        "type_line": "Creature",
    }
    assert is_digital_or_arena_card(card_a_vivi) is True
    assert is_playable_card(card_a_vivi) is False

    # Double faced card with A- face
    card_dfc_a = {
        "id": "222",
        "name": "Normal Front",
        "card_faces": [{"name": "A-Back Face", "type_line": "Instant"}],
        "set": "dmu",
        "collector_number": "45",
        "layout": "transform",
        "type_line": "Creature",
    }
    assert is_digital_or_arena_card(card_dfc_a) is True
    assert is_playable_card(card_dfc_a) is False


def test_is_digital_or_arena_card_by_collector_number():
    card_a_collector = {
        "id": "333",
        "name": "The One Ring",
        "set": "ltr",
        "collector_number": "A-246",
        "layout": "normal",
        "type_line": "Legendary Artifact",
    }
    assert is_digital_or_arena_card(card_a_collector) is True
    assert is_playable_card(card_a_collector) is False


def test_is_digital_or_arena_card_by_flags():
    # digital is True
    card_digital = {
        "id": "444",
        "name": "Digital Card",
        "set": "xyz",
        "collector_number": "1",
        "digital": True,
        "type_line": "Sorcery",
    }
    assert is_digital_or_arena_card(card_digital) is True
    assert is_playable_card(card_digital) is False

    # games without paper
    card_games_arena = {
        "id": "555",
        "name": "Arena Only Card",
        "set": "xyz",
        "collector_number": "1",
        "games": ["arena"],
        "type_line": "Instant",
    }
    assert is_digital_or_arena_card(card_games_arena) is True
    assert is_playable_card(card_games_arena) is False

    # arena security stamp
    card_arena_stamp = {
        "id": "666",
        "name": "Arena Stamp Card",
        "set": "xyz",
        "collector_number": "1",
        "security_stamp": "arena",
        "type_line": "Creature",
    }
    assert is_digital_or_arena_card(card_arena_stamp) is True
    assert is_playable_card(card_arena_stamp) is False

    # set_type or layout alchemy
    card_alchemy_type = {
        "id": "777",
        "name": "Alchemy Type Card",
        "set": "xyz",
        "collector_number": "1",
        "set_type": "alchemy",
        "type_line": "Enchantment",
    }
    assert is_digital_or_arena_card(card_alchemy_type) is True
    assert is_playable_card(card_alchemy_type) is False


def test_is_playable_card_accepts_real_paper():
    real_card = {
        "id": "888",
        "name": "Vivi Ornitier",
        "set": "fin",
        "collector_number": "42",
        "layout": "normal",
        "type_line": "Legendary Creature — Human Black Mage",
        "games": ["paper", "arena"],
        "digital": False,
    }
    assert is_digital_or_arena_card(real_card) is False
    assert is_playable_card(real_card) is True


def test_is_catalog_record_playable_arena():
    class DummyCatalog:
        def __init__(self, name, set_code, collector_number, type_line="Creature"):
            self.name = name
            self.setCode = set_code
            self.collectorNumber = collector_number
            self.typeLine = type_line

    assert is_catalog_record_playable(DummyCatalog("A-Vivi Ornitier", "mh3", "A-123")) is False
    assert is_catalog_record_playable(DummyCatalog("The One Ring", "ltr", "A-246")) is False
    assert is_catalog_record_playable(DummyCatalog("Some Card", "y22", "1")) is False
    assert is_catalog_record_playable(DummyCatalog("Some Card", "ana", "1")) is False
    assert is_catalog_record_playable(DummyCatalog("Vivi Ornitier", "fin", "42")) is True


@pytest.mark.asyncio
async def test_scryfall_search_adds_paper_filters(monkeypatch):
    captured_params = {}

    class MockResponse:
        status_code = 200

        def json(self):
            return {
                "total_cards": 2,
                "has_more": False,
                "data": [
                    {
                        "id": "111",
                        "name": "Vivi Ornitier",
                        "set": "fin",
                        "collector_number": "42",
                        "layout": "normal",
                        "type_line": "Legendary Creature",
                        "games": ["paper"],
                    },
                    {
                        "id": "222",
                        "name": "A-Vivi Ornitier",
                        "set": "mh3",
                        "collector_number": "A-42",
                        "layout": "normal",
                        "type_line": "Legendary Creature",
                    },
                ],
            }

        def raise_for_status(self):
            pass

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, **kwargs):
            nonlocal captured_params
            captured_params = params or {}
            return MockResponse()

    async def mock_search_local(q, limit=15):
        return []

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: MockClient())
    monkeypatch.setattr(ScryfallService, "search_cards_local", mock_search_local)

    results = await ScryfallService.search_cards("Vivi")

    # Scryfall query must contain paper and non-digital flags
    assert "game:paper" in captured_params.get("q", "")
    assert "-is:digital" in captured_params.get("q", "")
    assert "-set_type:alchemy" in captured_params.get("q", "")

    # Output data must NOT include A-Vivi Ornitier
    card_names = [c["name"] for c in results["data"]]
    assert "Vivi Ornitier" in card_names
    assert "A-Vivi Ornitier" not in card_names


@pytest.mark.asyncio
async def test_scryfall_autocomplete_filters_arena_rebalances(monkeypatch):
    class MockResponse:
        status_code = 200

        def json(self):
            return {
                "data": [
                    "A-The One Ring",
                    "The One Ring",
                    "A-Orcish Bowmasters",
                    "Orcish Bowmasters",
                ]
            }

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, **kwargs):
            return MockResponse()

    async def mock_autocomplete_local(q, limit=10):
        return []

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: MockClient())
    monkeypatch.setattr(ScryfallService, "autocomplete_local", mock_autocomplete_local)

    suggestions = await ScryfallService.autocomplete_cards("The")
    assert "The One Ring" in suggestions
    assert "Orcish Bowmasters" in suggestions
    assert "A-The One Ring" not in suggestions
    assert "A-Orcish Bowmasters" not in suggestions


@pytest.mark.asyncio
async def test_get_card_named_rejects_a_prefix():
    result = await ScryfallService.get_card_named("A-Vivi Ornitier", exact=True)
    assert result is None
