import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.schemas.deck import DeckCreate, DeckUpdate
from src.services.deck_service import DeckService, is_commander_candidate_type, _is_commander_eligible


@pytest.mark.parametrize(
    ("type_line", "oracle_text", "expected"),
    [
        ("Legendary Creature — Dragon", None, True),
        ("Legendary Artifact — Vehicle", None, True),
        ("Legendary Artifact", None, False),
        ("Legendary Artifact — Equipment", None, False),
        ("Legendary Sorcery", None, False),
        ("Legendary Instant", None, False),
        ("Creature — Human Wizard", None, False),
        (None, None, False),
        # "Dihada, Binder of Wills" ("Dihada, Doblegadora de Voluntades" en
        # español) is a legendary planeswalker whose oracle text explicitly
        # states she can be your commander.
        ("Legendary Planeswalker — Dihada", "Dihada, Binder of Wills can be your commander.", True),
        # A legendary planeswalker without that clause is not a candidate.
        ("Legendary Planeswalker — Dihada", None, False),
        ("Legendary Planeswalker — Narset", "Narset, Enlightened Master gets +1/+0 and has flying.", False),
    ],
)
def test_commander_candidate_types(type_line, oracle_text, expected):
    assert is_commander_candidate_type(type_line, oracle_text) is expected


def test_dihada_binder_of_wills_can_be_commander():
    # Real Scryfall response for "Dihada, Binder of Wills"
    # (es: "Dihada, Doblegadora de Voluntades"): type_line is
    # "Legendary Planeswalker — Dihada" and the oracle text ends with
    # "Dihada, Binder of Wills can be your commander."
    type_line = "Legendary Planeswalker — Dihada"
    oracle_text = (
        "+2: Up to one target legendary creature gains vigilance, lifelink, and "
        "indestructible until your next turn.\n"
        "\u22123: Reveal the top four cards of your library. Put any number of "
        "legendary cards from among them into your hand and the rest into your "
        "graveyard. Create a Treasure token for each card put into your graveyard "
        "this way.\n"
        "\u221211: Gain control of all nonland permanents until end of turn. Untap "
        "them. They gain haste until end of turn.\n"
        "Dihada, Binder of Wills can be your commander."
    )

    assert is_commander_candidate_type(type_line, oracle_text) is True


@pytest.mark.asyncio
async def test_dihada_eligible_server_side_from_catalog_oracle_text():
    # The worker-hydrated catalog entry carries the English oracle text. The
    # backend uses it to authorize Dihada, Binder of Wills (es: "Dihada,
    # Doblegadora de Voluntades") as commander without a live Scryfall call.
    cat = {
        "id": "ddeb54d6-a600-42b9-98df-20f8d58caed8",
        "typeLine": "Legendary Planeswalker — Dihada",
        "oracleText": "Dihada, Binder of Wills can be your commander.",
    }
    with (
        patch(
            "src.services.deck_service.ScryfallService.get_catalog_card",
            new_callable=AsyncMock,
            return_value=cat,
        ) as get_catalog,
        patch(
            "src.services.deck_service.ScryfallService.get_card_named",
            new_callable=AsyncMock,
        ) as get_named,
    ):
        ok = await _is_commander_eligible(
            "Dihada, Binder of Wills", "Legendary Planeswalker — Dihada"
        )

    assert ok is True
    get_catalog.assert_awaited_once()
    get_named.assert_not_awaited()


@pytest.mark.asyncio
async def test_planeswalker_eligibility_falls_back_to_scryfall_when_catalog_lacks_text():
    cat_without_text = {
        "id": "ddeb54d6-a600-42b9-98df-20f8d58caed8",
        "typeLine": "Legendary Planeswalker — Dihada",
        "oracleText": None,
    }
    remote_card = {
        "id": "ddeb54d6-a600-42b9-98df-20f8d58caed8",
        "oracle_text": "Dihada, Binder of Wills can be your commander.",
    }
    with (
        patch(
            "src.services.deck_service.ScryfallService.get_catalog_card",
            new_callable=AsyncMock,
            return_value=cat_without_text,
        ),
        patch(
            "src.services.deck_service.ScryfallService.get_card_named",
            new_callable=AsyncMock,
            return_value=remote_card,
        ) as get_named,
    ):
        ok = await _is_commander_eligible(
            "Dihada, Binder of Wills", "Legendary Planeswalker — Dihada"
        )

    assert ok is True
    get_named.assert_awaited_once()


