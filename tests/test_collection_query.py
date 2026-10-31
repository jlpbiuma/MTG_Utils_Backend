import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.collection_service import CollectionService


def _card(cid, name, scryfall_id, qty=1, type_line=None, mana_cost=None):
    return SimpleNamespace(
        id=cid,
        userId="user-1",
        cardScryfallId=scryfall_id,
        cardName=name,
        quantity=qty,
        isFoil=False,
        setCode=None,
        collectorNumber=None,
        manaCost=mana_cost,
        typeLine=type_line,
        imageUri=None,
        updatedAt="2026-01-01T00:00:00Z",
    )


def _fake_summary(quotes=None):
    return SimpleNamespace(
        quotes=quotes or {},
        currencySymbol="€",
    )


async def _call_query(records, deck_cards=None, **kwargs):
    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=records)
    mock_db.deckcard.find_many = AsyncMock(return_value=deck_cards or [])
    with (
        patch("src.services.collection_service.db", mock_db),
        patch(
            "src.services.collection_service.resolve_minio_image_uris",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.services.collection_service.PricingService.get_price_summary",
            new=AsyncMock(return_value=_fake_summary()),
        ),
    ):
        return await CollectionService.get_user_collection_query("user-1", **kwargs)


@pytest.mark.asyncio
async def test_query_groups_over_whole_collection_in_defined_order():
    records = [
        _card("c1", "Llanowar Elves", "s1", type_line="Creature — Elf Druid"),
        _card("c2", "Forest", "s2", qty=3, type_line="Basic Land — Forest"),
        _card("c3", "Lightning Bolt", "s3", type_line="Instant"),
        _card("c4", "Slash Clone", "s4", qty=2, type_line=None),
    ]

    result = await _call_query(records)

    assert result.grouped is True
    assert result.uniqueCards == 4
    assert result.totalCards == 7
    assert [s.key for s in result.sections] == ["creatures", "instants", "lands", "other"]
    assert result.sections[0].label == "Criaturas"
    assert result.sections[0].totalCards == 1
    assert result.sections[0].uniqueCards == 1
    assert result.sections[0].ownedCards == 1
    assert result.sections[0].completionPercentage == 100.0
    # Null type_line buckets into "Otras Cartas"
    other = result.sections[-1]
    assert other.key == "other"
    assert other.totalCards == 2  # qty 2 for Slash Clone


@pytest.mark.asyncio
async def test_query_filters_by_name_on_backend():
    records = [
        _card("c1", "Sol Ring", "s1", type_line="Artifact"),
        _card("c2", "Arcane Signet", "s2", type_line="Artifact"),
        _card("c3", "Lotus Petal", "s3", type_line="Artifact"),
    ]
    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=records[:2])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])

    with (
        patch("src.services.collection_service.db", mock_db),
        patch(
            "src.services.collection_service.resolve_minio_image_uris",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.services.collection_service.PricingService.get_price_summary",
            new=AsyncMock(return_value=_fake_summary()),
        ),
    ):
        result = await CollectionService.get_user_collection_query(
            "user-1", query="ring", grouped=False
        )

    mock_db.collectioncard.find_many.assert_any_await(
        where={
            "userId": "user-1",
            "cardName": {"contains": "ring", "mode": "insensitive"},
        },
        order=[{"cardName": "asc"}, {"id": "asc"}],
    )
    assert result.grouped is False
    assert result.query == "ring"
    assert result.uniqueCards == 2
    assert [c.cardScryfallId for c in result.cards] == ["s2", "s1"]


@pytest.mark.asyncio
async def test_query_sorts_by_name_desc_on_backend():
    records = [
        _card("c1", "Apple", "s1"),
        _card("c2", "Banana", "s2"),
        _card("c3", "Fig", "s3"),
    ]

    result = await _call_query(records, sort="name", direction="desc", grouped=False)

    assert [c.cardName for c in result.cards] == ["Fig", "Banana", "Apple"]


@pytest.mark.asyncio
async def test_query_sorts_by_cmc_on_backend():
    records = [
        _card("c1", "Bolt", "s1", type_line="Instant", mana_cost="{R}"),
        _card("c2", "Ring", "s2", type_line="Artifact", mana_cost="{1}"),
        _card("c3", "Titan", "s3", type_line="Creature", mana_cost="{2}{G}{G}"),
    ]

    result = await _call_query(records, sort="cmc", direction="asc", grouped=False)

    assert [c.cardName for c in result.cards] == ["Bolt", "Ring", "Titan"]


@pytest.mark.asyncio
async def test_query_sorts_by_price_trend_desc_on_backend():
    records = [
        _card("c1", "Cheap", "s-1", type_line="Artifact"),
        _card("c2", "Pricy", "s-2", type_line="Artifact"),
    ]
    quotes = {
        "s-1": SimpleNamespace(unitPrice=SimpleNamespace(trend=1.0), subtotal=1.0, quantity=1),
        "s-2": SimpleNamespace(unitPrice=SimpleNamespace(trend=20.0), subtotal=20.0, quantity=1),
    }

    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=records)
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    with (
        patch("src.services.collection_service.db", mock_db),
        patch(
            "src.services.collection_service.resolve_minio_image_uris",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.services.collection_service.PricingService.get_price_summary",
            new=AsyncMock(return_value=_fake_summary(quotes)),
        ) as pricing,
    ):
        result = await CollectionService.get_user_collection_query(
            "user-1", sort="price_trend", direction="desc", grouped=False
        )

    pricing.assert_awaited_once()
    assert [c.cardName for c in result.cards] == ["Pricy", "Cheap"]


