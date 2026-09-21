from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import edhrec_service
from src.services.edhrec_service import (
    EdhrecService,
    get_edhrec_card_image_url,
    to_edhrec_slug,
)


def test_to_edhrec_slug():
    assert to_edhrec_slug("Atris, Oracle of Half-Truths") == "atris-oracle-of-half-truths"
    assert to_edhrec_slug("Niv-Mizzet, Parun") == "niv-mizzet-parun"
    assert to_edhrec_slug("Kethis, the Hidden Hand") == "kethis-the-hidden-hand"
    assert to_edhrec_slug("Urza, Lord High Artificer") == "urza-lord-high-artificer"
    assert to_edhrec_slug("Fire // Ice") == "fire-ice"
    assert to_edhrec_slug("Jace, the Mind Sculptor") == "jace-the-mind-sculptor"
    assert to_edhrec_slug("") == ""

def test_get_edhrec_card_image_url():
    scryfall_id = "44445555-6666-7777-8888-99990000aaaa"
    url = get_edhrec_card_image_url(scryfall_id)
    assert url == "https://card-images.edhrec.com/normal/front/4/4/44445555-6666-7777-8888-99990000aaaa.jpg"

    assert get_edhrec_card_image_url("") is None
    assert get_edhrec_card_image_url("a") is None


def _edhrec_commander_payload():
    """Shape returned by json.edhrec.com/pages/commanders/<slug>.json"""
    return {
        "container": {
            "json_dict": {
                "card": {
                    "name": "Atraxa, Praetors' Voice",
                    "id": "aaaa1111-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "num_decks": 12000,
                    "color_identity": ["W", "U", "B", "G"],
                    "type_line": "Legendary Creature — Phyrexian Angel Horror",
                },
                "cardlists": [
                    {
                        "header": "High Synergy Cards",
                        "tag": "highsynergycards",
                        "cardviews": [
                            {
                                "id": "c05c2aa6-29c7-40f8-872e-91099b9225c4",
                                "name": "Sol Ring",
                                "sanitized": "sol-ring",
                                "synergy": 0.05,
                                "num_decks": 9100,
                                "potential_decks": 10000,
                            },
                            {
                                "id": "bbbb2222-cccc-dddd-eeee-ffffffffffff",
                                "name": "Faeburrow Elder",
                                "sanitized": "faeburrow-elder",
                                "synergy": 0.354,
                                "num_decks": 740,
                                "potential_decks": 1000,
                            },
                        ],
                    },
                    {
                        "header": "Top Cards",
                        "tag": "topcards",
                        "cardviews": [
                            {
                                "id": "c05c2aa6-29c7-40f8-872e-91099b9225c4",
                                "name": "Sol Ring",
                                "sanitized": "sol-ring",
                                "synergy": 0.05,
                                "num_decks": 9100,
                                "potential_decks": 10000,
                            },
                        ],
                    },
                ],
            }
        }
    }


def _mock_edhrec_http(payload):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = payload

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.asyncio
async def test_fetch_edhrec_data_parses_cards_from_container_json_dict_cardlists():
    edhrec_service._edhrec_cache.clear()
    mock_client = _mock_edhrec_http(_edhrec_commander_payload())

    with patch("src.services.edhrec_service.httpx.AsyncClient", return_value=mock_client):
        result = await EdhrecService.fetch_edhrec_data("Atraxa, Praetors' Voice")

    assert result is not None
    assert result["categories"] == ["High Synergy Cards", "Top Cards"]
    assert len(result["cards"]) == 2

    # Sorted by community inclusion % descending
    assert result["cards"][0]["name"] == "Sol Ring"
    assert result["cards"][0]["inclusionPct"] == 91.0
    assert result["cards"][0]["synergy"] == 5.0
    assert result["cards"][0]["categories"] == ["High Synergy Cards", "Top Cards"]
    assert result["cards"][0]["imageUri"] == (
        "https://card-images.edhrec.com/normal/front/c/0/c05c2aa6-29c7-40f8-872e-91099b9225c4.jpg"
    )

    fae = result["cards"][1]
    assert fae["name"] == "Faeburrow Elder"
    assert fae["inclusionPct"] == 74.0
    assert fae["synergy"] == 35.4