@pytest.mark.asyncio
async def test_planeswalker_without_commander_clause_is_not_eligible():
    cat = {
        "id": "narset-id",
        "typeLine": "Legendary Planeswalker — Narset",
        "oracleText": "Narset, Enlightened Master gets +1/+0 and has flying.",
    }
    with patch(
        "src.services.deck_service.ScryfallService.get_catalog_card",
        new_callable=AsyncMock,
        return_value=cat,
    ):
        ok = await _is_commander_eligible(
            "Narset, Enlightened Master", "Legendary Planeswalker — Narset"
        )

    assert ok is False


@pytest.mark.asyncio
async def test_commander_eligibility_uses_type_line_without_scryfall_lookup():
    with (
        patch(
            "src.services.deck_service.ScryfallService.get_catalog_card",
            new_callable=AsyncMock,
        ) as get_catalog,
        patch(
            "src.services.deck_service.ScryfallService.get_card_named",
            new_callable=AsyncMock,
        ) as get_named,
    ):
        ok = await _is_commander_eligible("Niv-Mizzet, Parun", "Legendary Creature — Dragon Wizard")

    assert ok is True
    get_catalog.assert_not_awaited()
    get_named.assert_not_awaited()

def test_deck_schemas():
    create = DeckCreate(name="Niv Mizzet", format="Commander", commander="Niv-Mizzet, Parun")
    assert create.name == "Niv Mizzet"
    assert create.format == "Commander"
    assert create.commander == "Niv-Mizzet, Parun"

    update = DeckUpdate(name="Niv Updated")
    assert update.name == "Niv Updated"
    assert update.commander is None

@pytest.mark.asyncio
async def test_get_user_decks_empty():
    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    with patch("src.services.deck_service.db", mock_db):
        decks = await DeckService.get_user_decks("user-123")
        assert len(decks) == 0


@pytest.mark.asyncio
async def test_get_user_decks_enriches_colors_and_prices():
    from unittest.mock import AsyncMock, MagicMock, patch
    from datetime import datetime

    now = datetime.now()

    def make_card(card_id, name, scry_id, mana_cost, quantity, assigned):
        card = MagicMock()
        card.id = card_id
        card.deckId = "deck-1"
        card.cardScryfallId = scry_id
        card.cardName = name
        card.quantity = quantity
        card.assignedQuantity = assigned
        card.isSideboard = False
        card.isCommander = False
        card.manaCost = mana_cost
        card.typeLine = "Legendary Creature"
        card.imageUri = None
        card.setCode = None
        return card

    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "Azorius Blink"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Brago, King Eternal"
    deck.commanderScryfallId = "scry-brago"
    deck.commanderImageUri = None
    deck.createdAt = now
    deck.updatedAt = now
    deck.cards = [
        make_card("c1", "Brago, King Eternal", "scry-brago", "{2}{W}{U}", 1, 0),
        make_card("c2", "Sol Ring", "scry-sol", "{1}", 1, 1),
    ]

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])

    brago_quote = MagicMock()
    brago_quote.unitPrice.trend = 3.5
    sol_quote = MagicMock()
    sol_quote.unitPrice.trend = 2.25

    with patch("src.services.deck_service.db", mock_db), \
         patch(
             "src.services.deck_service.PricingService._latest_provider_quote",
             new_callable=AsyncMock,
             side_effect=[brago_quote, sol_quote],
         ):
        decks = await DeckService.get_user_decks("user-1")

    assert len(decks) == 1
    summary = decks[0]
    assert summary.colors == ["W", "U"]
    assert summary.colorIdentity == "WU"
    assert summary.totalValue == 5.75
    assert summary.ownedValue == 2.25
    assert summary.missingValue == 3.5
    assert summary.currency == "EUR"
    assert summary.currencySymbol == "€"

    # Sol Ring owned (assigned) -> ownedValue; Brago missing -> missingValue

