import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
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


@pytest.mark.asyncio
async def test_card_details_resolves_printing_id_to_catalog_cache():
    """Detail dialog opens with a printing id; cache lives on the catalog row."""
    cached_details = {
        "id": "catalog-sol-ring",
        "name": "Sol Ring",
        "oracle_text_es": "Añade {C}.",
        "cache_version": 2,
    }
    printing = SimpleNamespace(id="printing-sol-ring", catalogId="catalog-sol-ring")
    catalog = SimpleNamespace(id="catalog-sol-ring", detailsEs=cached_details)

    find_unique_catalog = AsyncMock(side_effect=[None, catalog])
    find_unique_printing = AsyncMock(return_value=printing)
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=find_unique_catalog),
        cardprinting=SimpleNamespace(find_unique=find_unique_printing),
    )

    with (
        patch("src.services.scryfall_service.db", new=fake_db),
        patch.object(
            ScryfallService,
            "enrich_card_via_worker",
            new=AsyncMock(side_effect=AssertionError("must not block on worker")),
        ),
    ):
        result = await ScryfallService.get_card_details_es(
            card_id="printing-sol-ring", name="Sol Ring"
        )

    assert result["id"] == "catalog-sol-ring"
    find_unique_printing.assert_awaited_once_with(where={"id": "printing-sol-ring"})


@pytest.mark.asyncio
async def test_card_details_falls_back_to_name_when_id_not_in_catalog():
    cached_details = {
        "id": "catalog-sol-ring",
        "name": "Sol Ring",
        "cache_version": 2,
    }
    catalog = SimpleNamespace(id="catalog-sol-ring", detailsEs=cached_details)
    find_unique_catalog = AsyncMock(side_effect=[None, catalog])
    find_unique_printing = AsyncMock(return_value=None)
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=find_unique_catalog),
        cardprinting=SimpleNamespace(find_unique=find_unique_printing),
    )

    with (
        patch("src.services.scryfall_service.db", new=fake_db),
        patch.object(
            ScryfallService,
            "enrich_card_via_worker",
            new=AsyncMock(side_effect=AssertionError("must not block on worker")),
        ),
    ):
        result = await ScryfallService.get_card_details_es(
            card_id="unknown-printing", name="Sol Ring"
        )

    assert result["id"] == "catalog-sol-ring"
    assert find_unique_catalog.await_count == 2


@pytest.mark.asyncio
async def test_card_details_returns_stale_cache_without_waiting_for_worker():
    """Stale detailsEs must not block the UI on a 90s enrich-card call."""
    stale = {
        "id": "catalog-sol-ring",
        "name": "Sol Ring",
        "oracle_text_es": "Add {C}.",
        # No cache_version 2 → previously treated as miss and blocked on worker
    }

    async def slow_enrich(*_args, **_kwargs):
        await asyncio.sleep(5)
        return "catalog-sol-ring"

    with (
        patch.object(
            ScryfallService,
            "_get_cached_card_details",
            new=AsyncMock(return_value=stale),
        ),
        patch.object(
            ScryfallService,
            "enrich_card_via_worker",
            new=AsyncMock(side_effect=slow_enrich),
        ) as enrich,
    ):
        started = asyncio.get_event_loop().time()
        result = await ScryfallService.get_card_details_es(
            card_id="catalog-sol-ring", name="Sol Ring"
        )
        elapsed = asyncio.get_event_loop().time() - started

    assert result["name"] == "Sol Ring"
    assert elapsed < 1.0
    enrich.assert_not_awaited()


@pytest.mark.asyncio
async def test_card_details_cache_read_returns_json_from_catalog():
    cached_details = {"id": "cached-card", "name": "Carta en caché"}
    catalog = SimpleNamespace(detailsEs=cached_details)

    find_unique = AsyncMock(return_value=catalog)
    fake_db = SimpleNamespace(cardcatalog=SimpleNamespace(find_unique=find_unique))
    with patch("src.services.scryfall_service.db", new=fake_db):
        result = await ScryfallService._get_cached_card_details("cached-card", None)

    assert result == cached_details
    find_unique.assert_awaited_once_with(where={"id": "cached-card"})


@pytest.mark.asyncio
async def test_card_details_cache_serializes_the_full_response_as_json():
    upsert = AsyncMock()
    fake_db = SimpleNamespace(cardcatalog=SimpleNamespace(upsert=upsert))
    details = {
        "id": "cached-card",
        "name": "Carta en caché",
        "mana_cost": "{U}",
        "image_uris": {"normal": "https://example.test/card.jpg"},
        "legalities": [{"format": "commander", "status_es": "Legal"}],
    }

    with patch("src.services.scryfall_service.db", new=fake_db):
        await ScryfallService._cache_card_details(details)

    payload = upsert.await_args.kwargs["data"]
    assert payload["update"]["detailsEs"].data == details
    assert payload["create"]["oracleTextEs"] is None


