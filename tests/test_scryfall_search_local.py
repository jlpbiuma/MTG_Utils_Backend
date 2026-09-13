import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.scryfall_service import ScryfallService

def make_catalog_record(id="c1", name="Sol Ring", image_uri="https://img/sol.png"):
    rec = MagicMock()
    rec.id = id
    rec.name = name
    rec.imageUri = image_uri
    rec.manaCost = "{1}"
    rec.typeLine = "Artifact"
    rec.setCode = "M20"
    rec.collectorNumber = "252"
    return rec

def make_fake_scryfall_client(response):
    """Builds an async context manager that returns `response` from get()."""
    client_instance = MagicMock()
    client_instance.get = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    client_factory = MagicMock(return_value=client_instance)
    return client_factory

@pytest.mark.asyncio
async def test_search_cards_empty_query():
    result = await ScryfallService.search_cards("   ")
    assert result["total_cards"] == 0
    assert result["data"] == []

@pytest.mark.asyncio
async def test_search_cards_returns_local_results_without_scryfall():
    mock_db = MagicMock()
    mock_db.cardcatalog.find_many = AsyncMock(return_value=[make_catalog_record()])
    mock_db.cardprinting.find_many = AsyncMock(return_value=[MagicMock(
        catalogId="c1",
        imageUri="https://img/normal.webp",
        imageUriSmall="https://img/small.webp",
        imageUriLarge="https://img/large.webp",
    )])

    httpx_mock = AsyncMock()
    with patch("src.services.scryfall_service.db", mock_db), \
         patch("src.services.scryfall_service.httpx.AsyncClient", httpx_mock):
        result = await ScryfallService.search_cards("Sol")
        # Local results returned
        assert result["source"] == "local"
        assert result["total_cards"] == 1
        assert result["data"][0]["name"] == "Sol Ring"
        assert result["data"][0]["id"] == "c1"
        assert result["data"][0]["image_uris"] == {
            "small": "https://img/small.webp",
            "normal": "https://img/normal.webp",
            "large": "https://img/large.webp",
            "art_crop": "https://img/normal.webp",
        }
        # Scryfall must not be called when DB has matches
        httpx_mock.assert_not_called()

@pytest.mark.asyncio
async def test_search_cards_falls_back_to_scryfall_when_db_empty():
    mock_db = MagicMock()
    mock_db.cardcatalog.find_many = AsyncMock(return_value=[])

    fake_card = {
        "id": "scry-1",
        "name": "Sol Ring",
        "mana_cost": "{1}",
        "type_line": "Artifact",
        "image_uris": {
            "normal": "https://cards.scryfall.io/normal/front/1/2/card.jpg",
        },
    }
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "total_cards": 1,
        "has_more": False,
        "data": [fake_card],
    }
    client_factory = make_fake_scryfall_client(fake_response)

    with patch("src.services.scryfall_service.db", mock_db), \
         patch("src.services.scryfall_service.httpx.AsyncClient", client_factory):
        result = await ScryfallService.search_cards("Sol")
        assert result["total_cards"] == 1
        assert result["data"][0]["name"] == "Sol Ring"
        assert result["data"][0]["image_uris"]["normal"] is None
        client_factory.return_value.get.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_cards_empty_when_db_and_scryfall_miss():
    mock_db = MagicMock()
    mock_db.cardcatalog.find_many = AsyncMock(return_value=[])

    fake_response = MagicMock()
    fake_response.status_code = 404
    client_factory = make_fake_scryfall_client(fake_response)

    with patch("src.services.scryfall_service.db", mock_db), \
         patch("src.services.scryfall_service.httpx.AsyncClient", client_factory):
        result = await ScryfallService.search_cards("NonExistentCardXYZ")
        assert result["total_cards"] == 0
        assert result["data"] == []

@pytest.mark.asyncio
async def test_autocomplete_cards_local_first():
    mock_db = MagicMock()
    record_a = make_catalog_record(id="c-a", name="Lightning Bolt")
    record_b = make_catalog_record(id="c-b", name="Lightning Helix")
    mock_db.cardcatalog.find_many = AsyncMock(return_value=[record_a, record_b])

    httpx_mock = AsyncMock()
    with patch("src.services.scryfall_service.db", mock_db), \
         patch("src.services.scryfall_service.httpx.AsyncClient", httpx_mock):
        names = await ScryfallService.autocomplete_cards("Light")
        assert names == ["Lightning Bolt", "Lightning Helix"]
        httpx_mock.assert_not_called()