@pytest.mark.asyncio
async def test_get_deck_detail_missing_count_is_collection_aware():
    from datetime import datetime
    now = datetime.now()

    def make_card(card_id, name, scry_id, mana_cost, qty, assigned, type_line="Creature"):
        card = MagicMock()
        card.id = card_id
        card.deckId = "deck-1"
        card.cardScryfallId = scry_id
        card.cardName = name
        card.quantity = qty
        card.assignedQuantity = assigned
        card.isSideboard = False
        card.isCommander = False
        card.manaCost = mana_cost
        card.typeLine = type_line
        card.imageUri = "https://cards.scryfall.test/img.jpg"
        card.setCode = None
        return card

    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "Azorius Blink"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Brago, King Eternal"
    deck.commanderScryfallId = "scry-brago"
    deck.commanderImageUri = None
    deck.createdAt = now
    deck.updatedAt = now
    deck.cards = [
        make_card("c1", "Counterspell", "scry-cs", "{1}{U}", 4, 0),
        make_card("c2", "Sol Ring", "scry-sol", "{1}", 1, 1),
        make_card("c3", "Mana Crypt", "scry-crypt", "{0}", 1, 0),
    ]

    # Collection fully covers Counterspell and Sol Ring; Mana Crypt is missing
    col_cs = MagicMock()
    col_cs.cardName = "Counterspell"
    col_cs.quantity = 4
    col_sol = MagicMock()
    col_sol.cardName = "Sol Ring"
    col_sol.quantity = 1

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=deck)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[col_cs, col_sol])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.update = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.resolve_minio_image_uris", new_callable=AsyncMock, return_value={}), \
         patch("src.services.deck_service.PricingService._latest_provider_quote", new_callable=AsyncMock, return_value=None):
        detail = await DeckService.get_deck_detail("deck-1", "user-1")

    assert detail is not None
    by_name = {c.cardName: c for c in detail.cards}
    # Counterspell is fully covered by the collection even though not assigned
    assert by_name["Counterspell"].missingCount == 0
    assert by_name["Counterspell"].ownedInCollection == 4
    # Sol Ring is covered via an assigned physical copy
    assert by_name["Sol Ring"].missingCount == 0
    # Mana Crypt is genuinely missing
    assert by_name["Mana Crypt"].missingCount == 1

    assert detail.totalCards == 7
    assert detail.ownedCards == 5
    assert detail.missingCards == 2

@pytest.mark.asyncio
async def test_get_user_decks_excludes_basic_lands_from_completion():
    from datetime import datetime
    now = datetime.now()

    def make_card(card_id, name, scry_id, mana_cost, qty, assigned, type_line="Creature"):
        card = MagicMock()
        card.id = card_id
        card.deckId = "deck-1"
        card.cardScryfallId = scry_id
        card.cardName = name
        card.quantity = qty
        card.assignedQuantity = assigned
        card.isSideboard = False
        card.isCommander = False
        card.manaCost = mana_cost
        card.typeLine = type_line
        card.imageUri = "https://cards.scryfall.test/img.jpg"
        card.setCode = None
        return card

    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "Azorius Blink"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Brago, King Eternal"
    deck.commanderScryfallId = "scry-brago"
    deck.commanderImageUri = None
    deck.createdAt = now
    deck.updatedAt = now
    deck.cards = [
        make_card("c1", "Island", "scry-island", "", 30, 0, type_line="Basic Land — Island"),
        make_card("c2", "Command Tower", "scry-tower", "", 1, 0, type_line="Land"),
        make_card("c3", "Sol Ring", "scry-sol", "{1}", 1, 0, type_line="Artifact"),
    ]

    # Collection owns both non-basic cards, but none of the basics
    col_tower = MagicMock()
    col_tower.cardName = "Command Tower"
    col_tower.quantity = 1
    col_sol = MagicMock()
    col_sol.cardName = "Sol Ring"
    col_sol.quantity = 1

    col_brago = MagicMock()
    col_brago.cardName = "Brago, King Eternal"
    col_brago.quantity = 1

    mock_db = MagicMock()
    mock_db.deck.find_many = AsyncMock(return_value=[deck])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[col_tower, col_sol, col_brago])

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.PricingService._latest_provider_quote", new_callable=AsyncMock, return_value=None):
        decks = await DeckService.get_user_decks("user-1")

    assert len(decks) == 1
    summary = decks[0]
    # 30 Basics are automatically owned, plus 3 non-basics/commander owned in collection -> 100% completion
    assert summary.totalCards == 33
    assert summary.ownedCards == 33
    assert summary.missingCards == 0
    assert summary.completionPercentage == 100