@pytest.mark.asyncio
async def test_get_deck_recommendations_highlights_cards_owned_in_collection():
    edhrec_service._edhrec_cache.clear()

    deck = SimpleNamespace(
        id="deck-1",
        userId="user-1",
        name="Atraxa Counters",
        commander="Atraxa, Praetors' Voice",
        commanderImageUri=None,
        commanderScryfallId=None,
    )
    deck_cards = [
        SimpleNamespace(cardName="Sol Ring", quantity=1, deckId="deck-1", deck=deck),
    ]
    collection_cards = [
        SimpleNamespace(cardName="Faeburrow Elder", quantity=2),
        SimpleNamespace(cardName="Sol Ring", quantity=3),
    ]

    fake_db = SimpleNamespace(
        deck=SimpleNamespace(find_unique=AsyncMock(return_value=deck)),
        deckcard=SimpleNamespace(
            find_first=AsyncMock(return_value=None),
            find_many=AsyncMock(return_value=deck_cards),
        ),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=collection_cards)),
        wantcard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with (
        patch("src.services.edhrec_service.db", new=fake_db),
        patch.object(
            EdhrecService,
            "fetch_edhrec_data",
            new=AsyncMock(
                return_value={
                    "categories": ["High Synergy Cards"],
                    "cards": [
                        {
                            "id": "1",
                            "name": "Sol Ring",
                            "normalizedName": "sol ring",
                            "sanitized": "sol-ring",
                            "category": "High Synergy Cards",
                            "categories": ["High Synergy Cards"],
                            "numDecks": 9100,
                            "potentialDecks": 10000,
                            "inclusionPct": 91.0,
                            "synergy": 5.0,
                            "imageUri": None,
                        },
                        {
                            "id": "2",
                            "name": "Faeburrow Elder",
                            "normalizedName": "faeburrow elder",
                            "sanitized": "faeburrow-elder",
                            "category": "High Synergy Cards",
                            "categories": ["High Synergy Cards"],
                            "numDecks": 740,
                            "potentialDecks": 1000,
                            "inclusionPct": 74.0,
                            "synergy": 35.4,
                            "imageUri": None,
                        },
                        {
                            "id": "3",
                            "name": "Command Tower",
                            "normalizedName": "command tower",
                            "sanitized": "command-tower",
                            "category": "High Synergy Cards",
                            "categories": ["High Synergy Cards"],
                            "numDecks": 800,
                            "potentialDecks": 1000,
                            "inclusionPct": 80.0,
                            "synergy": 10.0,
                            "imageUri": None,
                        },
                    ],
                }
            ),
        ),
    ):
        result = await EdhrecService.get_deck_recommendations("deck-1", "user-1")

    by_name = {c.name: c for c in result.recommendations}

    # In deck (and collection) → isInDeck
    assert by_name["Sol Ring"].isInDeck is True
    assert by_name["Sol Ring"].isInCollection is True
    assert by_name["Sol Ring"].collectionQuantity == 3

    # Not in deck, but owned → emphasized as in collection
    assert by_name["Faeburrow Elder"].isInDeck is False
    assert by_name["Faeburrow Elder"].isInCollection is True
    assert by_name["Faeburrow Elder"].collectionQuantity == 2

    # Missing from both
    assert by_name["Command Tower"].isInDeck is False
    assert by_name["Command Tower"].isInCollection is False
    assert by_name["Command Tower"].collectionQuantity == 0


