"""Regression budgets: independent reads overlap and invisible history is not read."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from src.services.simulated_collection_service import SimulatedCollectionService
from src.services.scryfall_service import ScryfallService


@pytest.mark.asyncio
async def test_offer_dispatches_user_queries_together_without_loading_hidden_history():
    started = set()
    ready = asyncio.Event()
    def read(label):
        async def query(**kwargs):
            assert kwargs['where']['userId'] == 'owner'
            started.add(label)
            if len(started) == 3:
                ready.set()
            await ready.wait()  # A sequential implementation deadlocks here.
            return []
        return AsyncMock(side_effect=query)
    db = NS(deck=NS(find_many=read('decks')), collectioncard=NS(find_many=read('collection')),
            wantcard=NS(find_many=read('wants')), cardprinting=NS(find_many=AsyncMock(return_value=[])))
    history = AsyncMock(side_effect=AssertionError('Hidden history must not run'))
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.resolve_offer_names', AsyncMock(return_value={})), patch('src.services.simulated_collection_service.get_history_statistics', history):
        analysis = await asyncio.wait_for(SimulatedCollectionService.analyze_raw_text('owner', '1 Relámpago', include_history=False), timeout=1)
    assert started == {'decks', 'collection', 'wants'}
    for table in (db.deck, db.collectioncard, db.wantcard, db.cardprinting):
        assert table.find_many.await_count == 1
    assert analysis.totalCards == 1 and analysis.historyStatistics is None
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_printing_detail_uses_two_queries_and_never_waits_for_enrichment():
    details = {'id': 'catalog', 'name': 'Lightning Bolt', 'name_es': 'Relámpago'}
    catalog = NS(id='catalog', detailsEs=details)
    db = NS(cardcatalog=NS(find_unique=AsyncMock(return_value=None)),
            cardprinting=NS(find_unique=AsyncMock(return_value=NS(catalogId='catalog', catalog=catalog))))
    worker = AsyncMock(side_effect=AssertionError('Warm details must not enrich'))
    with patch('src.services.scryfall_service.db', db), patch.object(ScryfallService, 'enrich_card_via_worker', worker):
        response = await ScryfallService.get_card_details_es('printing', 'Lightning Bolt')
    assert response['name_es'] == 'Relámpago' and response['_catalog_id'] == 'catalog'
    db.cardcatalog.find_unique.assert_awaited_once_with(where={'id': 'printing'})
    db.cardprinting.find_unique.assert_awaited_once_with(where={'id': 'printing'}, include={'catalog': True})
    worker.assert_not_awaited()
    assert details == {'id': 'catalog', 'name': 'Lightning Bolt', 'name_es': 'Relámpago'}


@pytest.mark.asyncio
async def test_worker_detail_wait_has_a_three_second_budget():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=NS(status_code=200, json=lambda: {'status': 'enriched', 'card': {'id': 'catalog'}}))
    with patch('src.services.scryfall_service.httpx.AsyncClient', return_value=client) as factory:
        assert await ScryfallService.enrich_card_via_worker('printing', None) == 'catalog'
    factory.assert_called_once_with(timeout=3.0)


@pytest.mark.asyncio
async def test_detail_endpoint_reuses_resolved_catalog_without_a_duplicate_lookup():
    from src.routers import scryfall
    db = NS(cardcatalog=NS(find_unique=AsyncMock(side_effect=AssertionError('Duplicate catalog lookup'))),
            cardprinting=NS(find_many=AsyncMock(return_value=[])), cardruling=NS(find_many=AsyncMock(return_value=[])))
    with patch.object(scryfall, 'db', db), patch.object(ScryfallService, 'get_card_details_es', AsyncMock(return_value={'id': 'catalog', '_catalog_id': 'catalog', 'name': 'Lightning Bolt'})):
        response = await scryfall.get_card_details(id='printing', name='Lightning Bolt', set=None, collector_number=None)
    assert '_catalog_id' not in response
    db.cardcatalog.find_unique.assert_not_awaited()
    assert db.cardprinting.find_many.await_count == db.cardruling.find_many.await_count == 1


@pytest.mark.asyncio
async def test_slow_enrichment_releases_the_detail_request_for_its_fallback():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock()
    async def expired(awaitable, *, timeout):
        assert timeout == 3.0
        awaitable.close()
        raise asyncio.TimeoutError
    with patch('src.services.scryfall_service.httpx.AsyncClient', return_value=client), patch('src.services.scryfall_service.asyncio.wait_for', expired):
        assert await ScryfallService.enrich_card_via_worker('printing', None) is None


@pytest.mark.asyncio
async def test_cardmarket_summaries_do_not_query_histories_but_the_detail_still_does():
    from datetime import datetime, timezone
    from src.schemas.price_statistics import PriceHistoryStatistics
    coll = NS(id='offer', userId='owner', name='Lote', description=None, rawText='1 Relámpago', origin='cardmarket', cards=[],
              createdAt=datetime.now(timezone.utc), updatedAt=datetime.now(timezone.utc))
    db = NS(simulatedcollection=NS(find_many=AsyncMock(return_value=[coll]), find_unique=AsyncMock(return_value=coll)),
            deck=NS(find_many=AsyncMock(return_value=[])), collectioncard=NS(find_many=AsyncMock(return_value=[])),
            wantcard=NS(find_many=AsyncMock(return_value=[])), cardprinting=NS(find_many=AsyncMock(return_value=[])))
    history = AsyncMock(return_value=({}, PriceHistoryStatistics()))
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.resolve_offer_names', AsyncMock(return_value={})), patch('src.services.simulated_collection_service.get_history_statistics', history):
        summaries = await SimulatedCollectionService.list_simulated_collections('owner')
        assert len(summaries) == 1 and summaries[0].totalCards == 1
        history.assert_not_awaited()
        detail = await SimulatedCollectionService.get_simulated_collection('owner', 'offer')
        assert detail.origin == 'cardmarket' and detail.totalCards == 1
        history.assert_awaited_once()


@pytest.mark.asyncio
async def test_many_saved_offers_share_one_request_scoped_catalog_and_inventory_snapshot():
    from datetime import datetime, timezone
    rows=[NS(id=f'offer-{n}',userId='owner',name=f'Lote {n}',description=None,
             rawText='1 Relámpago',origin='other',cards=[],createdAt=datetime.now(timezone.utc),
             updatedAt=datetime.now(timezone.utc)) for n in range(12)]
    database=NS(simulatedcollection=NS(find_many=AsyncMock(return_value=rows)),
                deck=NS(find_many=AsyncMock(return_value=[])),collectioncard=NS(find_many=AsyncMock(return_value=[])),
                wantcard=NS(find_many=AsyncMock(return_value=[])),cardprinting=NS(find_many=AsyncMock(return_value=[])))
    with patch('src.services.simulated_collection_service.db',database), \
         patch('src.services.simulated_collection_service.resolve_offer_names',AsyncMock(return_value={})) as names, \
         patch('src.services.simulated_collection_service.upcoming_reprints_by_name',AsyncMock(return_value={})) as future:
        summaries=await SimulatedCollectionService.list_simulated_collections('owner')
        assert len(summaries)==12 and all(item.totalCards==1 for item in summaries)
        assert names.await_count==future.await_count==1
        for table in (database.deck,database.collectioncard,database.wantcard,database.cardprinting):
            assert table.find_many.await_count==1
        # A later request must refresh user and catalog state, rather than using stale data.
        await SimulatedCollectionService.list_simulated_collections('other-user')
        assert names.await_count==2
        database.collectioncard.find_many.assert_awaited_with(where={'userId':'other-user'})