@pytest.mark.asyncio
async def test_enrich_card_via_worker_returns_catalog_id_on_success():
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "enriched", "card": {"id": "catalog-sol-ring"}},
    )
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.scryfall_service.httpx.AsyncClient", return_value=fake_ctx):
        result = await ScryfallService.enrich_card_via_worker(card_id=None, name="Sol Ring")

    assert result == "catalog-sol-ring"
    fake_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_enrich_card_via_worker_returns_none_on_worker_error():
    fake_response = SimpleNamespace(status_code=500, json=lambda: {})
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.scryfall_service.httpx.AsyncClient", return_value=fake_ctx):
        result = await ScryfallService.enrich_card_via_worker(card_id=None, name="Sol Ring")

    assert result is None


@pytest.mark.asyncio
async def test_card_details_missing_from_db_delegates_hydration_to_worker():
    hydrated_details = {
        "id": "catalog-sol-ring",
        "name": "Sol Ring",
        "name_es": "Sol Ring",
        "random_legality": {"commander": "legal", "standard": "banned"},
        "legalities": {"commander": "legal"},
    }
    finalized = {
        "id": "catalog-sol-ring",
        "legalities": [
            {
                "format": "commander",
                "format_name": "Commander",
                "status": "legal",
                "status_es": "legal",
            }
        ],
    }

    with (
        patch.object(
            ScryfallService,
            "_get_cached_card_details",
            new=AsyncMock(side_effect=[None, hydrated_details]),
        ) as get_cached,
        patch.object(
            ScryfallService,
            "enrich_card_via_worker",
            new=AsyncMock(return_value="catalog-sol-ring"),
        ) as enrich,
    ):
        result = await ScryfallService.get_card_details_es(
            card_id="missing-catalog-id", name=None
        )

    enrich.assert_awaited_once_with("missing-catalog-id", None)
    assert get_cached.await_count == 2
    assert result["id"] == "catalog-sol-ring"
    assert result["legalities"][0]["format"] == "commander"


@pytest.mark.asyncio
async def test_card_details_falls_back_to_scryfall_when_worker_unreachable():
    cached_hits = [None, None]

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "remote-1", "name": "Sol Ring"}

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=FakeResponse())
    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(
            ScryfallService,
            "_get_cached_card_details",
            new=AsyncMock(side_effect=cached_hits),
        ),
        patch.object(
            ScryfallService,
            "enrich_card_via_worker",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.services.scryfall_service.httpx.AsyncClient",
            return_value=fake_ctx,
        ),
    ):
        result = await ScryfallService.get_card_details_es(name="Sol Ring")

    assert result["id"] == "remote-1"


@pytest.mark.asyncio
async def test_card_endpoint_joins_printings_by_catalog_id():
    mock_details = {
        "id": "catalog-1",
        "name": "Sol Ring",
        "name_es": "Sol Ring",
    }
    printing = SimpleNamespace(
        id="printing-1",
        setId="set-c21",
        set=SimpleNamespace(id="set-c21", code="c21", name="Commander 2021"),
        collectorNumber="263",
        rarity="uncommon",
        imageUri="https://images.test/normal.webp",
        imageUriSmall="https://images.test/small.webp",
        imageUriLarge="https://images.test/large.webp",
        priceEur=1.5,
        priceEurFoil=None,
        priceUsd=None,
        priceUsdFoil=None,
        priceCardmarketTrend=1.5,
        priceCardmarketMin=1.0,
        priceCardmarketMax=2.0,
    )
    printing.releasedAt = SimpleNamespace(isoformat=lambda: "2021-04-23T00:00:00+00:00")
    ruling = SimpleNamespace(
        rulingDate=SimpleNamespace(isoformat=lambda: "2020-09-25T00:00:00+00:00"),
        text="Tapped for {1}.",
        source="wotc",
    )
    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(return_value=[printing])
    mock_db.cardruling.find_many = AsyncMock(return_value=[ruling])

    with (
        patch.object(
            ScryfallService,
            "get_card_details_es",
            new=AsyncMock(return_value=mock_details),
        ),
        patch("src.routers.scryfall.db", mock_db),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/scryfall/card?name=Sol+Ring")

        assert res.status_code == 200
        mock_db.cardprinting.find_many.assert_awaited_once_with(
            where={
                "OR": [
                    {"catalogId": "catalog-1"},
                    {"id": "catalog-1"},
                ]
            },
            order={"releasedAt": "desc"},
            include={"set": True},
        )
    payload_printing = res.json()["printings"][0]
    assert payload_printing["id"] == "printing-1"
    assert payload_printing["name_es"] == "Sol Ring"
    assert payload_printing["image_uri_large"] == "https://images.test/large.webp"
    assert res.json()["image_uris"] == {
        "small": "https://images.test/small.webp",
        "normal": "https://images.test/normal.webp",
        "large": "https://images.test/large.webp",
        "art_crop": "https://images.test/normal.webp",
    }
    assert payload_printing["released_at"] == "2021-04-23T00:00:00+00:00"
    assert payload_printing["trend"] == 1.5
    assert payload_printing["min"] == 1.0
    assert payload_printing["max"] == 2.0
    assert res.json()["rulings"][0]["text"] == "Tapped for {1}."
    assert res.json()["prices"]["eur"] == 1.5
    assert res.json()["prices"]["usd"] is None