@pytest.mark.asyncio
async def test_get_deck_detail_excludes_basic_lands_from_completion():
    from datetime import datetime
    now = datetime.now()

    def make_card(card_id, name, scry_id, mana_cost, qty, assigned, type_line="Creature"):
        card = MagicMock()
        card.id = card_id
        card.deckId = "deck-1"
        card.cardScryfallId = scry_id
        card.cardName = name
        card.quantity = qty
        card.assignedQuantity = assigned
        card.isSideboard = False
        card.isCommander = False
        card.manaCost = mana_cost
        card.typeLine = type_line
        card.imageUri = "https://cards.scryfall.test/img.jpg"
        card.setCode = None
        return card

    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "Mono Red"
    deck.format = "Commander"
    deck.description = None
    deck.commander = "Purphoros, God of the Forge"
    deck.commanderScryfallId = "scry-purphoros"
    deck.commanderImageUri = None
    deck.createdAt = now
    deck.updatedAt = now
    deck.cards = [
        make_card("c1", "Mountain", "scry-mtn", "", 25, 0, type_line="Basic Land — Mountain"),
        make_card("c2", "Command Tower", "scry-tower", "", 1, 0, type_line="Land"),
    ]

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=deck)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.update = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.resolve_minio_image_uris", new_callable=AsyncMock, return_value={}), \
         patch("src.services.deck_service.PricingService._latest_provider_quote", new_callable=AsyncMock, return_value=None):
        detail = await DeckService.get_deck_detail("deck-1", "user-1")

    assert detail is not None
    # 25 Mountains + 1 Command Tower + 1 Purphoros (commander) = 27 total cards
    assert detail.totalCards == 27
    assert detail.ownedCards == 25
    assert detail.missingCards == 2

    by_name = {c.cardName: c for c in detail.cards}
    assert by_name["Mountain"].missingCount == 0
    assert by_name["Command Tower"].missingCount == 1
    assert by_name["Purphoros, God of the Forge"].missingCount == 1

@pytest.mark.asyncio
async def test_update_deck_name_and_commander():
    mock_deck = MagicMock()
    mock_deck.id = "deck-1"
    mock_deck.userId = "user-1"
    mock_deck.name = "Original Name"
    mock_deck.format = "Commander"
    mock_deck.commander = "Old Commander"
    mock_deck.cards = []

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=mock_deck)
    mock_db.deck.update = AsyncMock()
    mock_db.deckcard.update_many = AsyncMock()
    mock_db.deckcard.find_first = AsyncMock(return_value=None)
    mock_db.deckcard.create = AsyncMock()

    mock_detail = MagicMock()
    mock_detail.name = "New Name"
    mock_detail.commander = "New Commander"

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.DeckService.get_deck_detail", new_callable=AsyncMock) as mock_get_detail, \
         patch("src.services.deck_service.ScryfallService.get_catalog_card", new_callable=AsyncMock) as mock_resolve, \
         patch("src.services.deck_service.trigger_async_priority_enrichment"):
        mock_get_detail.return_value = mock_detail
        mock_resolve.return_value = {
            "id": "scry-new-cmd",
            "imageUri": "https://cards.scryfall.test/new-cmd.jpg",
            "manaCost": "{1}{U}{R}",
            "typeLine": "Legendary Creature — Dragon",
        }

        update_payload = DeckUpdate(
            name="New Name",
            commander="New Commander",
            description="Updated notes"
        )
        res = await DeckService.update_deck("deck-1", "user-1", update_payload)

        assert res is not None
        assert res.name == "New Name"
        assert res.commander == "New Commander"

        # Verify old commanders were unmarked
        mock_db.deckcard.update_many.assert_awaited_once_with(
            where={"deckId": "deck-1"},
            data={"isCommander": False}
        )

        # Verify new commander card was created with isCommander=True
        mock_db.deckcard.create.assert_awaited_once()
        create_kwargs = mock_db.deckcard.create.call_args[1]["data"]
        assert create_kwargs["cardName"] == "New Commander"
        assert create_kwargs["isCommander"] is True
        assert create_kwargs["cardScryfallId"] == "scry-new-cmd"

        # Verify deck record was updated
        mock_db.deck.update.assert_awaited_once()
        update_args = mock_db.deck.update.call_args[1]["data"]
        assert update_args["name"] == "New Name"
        assert update_args["commander"] == "New Commander"
        assert update_args["description"] == "Updated notes"


