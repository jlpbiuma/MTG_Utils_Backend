import json
from pathlib import Path
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import pytest
from src.services.upcoming_reprints import upcoming_reprints_by_name
from src.services.simulated_collection_service import SimulatedCollectionService
from src.services.scryfall_service import ScryfallService
from src.routers import scryfall

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/reprints/foundations-commander.json').read_text())


def printing(name, code, released, kind='expansion', digital=False):
    return NS(id=f'{name}-{code}', catalog=NS(name=name, normalizedName=name.lower()),
              set=NS(code=code, name='Foundations Commander' if code == 'fdc' else code, releasedAt=released, setType=kind, isDigital=digital),
              releasedAt='2099-01-01')  # Set date overrides stale printing metadata.


@pytest.mark.asyncio
async def test_confirmed_foundations_reprints_warn_before_release_and_expire_on_release():
    prints = [p for card in FIXTURE['cards'] for p in [printing(card['name'], 'old', card['old_release']), printing(card['name'], 'fdc', FIXTURE['set']['released_at'], 'commander')]]
    prints.append(printing('Taurean Mauler', 'fdc', '2026-10-02', 'commander'))
    db = NS(query_raw=AsyncMock(return_value=[]))
    names = [card['name'] for card in FIXTURE['cards']]
    upcoming = await upcoming_reprints_by_name(names, prints, db, today=date(2026, 10, 1))
    for name in names:
        assert upcoming[name.lower()] == [{'setCode': 'fdc', 'setName': 'Foundations Commander', 'releaseDate': '2026-10-02'}]
    assert await upcoming_reprints_by_name(names, prints, db, today=date(2026, 10, 2)) == {}
    assert await upcoming_reprints_by_name(names, prints, db, today=date(2026, 10, 4)) == {}


@pytest.mark.asyncio
async def test_ignores_missing_dates_nonpaper_sets_and_first_prints():
    prints = [printing('A', 'old', '2020-01-01'), printing('A', 'art', '2027-01-01', 'art_series'),
              printing('A', 'arena', '2027-01-01', digital=True), printing('A', 'token', '2027-01-01', 'token'),
              printing('A', 'unknown', None), printing('Brand new', 'new', '2027-01-01')]
    # The unknown set also has no trustworthy printing date.
    prints[-2].releasedAt = None
    assert await upcoming_reprints_by_name(['A', 'Brand new'], prints, NS(query_raw=AsyncMock(return_value=[])), today=date(2026, 10, 1)) == {}


@pytest.mark.asyncio
async def test_bulk_previews_need_no_downloaded_printings_and_use_one_indexed_batch():
    rows = [dict(name_key=card['name'].lower(), set_code='fdc', set_name='Foundations Commander', release_date='2026-10-02') for card in FIXTURE['cards']]
    db = NS(query_raw=AsyncMock(return_value=rows + rows))
    result = await upcoming_reprints_by_name([card['name'] for card in FIXTURE['cards']], [], db, today=date(2026, 10, 1))
    assert len(result) == 2 and all(len(reprints) == 1 for reprints in result.values())
    db.query_raw.assert_awaited_once()
    sql, names, today, corrections = db.query_raw.await_args.args
    assert json.loads(corrections)["fdc"] == "2026-10-02"
    assert set(json.loads(names)) == {'taurean mauler', 'hulking raptor'}
    assert 'b.generation=snapshot.generation' in sql and 'b.name_key IN' in sql
    assert "b.payload->>'reprint' = 'true'" in sql and "NOT s.is_digital" in sql


@pytest.mark.asyncio
async def test_bulk_outage_keeps_local_warnings_and_card_detail_contract():
    prints = [printing('Taurean Mauler', 'old', '2008-02-01'), printing('Taurean Mauler', 'fdc', '2026-10-02')]
    db = NS(query_raw=AsyncMock(side_effect=RuntimeError('No snapshot')))
    assert 'taurean mauler' in await upcoming_reprints_by_name(['Taurean Mauler'], prints, db, today=date(2026, 10, 1))


@pytest.mark.asyncio
async def test_offer_analysis_and_detail_include_the_same_warning_without_extra_print_queries():
    notice = {'setCode': 'fdc', 'setName': 'Foundations Commander', 'releaseDate': '2026-10-02'}
    db = NS(deck=NS(find_many=AsyncMock(return_value=[])), collectioncard=NS(find_many=AsyncMock(return_value=[])),
            wantcard=NS(find_many=AsyncMock(return_value=[])), cardprinting=NS(find_many=AsyncMock(return_value=[])),
            cardruling=NS(find_many=AsyncMock(return_value=[])))
    with patch('src.services.simulated_collection_service.db', db), patch('src.services.simulated_collection_service.resolve_offer_names', AsyncMock(return_value={'laceradortaurino': 'Taurean Mauler'})), patch('src.services.simulated_collection_service.upcoming_reprints_by_name', AsyncMock(return_value={'taurean mauler': [notice]})):
        analysis = await SimulatedCollectionService.analyze_raw_text('owner', '1 Lacerador taurino', include_history=False)
    assert analysis.cards[0].cardName == 'Taurean Mauler'
    assert analysis.cards[0].upcomingReprints[0].model_dump() == notice
    db.cardprinting.find_many.assert_awaited_once()
    db.cardprinting.find_many.reset_mock()
    with patch.object(scryfall, 'db', db), patch.object(ScryfallService, 'get_card_details_es', AsyncMock(return_value={'id': 'catalog', '_catalog_id': 'catalog', 'name': 'Taurean Mauler'})), patch.object(scryfall, 'upcoming_reprints_by_name', AsyncMock(return_value={'taurean mauler': [notice]})):
        detail = await scryfall.get_card_details(id='old', name='Taurean Mauler', set=None, collector_number=None)
    assert detail['upcomingReprints'] == [notice]
    db.cardprinting.find_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_scryfall_foundations_commander_date_is_corrected_in_warning_detection():
    from src.services.set_release_dates import corrected_set_release
    prints = [printing('Taurean Mauler', 'old', '2008-02-01'), printing('Taurean Mauler', 'fdc', '2024-11-15')]
    result = await upcoming_reprints_by_name(['Taurean Mauler'], prints, NS(query_raw=AsyncMock(return_value=[])), today=date(2026, 10, 1))
    assert result['taurean mauler'][0]['releaseDate'] == '2026-10-02'
    assert corrected_set_release('fdn', '2024-11-15') == '2024-11-15'


@pytest.mark.asyncio
async def test_expansion_catalog_returns_the_official_date_even_before_worker_backfill():
    from datetime import datetime, timezone
    from src.routers import catalog
    old = datetime(2024, 11, 15, tzinfo=timezone.utc)
    sets = [NS(code=code, name=code, releasedAt=old, cardCount=100) for code in ('fdc', 'fdn')]
    db = NS(cardset=NS(find_many=AsyncMock(return_value=sets)), collectioncard=NS(find_many=AsyncMock(return_value=[])), query_raw=AsyncMock(return_value=[]))
    with patch.object(catalog, 'db', db):
        response = await catalog.list_sets(limit=2000, user_id='owner')
    dates = {row['code']: row['releasedAt'] for row in response}
    assert dates['fdc'] == datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert dates['fdn'] == old
    assert sets[0].releasedAt == old  # A read never writes catalog metadata.
