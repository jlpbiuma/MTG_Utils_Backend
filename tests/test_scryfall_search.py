import pytest
from unittest.mock import AsyncMock, patch
from src.services.scryfall_service import ScryfallService, score_card_match


def test_score_card_match_ranks_exact_and_word_matches_first():
    assert score_card_match("Vivi", "Vivi") == 0
    assert score_card_match("Vivi Ornitier", "Vivi") == 1
    assert score_card_match("Vivi's Persistence", "Vivi") == 1
    assert score_card_match("Vivid Creek", "Vivi") == 2
    assert score_card_match("Vivien Reid", "Vivi") == 2
    assert score_card_match("A-Vivi Ornitier", "Vivi") == 3
    assert score_card_match("Phyrexian Vivisector", "Vivi") == 4
    assert score_card_match("Revivify", "Vivi") == 4


@pytest.mark.asyncio
async def test_search_cards_vivi_puts_vivi_ornitier_at_top():
    cards = [
        {"name": "Revivify", "id": "1"},
        {"name": "Vivid Creek", "id": "2"},
        {"name": "Phyrexian Vivisector", "id": "3"},
        {"name": "Vivi Ornitier", "id": "4"},
        {"name": "Vivien Reid", "id": "5"},
        {"name": "Vivi's Persistence", "id": "6"},
    ]
    with patch.object(ScryfallService, "search_cards_local", new_callable=AsyncMock) as mock_local:
        mock_local.return_value = cards
        res = await ScryfallService.search_cards("Vivi")
        results = [c["name"] for c in res["data"]]
        assert results[0] == "Vivi Ornitier"
        assert results[1] == "Vivi's Persistence"