@pytest.mark.asyncio
async def test_get_deck_recommendations_includes_requested_in_decks_by_name():
    """Cross-deck demand matches by normalized name, ignoring printing/art."""
    edhrec_service._edhrec_cache.clear()

    deck = SimpleNamespace(
        id="deck-1",
        userId="user-1",
        name="Atraxa Counters",
        commander="Atraxa, Praetors' Voice",
        commanderImageUri=None,
        commanderScryfallId=None,
    )
    other_deck = SimpleNamespace(id="deck-2", name="Tidus Voltron", userId="user-1")

    # Current deck has Sol Ring; another deck asks for Faeburrow (different printing id)
    current_deck_cards = [
        SimpleNamespace(cardName="Sol Ring", quantity=1, deckId="deck-1", deck=deck),
    ]
    all_user_deck_cards = [
        SimpleNamespace(
            cardName="Sol Ring",
            quantity=1,
            deckId="deck-1",
            deck=deck,
            manaCost="{G}",
        ),
        SimpleNamespace(
            cardName="Faeburrow Elder",
            quantity=1,
            deckId="deck-2",
            deck=other_deck,
            cardScryfallId="different-printing-id",
        ),
        SimpleNamespace(
            cardName="Command Tower",
            quantity=2,
            deckId="deck-2",
            deck=other_deck,
        ),
        # Also in current deck under another art — still one demand entry for this deck
        SimpleNamespace(
            cardName="Command Tower",
            quantity=1,
            deckId="deck-1",
            deck=deck,
            cardScryfallId="cmd-tower-art-b",
        ),
    ]

    async def deckcard_find_many(**kwargs):
        where = kwargs.get("where") or {}
        if where.get("deckId") == "deck-1":
            return current_deck_cards
        if "deck" in where:
            return all_user_deck_cards
        return []

    fake_db = SimpleNamespace(
        deck=SimpleNamespace(find_unique=AsyncMock(return_value=deck)),
        deckcard=SimpleNamespace(
            find_first=AsyncMock(return_value=None),
            find_many=AsyncMock(side_effect=deckcard_find_many),
        ),
        collectioncard=SimpleNamespace(
            find_many=AsyncMock(
                return_value=[SimpleNamespace(cardName="Sol Ring", quantity=1)]
            )
        ),
        wantcard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with (
        patch("src.services.edhrec_service.db", new=fake_db),
        patch.object(
            EdhrecService,
            "fetch_edhrec_data",
            new=AsyncMock(
                return_value={
                    "categories": ["Top Cards"],
                    "cards": [
                        {
                            "id": "1",
                            "name": "Sol Ring",
                            "normalizedName": "sol ring",
                            "sanitized": "sol-ring",
                            "category": "Top Cards",
                            "categories": ["Top Cards"],
                            "numDecks": 9100,
                            "potentialDecks": 10000,
                            "inclusionPct": 91.0,
                            "synergy": 5.0,
                            "imageUri": None,
                        },
                        {
                            "id": "2",
                            "name": "Faeburrow Elder",
                            "normalizedName": "faeburrow elder",
                            "sanitized": "faeburrow-elder",
                            "category": "Top Cards",
                            "categories": ["Top Cards"],
                            "numDecks": 740,
                            "potentialDecks": 1000,
                            "inclusionPct": 74.0,
                            "synergy": 35.4,
                            "imageUri": None,
                        },
                        {
                            "id": "3",
                            "name": "Command Tower",
                            "normalizedName": "command tower",
                            "sanitized": "command-tower",
                            "category": "Top Cards",
                            "categories": ["Top Cards"],
                            "numDecks": 800,
                            "potentialDecks": 1000,
                            "inclusionPct": 80.0,
                            "synergy": 10.0,
                            "imageUri": None,
                        },
                        {
                            "id": "4",
                            "name": "Lightning Greaves",
                            "normalizedName": "lightning greaves",
                            "sanitized": "lightning-greaves",
                            "category": "Top Cards",
                            "categories": ["Top Cards"],
                            "numDecks": 500,
                            "potentialDecks": 1000,
                            "inclusionPct": 50.0,
                            "synergy": 1.0,
                            "imageUri": None,
                        },
                    ],
                }
            ),
        ),
    ):
        result = await EdhrecService.get_deck_recommendations("deck-1", "user-1")

    by_name = {c.name: c for c in result.recommendations}

    # Only in current deck
    assert by_name["Sol Ring"].requestedInDecksCount == 1
    assert by_name["Sol Ring"].requestedInDecks[0].deckName == "Atraxa Counters"
    assert by_name["Sol Ring"].requestedInDecks[0].completionPercentage == 50.0
    assert by_name["Sol Ring"].requestedInDecks[0].colors == ["G"]

    # Only in another deck (matched by name, not scryfall id)
    assert by_name["Faeburrow Elder"].requestedInDecksCount == 1
    assert by_name["Faeburrow Elder"].requestedInDecks[0].deckId == "deck-2"
    assert by_name["Faeburrow Elder"].requestedInDecks[0].deckName == "Tidus Voltron"

    # In both decks; quantities aggregated per deck
    assert by_name["Command Tower"].requestedInDecksCount == 2
    tower_by_deck = {r.deckId: r for r in by_name["Command Tower"].requestedInDecks}
    assert tower_by_deck["deck-1"].quantity == 1
    assert tower_by_deck["deck-2"].quantity == 2
    # Current deck listed first
    assert by_name["Command Tower"].requestedInDecks[0].deckId == "deck-1"

    # In no decks
    assert by_name["Lightning Greaves"].requestedInDecksCount == 0
    assert by_name["Lightning Greaves"].requestedInDecks == []
