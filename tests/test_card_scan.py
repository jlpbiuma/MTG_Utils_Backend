"""Tests for card scan resolve (OCR mocked)."""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.card_scan_service import CardScanService


@pytest.mark.asyncio
async def test_resolve_from_title_exact_catalog_and_cheapest_printing():
    catalog = {
        "id": "cat-1",
        "name": "Sol Ring",
        "normalizedName": "sol ring",
        "manaCost": "{1}",
        "typeLine": "Artifact",
        "imageUri": "https://example.com/sol.jpg",
    }
    printing = type(
        "P",
        (),
        {
            "id": "print-1",
            "catalogId": "cat-1",
            "collectorNumber": "266",
            "imageUri": "https://example.com/sol-c21.jpg",
            "priceEur": 1.5,
            "priceCardmarketTrend": 1.2,
            "setCode": "c21",
        },
    )()

    with (
        patch(
            "src.services.card_scan_service.ScryfallService.get_catalog_card",
            new=AsyncMock(return_value=catalog),
        ),
        patch(
            "src.services.card_scan_service.CardScanService._cheapest_printing_with_set",
            new=AsyncMock(return_value=printing),
        ),
    ):
        result = await CardScanService.resolve_from_title("Sol Ring")

    assert result.ocrTitle == "Sol Ring"
    assert result.matchScore == 0
    assert result.catalog.name == "Sol Ring"
    assert result.printing is not None
    assert result.printing.setCode == "c21"
    assert result.printing.priceCardmarketTrend == 1.2


@pytest.mark.asyncio
async def test_resolve_from_title_uses_local_search_when_exact_misses():
    local_hits = [
        {
            "id": "cat-2",
            "name": "Sabin, Master Monk",
            "normalizedName": "sabin, master monk",
            "manaCost": "{4}{R}",
            "typeLine": "Legendary Creature — Human Noble Monk",
            "imageUri": "https://example.com/sabin.jpg",
            "setCode": "fic",
            "collectorNumber": "57",
        }
    ]

    with (
        patch(
            "src.services.card_scan_service.ScryfallService.get_catalog_card",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.services.card_scan_service.ScryfallService.search_cards_local",
            new=AsyncMock(return_value=local_hits),
        ),
        patch(
            "src.services.card_scan_service.CardScanService._cheapest_printing_with_set",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await CardScanService.resolve_from_title("Sabin, Master Monk")

    assert result.catalog.name == "Sabin, Master Monk"
    assert result.matchScore == 0
    assert result.printing is None
    assert result.alternatives


@pytest.mark.asyncio
async def test_scan_image_calls_ocr_then_resolve():
    expected = object()
    with (
        patch(
            "src.services.card_scan_service.CardScanService.ocr_card_title",
            new=AsyncMock(return_value="Lightning Bolt"),
        ) as ocr,
        patch(
            "src.services.card_scan_service.CardScanService.resolve_from_title",
            new=AsyncMock(return_value=expected),
        ) as resolve,
    ):
        out = await CardScanService.scan_image(b"fake-jpeg", filename="x.jpg")

    assert out is expected
    ocr.assert_awaited_once()
    resolve.assert_awaited_once_with("Lightning Bolt")