@pytest.mark.asyncio
async def test_query_computes_section_prices_on_backend():
    records = [
        _card("c1", "Aura", "s-a", qty=2, type_line="Enchantment — Aura"),
        _card("c2", "Bolt", "s-b", type_line="Instant"),
    ]
    quotes = {
        "s-a": SimpleNamespace(unitPrice=SimpleNamespace(trend=5.0), subtotal=10.0, quantity=2),
        "s-b": SimpleNamespace(unitPrice=SimpleNamespace(trend=3.0), subtotal=3.0, quantity=1),
    }

    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=records)
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    with (
        patch("src.services.collection_service.db", mock_db),
        patch(
            "src.services.collection_service.resolve_minio_image_uris",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.services.collection_service.PricingService.get_price_summary",
            new=AsyncMock(return_value=_fake_summary(quotes)),
        ) as pricing,
    ):
        result = await CollectionService.get_user_collection_query(
            "user-1", grouped=True
        )

    pricing.assert_awaited_once()
    by_key = {s.key: s for s in result.sections}
    assert by_key["enchantments"].sectionTotalPrice == 10.0
    assert by_key["instants"].sectionTotalPrice == 3.0
    assert result.currencySymbol == "€"


@pytest.mark.asyncio
async def test_query_reports_deck_demand_and_sorts_by_it():
    records = [
        _card("c1", "Sol Ring", "s1", type_line="Artifact"),
        _card("c2", "Arcane Signet", "s2", type_line="Artifact"),
        _card("c3", "Command Tower", "s3", type_line="Land"),
    ]
    deck_cards = [
        SimpleNamespace(cardName="Sol Ring", quantity=1, deckId="d1", deck=SimpleNamespace(name="Atraxa")),
        SimpleNamespace(cardName="sol ring", quantity=1, deckId="d2", deck=SimpleNamespace(name="Tidus")),
        SimpleNamespace(cardName="Sol Ring", quantity=1, deckId="d3", deck=SimpleNamespace(name="Y'shtola")),
        SimpleNamespace(cardName="Arcane Signet", quantity=2, deckId="d1", deck=SimpleNamespace(name="Atraxa")),
    ]

    result = await _call_query(
        records,
        deck_cards=deck_cards,
        sort="requested_decks",
        direction="desc",
        grouped=False,
    )

    by_name = {c.cardName: c for c in result.cards}
    assert by_name["Sol Ring"].requestedInDecksCount == 3
    assert [r.deckName for r in by_name["Sol Ring"].requestedInDecks] == [
        "Atraxa",
        "Tidus",
        "Y'shtola",
    ]
    assert by_name["Arcane Signet"].requestedInDecksCount == 1
    assert by_name["Arcane Signet"].requestedInDecks[0].quantity == 2
    assert by_name["Command Tower"].requestedInDecksCount == 0
    assert [c.cardName for c in result.cards] == ["Sol Ring", "Arcane Signet", "Command Tower"]

def _quote(trend):
    return SimpleNamespace(unitPrice=SimpleNamespace(trend=trend), subtotal=trend)


@pytest.mark.asyncio
async def test_grouped_pages_are_consecutive_slices_with_whole_section_totals():
    records = [_card(f"c{i}", f"Creature {i:02}", f"s{i}", type_line="Creature") for i in range(5)]
    records += [_card(f"l{i}", f"Land {i:02}", f"t{i}", qty=2, type_line="Land") for i in range(3)]

    pages = [await _call_query(records, grouped=True, page=page, limit=3) for page in (1, 2, 3)]

    names = [[c.cardName for s in p.sections for c in s.cards] for p in pages]
    assert names == [["Creature 00", "Creature 01", "Creature 02"],
                     ["Creature 03", "Creature 04", "Land 00"],
                     ["Land 01", "Land 02"]]
    assert [p.hasMore for p in pages] == [True, True, False]
    assert all(p.uniqueCards == 8 and p.totalCards == 11 for p in pages)
    # A section split across pages keeps the totals of the whole section.
    assert [(s.key, s.uniqueCards, s.totalCards) for s in pages[1].sections] == [("creatures", 5, 5), ("lands", 3, 6)]


@pytest.mark.asyncio
async def test_price_filter_runs_over_the_whole_collection_before_paginating():
    records = [_card(f"c{i}", f"Card {i:02}", f"s{i}") for i in range(6)]
    quotes = {f"s{i}": _quote(float(i)) for i in range(5)}  # s5 has no price
    mock_db = MagicMock()
    mock_db.collectioncard.find_many = AsyncMock(return_value=records)
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    with (
        patch("src.services.collection_service.db", mock_db),
        patch("src.services.collection_service.resolve_minio_image_uris", new=AsyncMock(return_value={})),
        patch("src.services.collection_service.PricingService.get_price_summary", new=AsyncMock(return_value=_fake_summary(quotes))),
    ):
        first = await CollectionService.get_user_collection_query("user-1", grouped=False, min_price=1, max_price=3, page=1, limit=2)
        second = await CollectionService.get_user_collection_query("user-1", grouped=False, min_price=1, max_price=3, page=2, limit=2)

    assert first.uniqueCards == second.uniqueCards == 3
    assert [c.cardName for c in first.cards + second.cards] == ["Card 01", "Card 02", "Card 03"]
    assert first.hasMore is True and second.hasMore is False
