"""Dormant cards: not in an active deck and not recommended by EDHREC for its commanders."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.collection_service import CollectionService


def _owned(cid, name, type_line="Instant"):
    return NS(id=cid, cardScryfallId=f"s-{cid}", cardName=name, isFoil=False, quantity=1, setCode=None,
              collectorNumber=None, manaCost=None, typeLine=type_line, imageUri=None)


@pytest.mark.asyncio
async def test_dormant_excludes_deck_cards_basics_and_synced_edhrec_recommendations():
    deck = NS(commander="Atraxa, Praetors' Voice", cards=[NS(cardName="Sol Ring")])
    db = MagicMock()
    db.deck.find_many = AsyncMock(return_value=[deck])
    db.edhreccommander.find_many = AsyncMock(return_value=[NS(cardsJson={
        "creatures": [{"name": "Deepglow Skate", "normalizedName": "deepglow skate"}],
        "instants": [{"name": "Fire // Ice"}],
    })])
    db.collectioncard.find_many = AsyncMock(return_value=[
        _owned("1", "Sol Ring"), _owned("2", "Deepglow Skate"), _owned("3", "Fire // Ice"),
        _owned("4", "Island", "Basic Land — Island"), _owned("5", "Lightning Bolt"),
    ])
    summary = NS(quotes={"s-5": NS(unitPrice=NS(trend=2.0))})
    with patch("src.services.collection_service.db", db), \
         patch("src.services.collection_service.PricingService.get_price_summary", AsyncMock(return_value=summary)):
        result = await CollectionService.get_dormant_cards("u1")

    assert [card.cardName for card in result.cards] == ["Lightning Bolt"]
    assert result.activeCommanders == ["Atraxa, Praetors' Voice"]
    where = db.edhreccommander.find_many.await_args.kwargs["where"]
    assert where == {"normalizedName": {"in": ["atraxa, praetors' voice"]}, "status": "synced"}