@pytest.mark.asyncio
async def test_get_deck_detail_survives_card_enrichment_failure():
    deck = MagicMock()
    deck.id = "deck-1"
    deck.userId = "user-1"
    deck.name = "Resilient Deck"
    deck.format = "Commander"
    deck.description = None
    deck.commander = None
    deck.commanderScryfallId = None
    deck.commanderImageUri = None
    deck.createdAt = deck.updatedAt = __import__("datetime").datetime.now()

    card = MagicMock()
    card.id = "card-1"
    card.deckId = "deck-1"
    card.cardScryfallId = "scryfall-1"
    card.cardName = "Unknown Card"
    card.quantity = 1
    card.assignedQuantity = 0
    card.isSideboard = False
    card.isCommander = False
    card.manaCost = None
    card.typeLine = None
    card.imageUri = None
    card.setCode = None
    deck.cards = [card]

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=deck)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.update = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.resolve_minio_image_uris", new_callable=AsyncMock, return_value={}), \
         patch(
             "src.services.deck_service.ScryfallService.get_or_resolve_catalog_card",
             new_callable=AsyncMock,
             side_effect=RuntimeError("catalog unavailable"),
         ):
        result = await DeckService.get_deck_detail("deck-1", "user-1")

    assert result is not None
    assert result.id == "deck-1"
    assert len(result.cards) == 1
    assert result.cards[0].cardName == "Unknown Card"
    assert result.cards[0].canBeCommander is False


@pytest.mark.asyncio
async def test_import_deck_consolidates_duplicate_export_lines():
    deck = MagicMock(id="deck-1", userId="user-1", name="Imported", format="Commander")
    deck.name = "Imported"
    deck.format = "Commander"
    deck.commander = None
    from datetime import datetime
    deck.createdAt = deck.updatedAt = datetime.now()

    mock_db = MagicMock()
    mock_db.deck.create = AsyncMock(return_value=deck)
    mock_db.deckcard.create = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.ScryfallService.get_catalog_card", new_callable=AsyncMock, return_value=None), \
         patch("src.services.deck_service.trigger_async_priority_enrichment"):
        result = await DeckService.import_deck_text(
            "user-1",
            "Imported",
            "1 Sol Ring\n1 Sol Ring\n// Sideboard\n1 Sol Ring\n",
        )

    assert result.totalCards == 3
    assert mock_db.deckcard.create.await_count == 2
    created = [call.kwargs["data"] for call in mock_db.deckcard.create.await_args_list]
    assert {card["quantity"] for card in created} == {2, 1}


@pytest.mark.asyncio
async def test_get_deck_detail_reports_requested_in_decks():
    from datetime import datetime
    now = datetime.now()

    # Current deck: "Mazo Tidus"
    deck_tidus = MagicMock()
    deck_tidus.id = "deck-tidus"
    deck_tidus.userId = "user-1"
    deck_tidus.name = "Mazo Tidus"
    deck_tidus.format = "Commander"
    deck_tidus.description = None
    deck_tidus.commander = "Tidus"
    deck_tidus.commanderScryfallId = "scry-tidus"
    deck_tidus.commanderImageUri = None
    deck_tidus.createdAt = now
    deck_tidus.updatedAt = now

    card_arcane = MagicMock()
    card_arcane.id = "c-arcane-tidus"
    card_arcane.deckId = "deck-tidus"
    card_arcane.cardScryfallId = "scry-arcane"
    card_arcane.cardName = "Arcane Signet"
    card_arcane.quantity = 1
    card_arcane.assignedQuantity = 0
    card_arcane.isSideboard = False
    card_arcane.isCommander = False
    card_arcane.manaCost = "{2}"
    card_arcane.typeLine = "Artifact"
    card_arcane.imageUri = "https://cards.scryfall.test/arcane.jpg"
    card_arcane.setCode = "c20"

    deck_tidus.cards = [card_arcane]

    # Other user deck cards also asking for Arcane Signet
    deck_urza = MagicMock()
    deck_urza.id = "deck-urza"
    deck_urza.name = "Urza Lord High"
    oc_urza = MagicMock(
        id="c-arcane-urza",
        deckId="deck-urza",
        cardName="Arcane Signet",
        quantity=1,
        assignedQuantity=0,
        deck=deck_urza
    )

    deck_atraxa = MagicMock()
    deck_atraxa.id = "deck-atraxa"
    deck_atraxa.name = "Atraxa Proliferate"
    oc_atraxa = MagicMock(
        id="c-arcane-atraxa",
        deckId="deck-atraxa",
        cardName="Arcane Signet",
        quantity=2,
        assignedQuantity=0,
        deck=deck_atraxa
    )

    mock_db = MagicMock()
    mock_db.deck.find_unique = AsyncMock(return_value=deck_tidus)
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])  # Not in collection -> missing
    mock_db.deckcard.find_many = AsyncMock(return_value=[oc_urza, oc_atraxa])

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.resolve_minio_image_uris", new_callable=AsyncMock, return_value={}), \
         patch("src.services.deck_service.PricingService.get_latest_quotes_batch", new_callable=AsyncMock, return_value={}):
        detail = await DeckService.get_deck_detail("deck-tidus", "user-1")

    assert detail is not None
    assert len(detail.cards) == 2
    signet = next(c for c in detail.cards if c.cardName == "Arcane Signet")
    assert signet.missingCount == 1

    # Should report 3 decks requesting Arcane Signet: Tidus, Atraxa, Urza
    assert signet.requestedInDecksCount == 3
    deck_names = [req.deckName for req in signet.requestedInDecks]
    assert "Mazo Tidus" in deck_names
    assert "Urza Lord High" in deck_names
    assert "Atraxa Proliferate" in deck_names

    # Current deck comes first
    assert signet.requestedInDecks[0].deckId == "deck-tidus"


