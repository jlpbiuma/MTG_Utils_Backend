import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from src.main import app
from src.services.scryfall_service import ScryfallService

def test_type_line_translation():
    assert ScryfallService._translate_type_line("Creature — Elf Druid") == "Criatura — Elfo Druida"
    assert ScryfallService._translate_type_line("Instant") == "Instantáneo"
    assert ScryfallService._translate_type_line("Basic Land — Forest") == "Básica Tierra — Bosque"
    assert ScryfallService._translate_type_line("Legendary Creature — Dragon") == "Legendario/a Criatura — Dragón"

@pytest.mark.asyncio
async def test_scryfall_card_endpoint_mocked():
    mock_data = {
        "id": "mock-1",
        "name": "Lightning Bolt",
        "name_es": "Relámpago",
        "type_line": "Instant",
        "type_line_es": "Instantáneo",
        "oracle_text_es": "El Relámpago hace 3 puntos de daño a cualquier objetivo.",
        "rarity_es": "Infrecuente",
        "has_spanish_print": True,
        "legalities": [{"format": "commander", "status": "legal", "status_es": "Legal"}],
    }

    with patch.object(ScryfallService, "get_card_details_es", return_value=mock_data):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/scryfall/card?name=Lightning+Bolt")
            assert res.status_code == 200
            json_data = res.json()
            assert json_data["name_es"] == "Relámpago"
            assert json_data["type_line_es"] == "Instantáneo"
            assert json_data["rarity_es"] == "Infrecuente"
