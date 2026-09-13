import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.enrichment_service import (
    _priority_names,
    enqueue_priority_card_names,
    enrich_card_name,
    run_priority_enrichment,
)


@pytest.fixture(autouse=True)
def clear_priority_queue():
    yield
    _priority_names.clear()


def make_deck_card(id="dc-1", deck_id="deck-1", name="Sol Ring", sideboard=False):
    card = MagicMock()
    card.id = id
    card.deckId = deck_id
    card.cardName = name
    card.isSideboard = sideboard
    card.quantity = 2
    return card


def make_collection_card(id="cc-1", user_id="user-1", name="Sol Ring"):
    card = MagicMock()
    card.id = id
    card.userId = user_id
    card.cardName = name
    card.quantity = 1
    return card


@pytest.mark.asyncio
async def test_enqueue_and_drain_priority_names():
    await enqueue_priority_card_names(["Sol Ring", "  Lightning Bolt ", "", None])
    assert _priority_names == {"Sol Ring", "Lightning Bolt"}


@pytest.mark.asyncio
async def test_priority_enrichment_resolves_pending_rows():
    mock_db = MagicMock()
    mock_db.deckcard.find_many = AsyncMock(return_value=[make_deck_card()])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_unique = AsyncMock(return_value=None)
    mock_db.deckcard.update = AsyncMock()
    mock_db.deckcard.delete = AsyncMock()

    cat = {
        "id": "scry-sol-ring",
        "manaCost": "{1}",
        "typeLine": "Artifact",
        "imageUri": "https://img/sol.png",
    }

    with patch("src.services.enrichment_service.db", mock_db), \
         patch(
             "src.services.enrichment_service.ScryfallService.get_or_resolve_catalog_card",
             new_callable=AsyncMock,
         ) as mock_resolve:
        mock_resolve.return_value = cat

        ok = await enrich_card_name("Sol Ring")
        assert ok is True
        mock_resolve.assert_awaited_once_with("Sol Ring")

        mock_db.deckcard.find_many.assert_awaited_once()
        mock_db.deckcard.update.assert_awaited_once()
        update_data = mock_db.deckcard.update.call_args[1]["data"]
        assert update_data["cardScryfallId"] == "scry-sol-ring"
        assert update_data["typeLine"] == "Artifact"
        assert update_data["imageUri"] == "https://img/sol.png"


@pytest.mark.asyncio
async def test_priority_enrichment_merges_duplicate_rows():
    pending = make_deck_card(id="dc-pending", deck_id="deck-1", name="Sol Ring")
    mock_db = MagicMock()
    mock_db.deckcard.find_many = AsyncMock(return_value=[pending])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_unique = AsyncMock(return_value=MagicMock(id="dc-existing", quantity=1))
    mock_db.deckcard.update = AsyncMock()
    mock_db.deckcard.delete = AsyncMock()

    cat = {"id": "scry-sol-ring", "manaCost": "{1}", "typeLine": "Artifact", "imageUri": "u"}

    with patch("src.services.enrichment_service.db", mock_db), \
         patch(
             "src.services.enrichment_service.ScryfallService.get_or_resolve_catalog_card",
             new_callable=AsyncMock,
             return_value=cat,
         ):
        ok = await enrich_card_name("Sol Ring")
        assert ok is True
        mock_db.deckcard.delete.assert_awaited_once_with(where={"id": "dc-pending"})
        merged = mock_db.deckcard.update.call_args[1]["data"]
        assert merged["quantity"] == 3


@pytest.mark.asyncio
async def test_run_priority_enrichment_processes_queued_names():
    mock_db = MagicMock()
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    cat = {"id": "scry-x", "manaCost": None, "typeLine": "Artifact", "imageUri": None}

    with patch("src.services.enrichment_service.db", mock_db), \
         patch(
             "src.services.enrichment_service.ScryfallService.get_or_resolve_catalog_card",
             new_callable=AsyncMock,
             return_value=cat,
         ), \
         patch("src.services.enrichment_service.asyncio.sleep", new_callable=AsyncMock):
        await enqueue_priority_card_names(["Black Lotus", "Sol Ring"])

        result = await run_priority_enrichment(max_names=10)
        assert result["prioritizedNames"] == 2
        assert result["resolvedCards"] == 2
        assert _priority_names == set()


@pytest.mark.asyncio
async def test_priority_enrichment_reports_unresolved():
    mock_db = MagicMock()
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    with patch("src.services.enrichment_service.db", mock_db), \
         patch(
             "src.services.enrichment_service.ScryfallService.get_or_resolve_catalog_card",
             new_callable=AsyncMock,
             return_value=None,
         ), \
         patch("src.services.enrichment_service.asyncio.sleep", new_callable=AsyncMock):
        await enqueue_priority_card_names(["Nonexistent Made Up Card"])
        result = await run_priority_enrichment(max_names=10)
        assert result["prioritizedNames"] == 1
        assert result["resolvedCards"] == 0


@pytest.mark.asyncio
async def test_worker_enrich_trigger_priority_endpoint_fire_and_forget():
    from httpx import AsyncClient, ASGITransport
    from src.main import app

    with patch(
        "src.routers.worker.trigger_async_priority_enrichment",
        new=MagicMock(),
    ) as trigger:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/worker/trigger-priority",
                json={"card_names": ["Sol Ring", "Lightning Bolt"]},
            )
            assert res.status_code == 200
            assert res.json() == {"status": "success", "prioritized": 2}
            trigger.assert_called_once_with(["Sol Ring", "Lightning Bolt"])


@pytest.mark.asyncio
async def test_worker_enrich_with_priority_runs_async():
    from httpx import AsyncClient, ASGITransport
    from src.main import app

    with patch(
        "src.routers.worker.trigger_async_priority_enrichment",
        new=MagicMock(),
    ) as trigger:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/worker/enrich",
                json={"card_names": ["Sol Ring"], "priority": True},
            )
            assert res.status_code == 200
            assert res.json() == {"status": "success", "prioritized": 1}
            trigger.assert_called_once_with(["Sol Ring"])