@pytest.mark.asyncio
async def test_add_missing_card_to_collection_single():
    # Card in deck that is missing
    mock_deck = MagicMock()
    mock_deck.id = "deck-1"
    mock_deck.userId = "user-1"

    mock_card = MagicMock(
        id="card-sol-ring",
        deckId="deck-1",
        cardScryfallId="sol-ring-id",
        cardName="Sol Ring",
        quantity=1,
        assignedQuantity=0,
        manaCost="{1}",
        typeLine="Artifact",
        imageUri="https://example.com/solring.jpg",
        deck=mock_deck,
    )

    mock_db = MagicMock()
    mock_db.deckcard.find_unique = AsyncMock(return_value=mock_card)
    mock_db.deckcard.update = AsyncMock()
    mock_db.collectioncard.find_unique = AsyncMock(return_value=None)
    mock_db.collectioncard.find_first = AsyncMock(return_value=None)
    mock_db.collectioncard.create = AsyncMock()

    # Mock get_deck_detail returning missingCount = 1
    mock_detail_card = MagicMock(id="card-sol-ring", missingCount=1)
    mock_detail = MagicMock(cards=[mock_detail_card])

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.DeckService.get_deck_detail", new_callable=AsyncMock, return_value=mock_detail):
        result = await DeckService.add_missing_card_to_collection("card-sol-ring", "user-1")

    assert result["status"] == "success"
    assert result["addedCount"] == 1

    # Should have created collection card
    mock_db.collectioncard.create.assert_called_once()
    create_args = mock_db.collectioncard.create.call_args[1]["data"]
    assert create_args["cardName"] == "Sol Ring"
    assert create_args["quantity"] == 1

    # Should have assigned card to deck
    mock_db.deckcard.update.assert_called_once_with(
        where={"id": "card-sol-ring"},
        data={"assignedQuantity": 1}
    )


@pytest.mark.asyncio
async def test_add_missing_cards_to_collection_bulk():
    mock_card1 = MagicMock(
        id="c1",
        cardScryfallId="id-1",
        cardName="Arcane Signet",
        missingCount=1,
        assignedQuantity=0,
        quantity=1,
        manaCost="{2}",
        typeLine="Artifact",
        imageUri="https://example.com/signet.jpg",
    )
    mock_card2 = MagicMock(
        id="c2",
        cardScryfallId="id-2",
        cardName="Sol Ring",
        missingCount=2,
        assignedQuantity=0,
        quantity=2,
        manaCost="{1}",
        typeLine="Artifact",
        imageUri="https://example.com/ring.jpg",
    )
    mock_detail = MagicMock(cards=[mock_card1, mock_card2])

    mock_db = MagicMock()
    mock_db.collectioncard.find_unique = AsyncMock(return_value=None)
    mock_db.collectioncard.find_first = AsyncMock(return_value=None)
    mock_db.collectioncard.create = AsyncMock()
    mock_db.deckcard.update = AsyncMock()

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.DeckService.get_deck_detail", new_callable=AsyncMock, return_value=mock_detail):
        result = await DeckService.add_missing_cards_to_collection("deck-test", "user-1")

    assert result["status"] == "success"
    assert result["addedCount"] == 3  # 1 + 2

    # Should have created collection cards for both
    assert mock_db.collectioncard.create.call_count == 2
    # Should have updated assignedQuantity for both deck cards
    assert mock_db.deckcard.update.call_count == 2
    mock_db.deckcard.update.assert_any_call(where={"id": "c1"}, data={"assignedQuantity": 1})
    mock_db.deckcard.update.assert_any_call(where={"id": "c2"}, data={"assignedQuantity": 2})


