import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services.scryfall_service import ScryfallService


def _catalog_record(**overrides):
    base = {
        "id": "catalog-1",
        "name": "Humongous Fungus // Humongous Fungus",
        "normalizedName": "humongous fungus",
        "manaCost": None,
        "typeLine": "Card // Card",
        "imageUri": "http://images.test/art.jpg",
        "setCode": "atmt",
        "collectorNumber": None,
        "detailsEs": None,
        "nameEs": None,
        "typeLineEs": None,
        "oracleTextEs": None,
        "flavorTextEs": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_get_catalog_card_rejects_art_series_record():
    """art_series/memorabilia entries in the local catalog never resolve."""
    record = _catalog_record()
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=AsyncMock(return_value=record))
    )
    with patch("src.services.scryfall_service.db", fake_db):
        result = await ScryfallService.get_catalog_card(
            "Humongous Fungus // Humongous Fungus"
        )

    assert result is None


@pytest.mark.asyncio
async def test_get_catalog_card_rejects_token_record():
    record = _catalog_record(name="Elf Token", typeLine="Token Creature — Elf", setCode="ttok")
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=AsyncMock(return_value=record))
    )
    with patch("src.services.scryfall_service.db", fake_db):
        result = await ScryfallService.get_catalog_card("Elf Token")

    assert result is None


@pytest.mark.asyncio
async def test_get_catalog_card_keeps_legitimate_split_card():
    """Real split cards like 'Fight // Flight' must remain resolvable."""
    record = _catalog_record(
        name="Fight // Flight",
        normalizedName="fight",
        typeLine="Instant",
        setCode="SLD",
    )
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(find_unique=AsyncMock(return_value=record))
    )
    with patch("src.services.scryfall_service.db", fake_db):
        result = await ScryfallService.get_catalog_card("Fight // Flight")

    assert result is not None
    assert result["name"] == "Fight // Flight"


@pytest.mark.asyncio
async def test_get_or_resolve_catalog_card_uses_exact_lookup():
    """Misspelled/nonexistent names must NOT be fuzzy-resolved to another card."""
    with (
        patch.object(
            ScryfallService, "get_catalog_card",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            ScryfallService, "get_card_named",
            new=AsyncMock(return_value=None),
        ) as named,
    ):
        result = await ScryfallService.get_or_resolve_catalog_card("Slash Clone")

    assert result is None
    named.assert_awaited_once_with("Slash Clone", exact=True)


@pytest.mark.asyncio
async def test_get_or_resolve_catalog_card_never_writes_catalog_for_unknown():
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(upsert=AsyncMock())
    )
    with (
        patch.object(
            ScryfallService, "get_catalog_card",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            ScryfallService, "get_card_named",
            new=AsyncMock(side_effect=lambda name, exact=False: None),
        ),
        patch("src.services.scryfall_service.db", fake_db),
    ):
        result = await ScryfallService.get_or_resolve_catalog_card("Slash Clone")

    assert result is None
    fake_db.cardcatalog.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_resolve_catalog_card_saves_exactly_resolved_card():
    card_data = {
        "id": "uuid-sol-ring",
        "name": "Sol Ring",
        "type_line": "Artifact",
        "image_uris": {
            "normal": "https://cards.scryfall.io/normal/front/1/2/solring.jpg",
        },
        "set": "CLR",
        "collector_number": "1",
    }
    created = SimpleNamespace(
        id="uuid-sol-ring",
        name="Sol Ring",
        normalizedName="sol ring",
        manaCost="{1}",
        typeLine="Artifact",
        imageUri=None,
        setCode="CLR",
        collectorNumber="1",
    )
    fake_db = SimpleNamespace(
        cardcatalog=SimpleNamespace(
            find_first=AsyncMock(return_value=None),
            upsert=AsyncMock(return_value=created),
        )
    )
    with (
        patch.object(
            ScryfallService, "get_catalog_card",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            ScryfallService, "get_card_named",
            new=AsyncMock(return_value=card_data),
        ) as named,
        patch("src.services.scryfall_service.db", fake_db),
    ):
        result = await ScryfallService.get_or_resolve_catalog_card("Sol Ring")

    assert result["name"] == "Sol Ring"
    named.assert_awaited_once_with("Sol Ring", exact=True)
    fake_db.cardcatalog.upsert.assert_awaited_once()