import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone

from src.main import app
from src.services.edhrec_service import EdhrecService
from src.services.deck_service import DeckService
from src.schemas.edhrec import (
    CommanderTypeBreakdown,
    CommanderTypeOwnership,
    CommanderRecommendationsListResponse,
)


@pytest.fixture(autouse=True)
def local_price_quotes():
    with patch("src.services.edhrec_deck_service.PricingService.get_latest_quotes_batch",
               new_callable=AsyncMock, return_value={}):
        yield


@pytest.mark.asyncio
async def test_commander_recommendations_completion():
    user_id = "user-123"
    col_card1 = MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Elvish Archdruid", quantity=1)
    col_card2 = MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Priest of Titania", quantity=1)
    col_card3 = MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Assassin's Trophy", quantity=1)

    mock_cmd = MagicMock()
    mock_cmd.id = "cmd-1"
    mock_cmd.name = "Lathril, Blade of the Elves"
    mock_cmd.normalizedName = "lathril, blade of the elves"
    mock_cmd.slug = "lathril-blade-of-the-elves"
    mock_cmd.colorIdentity = "BG"
    mock_cmd.isTop100 = True
    mock_cmd.rank = 15
    mock_cmd.numDecks = 15000
    mock_cmd.creatureCount = 3
    mock_cmd.instantCount = 2
    mock_cmd.sorceryCount = 0
    mock_cmd.artifactCount = 0
    mock_cmd.enchantmentCount = 0
    mock_cmd.battleCount = 0
    mock_cmd.planeswalkerCount = 0
    mock_cmd.landCount = 35
    mock_cmd.basicLandCount = 10
    mock_cmd.nonbasicLandCount = 0
    mock_cmd.cardsJson = {
        "creatures": [
            {"normalizedName": "elvish archdruid", "name": "Elvish Archdruid"},
            {"normalizedName": "priest of titania", "name": "Priest of Titania"},
            {"normalizedName": "marwyn, the nurturer", "name": "Marwyn, the Nurturer"},
        ],
        "instants": [
            {"normalizedName": "assassin's trophy", "name": "Assassin's Trophy"},
            {"normalizedName": "heroic intervention", "name": "Heroic Intervention"},
        ],
    }
    mock_cmd.canonicalCardNames = [
        "elvish archdruid",
        "priest of titania",
        "marwyn, the nurturer",
        "assassin's trophy",
        "heroic intervention",
    ]

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.deck.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[col_card1, col_card2, col_card3])
    mock_db.edhreccommander.find_many = AsyncMock(return_value=[mock_cmd])

    with patch("src.services.edhrec_service.db", mock_db):
        res = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id)

        assert isinstance(res, CommanderRecommendationsListResponse)
        assert res.total == 1
        cmd_res = res.commanders[0]
        assert cmd_res.name == "Lathril, Blade of the Elves"
        assert cmd_res.isTop100 is True
        assert cmd_res.edhrecRank == 15

        # Quotas: 3 creatures (user owns 2), 2 instants (user owns 1)
        # Total required = 3 + 2 = 5 cards. Total owned = 2 + 1 = 3 cards.
        assert cmd_res.typeOwnership.creaturesOwned == 2
        assert cmd_res.typeOwnership.creaturesTotal == 3
        assert cmd_res.typeOwnership.instantsOwned == 1
        assert cmd_res.typeOwnership.instantsTotal == 2
        assert cmd_res.ownedCardsCount == 3
        assert cmd_res.totalRequiredCards == 5
        # 3 / 5 = 60.0%
        assert cmd_res.completionPercentage == 60.0


@pytest.mark.asyncio
async def test_commander_recommendations_filtering_and_sorting():
    user_id = "user-123"

    cmd1 = MagicMock(
        id="c1",
        normalizedName="atraxa, praetors' voice",
        slug="atraxa-praetors-voice",
        colorIdentity="WUBG",
        isTop100=True,
        rank=1,
        numDecks=30000,
        creatureCount=10,
        instantCount=10,
        sorceryCount=0,
        artifactCount=0,
        enchantmentCount=0,
        battleCount=0,
        planeswalkerCount=0,
        landCount=35,
        basicLandCount=10,
        nonbasicLandCount=0,
        cardsJson={},
        canonicalCardNames=["card_a"],
    )
    cmd1.name = "Atraxa, Praetors' Voice"

    cmd2 = MagicMock(
        id="c2",
        normalizedName="krenko, mob boss",
        slug="krenko-mob-boss",
        colorIdentity="R",
        isTop100=False,
        rank=None,
        numDecks=12000,
        creatureCount=10,
        instantCount=0,
        sorceryCount=0,
        artifactCount=0,
        enchantmentCount=0,
        battleCount=0,
        planeswalkerCount=0,
        landCount=35,
        basicLandCount=10,
        nonbasicLandCount=0,
        cardsJson={},
        canonicalCardNames=["card_b"],
    )
    cmd2.name = "Krenko, Mob Boss"

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.deck.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="card_b", quantity=1),
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Krenko, Mob Boss", quantity=1),
    ])
    mock_db.edhreccommander.find_many = AsyncMock(return_value=[cmd1, cmd2])

    with patch("src.services.edhrec_service.db", mock_db):
        # 1. Default sort by completion desc: Krenko (100%) should be first, Atraxa (0%) second
        res_comp = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="completion")
        assert res_comp.commanders[0].name == "Krenko, Mob Boss"
        assert res_comp.commanders[1].name == "Atraxa, Praetors' Voice"

        # 2. Sort by rank: Atraxa (rank 1) should be first
        res_rank = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="rank")
        assert res_rank.commanders[0].name == "Atraxa, Praetors' Voice"

        # 3. Filter by owned_commander_only
        res_owned = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, owned_commander_only=True)
        assert len(res_owned.commanders) == 1
        assert res_owned.commanders[0].name == "Krenko, Mob Boss"

        # 4. Filter by search
        res_search = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, search="Atraxa")
        assert len(res_search.commanders) == 1
        assert res_search.commanders[0].name == "Atraxa, Praetors' Voice"


@pytest.mark.asyncio
async def test_deck_summary_top100_badge():
    user_id = "user-123"

    deck1 = MagicMock()
    deck1.id = "d1"
    deck1.userId = user_id
    deck1.name = "My Atraxa Deck"
    deck1.format = "Commander"
    deck1.description = None
    deck1.commander = "Atraxa, Praetors' Voice"
    deck1.commanderScryfallId = "cmd-1"
    deck1.commanderImageUri = None
    deck1.tags = ""
    deck1.isArchived = False
    deck1.createdAt = datetime.now(timezone.utc)
    deck1.updatedAt = datetime.now(timezone.utc)
    deck1.cards = []

    deck2 = MagicMock()
    deck2.id = "d2"
    deck2.userId = user_id
    deck2.name = "Obscure Elf Deck"
    deck2.format = "Commander"
    deck2.description = None
    deck2.commander = "Unknown Commander"
    deck2.commanderScryfallId = "cmd-2"
    deck2.commanderImageUri = None
    deck2.tags = ""
    deck2.isArchived = False
    deck2.createdAt = datetime.now(timezone.utc)
    deck2.updatedAt = datetime.now(timezone.utc)
    deck2.cards = []

    mock_top100_card = MagicMock()
    mock_top100_card.normalizedName = "atraxa, praetors' voice"
    mock_top100_card.rank = 1

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.deck.find_many = AsyncMock(return_value=[deck1, deck2])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[])
    mock_db.edhreccommander.find_many = AsyncMock(return_value=[mock_top100_card])

    with patch("src.services.deck_service.db", mock_db), \
         patch("src.services.deck_service.PricingService.get_latest_quotes_batch", new_callable=AsyncMock, return_value={}):
        summaries = await DeckService.get_user_decks(user_id)

        assert len(summaries) == 2
        d1_summary = summaries[0]
        assert d1_summary.name == "My Atraxa Deck"
        assert d1_summary.isCommanderTop100 is True
        assert d1_summary.commanderEdhrecRank == 1

        d2_summary = summaries[1]
        assert d2_summary.name == "Obscure Elf Deck"
        assert d2_summary.isCommanderTop100 is False
        assert d2_summary.commanderEdhrecRank is None


@pytest.mark.asyncio
async def test_api_edhrec_recommendations_endpoint():
    transport = ASGITransport(app=app)
    with patch("src.routers.edhrec.EdhrecService.get_commander_recommendations_for_user", new_callable=AsyncMock) as mock_rec:
        mock_rec.return_value = CommanderRecommendationsListResponse(
            total=1,
            page=1,
            pageSize=24,
            totalPages=1,
            commanders=[],
        )

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/edhrec/commanders")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["page"] == 1
            assert "commanders" in data


def recommendation(name, **overrides):
    from types import SimpleNamespace
    values = dict(
        id=name, name=name, normalizedName=name.lower(), slug=name.lower(),
        colorIdentity="G", isTop100=False, rank=None, numDecks=100,
        creatureCount=0, instantCount=0, sorceryCount=0, artifactCount=0,
        enchantmentCount=0, battleCount=0, planeswalkerCount=0,
        landCount=0, basicLandCount=0, nonbasicLandCount=0,
        cardsJson={}, canonicalCardNames=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_existing_commanders_excluded_before_pagination_and_scoped_to_user():
    from types import SimpleNamespace
    database = MagicMock()
    database.cardprinting.find_many = AsyncMock(return_value=[])
    database.deck.find_many = AsyncMock(return_value=[
        SimpleNamespace(commander="  ATRAXA, Praetors' Voice  ", isArchived=True),
        SimpleNamespace(commander=None),
    ])
    database.deckcard.find_many = AsyncMock(return_value=[
        SimpleNamespace(cardName="Esika, God of the Tree // The Prismatic Bridge"),
        SimpleNamespace(cardName="Tymna the Weaver"),
    ])
    database.collectioncard.find_many = AsyncMock(return_value=[])
    database.edhreccommander.find_many = AsyncMock(return_value=[
        recommendation("Atraxa, Praetors' Voice"), recommendation("Esika, God of the Tree"),
        recommendation("Tymna the Weaver"), recommendation("Krenko, Mob Boss"),
        recommendation("Lathril, Blade of the Elves"),
    ])
    with patch("src.services.edhrec_service.db", database):
        result = await EdhrecService.get_commander_recommendations_for_user(
            "user-1", page_size=1, page=2, sort_by="name"
        )
    assert result.total == 2
    assert result.totalPages == 2
    assert result.commanders[0].name == "Lathril, Blade of the Elves"
    database.deck.find_many.assert_awaited_once_with(where={"userId": "user-1"})
    database.deckcard.find_many.assert_awaited_once_with(
        where={"deck": {"userId": "user-1"}, "isCommander": True}
    )
    assert database.edhreccommander.find_many.call_args.kwargs["where"] == {"status": "synced"}


@pytest.mark.asyncio
async def test_completion_uses_all_recommendations_with_quotas_and_unique_owned_cards():
    database = MagicMock()
    database.cardprinting.find_many = AsyncMock(return_value=[])
    database.deck.find_many = AsyncMock(return_value=[])
    database.deckcard.find_many = AsyncMock(return_value=[])
    database.collectioncard.find_many = AsyncMock(return_value=[
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Late Elf", quantity=1),
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Late Elf", quantity=4),  # another printing
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Mana Rock", quantity=1),
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Extra Rock", quantity=1),
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Forest", quantity=100),
        MagicMock(isFoil=False, cardScryfallId="test-print", cardName="Missing", quantity=0),
    ])
    database.edhreccommander.find_many = AsyncMock(return_value=[recommendation(
        "Lathril", creatureCount=2, artifactCount=1, nonbasicLandCount=1,
        cardsJson={
            "creatures": [{"name": n} for n in ["First Elf", "Second Elf", "Late Elf", "Late Elf", "Missing"]],
            "utilityartifacts": [{"name": "Unowned Rock"}],
            "manaartifacts": [{"name": "Mana Rock"}, {"name": "Extra Rock"}],
            "instants": [{"name": "Late Elf"}],  # zero quota
            "lands": [{"name": "Forest"}, {"name": "Command Tower"}],
        },
    )])
    with patch("src.services.edhrec_service.db", database):
        result = await EdhrecService.get_commander_recommendations_for_user("user-1")
    commander = result.commanders[0]
    assert commander.typeOwnership.creaturesOwned == 1
    assert commander.typeOwnership.artifactsOwned == 1
    assert commander.typeOwnership.instantsOwned == 0
    assert commander.typeOwnership.nonbasicLandsOwned == 0
    assert commander.totalRequiredCards == 4
    assert commander.ownedCardsCount == 2
    assert commander.completionPercentage == 50


@pytest.mark.asyncio
async def test_commander_recommendations_new_sorting_modes():
    user_id = "user-1"

    cmd1 = MagicMock(
        id="c1",
        normalizedName="commander one",
        slug="commander-one",
        colorIdentity="W",
        isTop100=False,
        rank=10,
        numDecks=1000,
        creatureCount=1,
        instantCount=0,
        sorceryCount=0,
        artifactCount=0,
        enchantmentCount=0,
        battleCount=0,
        planeswalkerCount=0,
        landCount=0,
        basicLandCount=0,
        nonbasicLandCount=0,
        cardsJson={
            "creatures": [{"name": "Owned Card 1"}],
            "highsynergycards": [{"name": "Owned Card 1"}],
            "topcards": [{"name": "Unowned Card 1"}],
        },
        canonicalCardNames=["Owned Card 1"],
    )
    cmd1.name = "Commander One"

    cmd2 = MagicMock(
        id="c2",
        normalizedName="commander two",
        slug="commander-two",
        colorIdentity="U",
        isTop100=False,
        rank=20,
        numDecks=2000,
        creatureCount=1,
        instantCount=0,
        sorceryCount=0,
        artifactCount=0,
        enchantmentCount=0,
        battleCount=0,
        planeswalkerCount=0,
        landCount=0,
        basicLandCount=0,
        nonbasicLandCount=0,
        cardsJson={
            "creatures": [{"name": "Owned Card 2"}],
            "highsynergycards": [{"name": "Unowned Card 2"}],
            "topcards": [{"name": "Owned Card 2"}],
        },
        canonicalCardNames=["Owned Card 2"],
    )
    cmd2.name = "Commander Two"

    mock_db = MagicMock()
    mock_db.cardprinting.find_many = AsyncMock(return_value=[])
    mock_db.deck.find_many = AsyncMock(return_value=[])
    mock_db.deckcard.find_many = AsyncMock(return_value=[])
    mock_db.collectioncard.find_many = AsyncMock(return_value=[
        MagicMock(isFoil=False, cardScryfallId="p1", cardName="Owned Card 1", quantity=1),
        MagicMock(isFoil=False, cardScryfallId="p2", cardName="Owned Card 2", quantity=1),
    ])
    mock_db.edhreccommander.find_many = AsyncMock(return_value=[cmd1, cmd2])

    from src.schemas.pricing import CardPriceQuote, UnitPriceBreakdown
    mock_quotes = {
        "owned card 1": CardPriceQuote(
            scryfallId="p1", cardName="Owned Card 1",
            unitPrice=UnitPriceBreakdown(trend=10.0), subtotal=10.0,
            lastUpdated=datetime.now(timezone.utc)
        ),
        "owned card 2": CardPriceQuote(
            scryfallId="p2", cardName="Owned Card 2",
            unitPrice=UnitPriceBreakdown(trend=25.0), subtotal=25.0,
            lastUpdated=datetime.now(timezone.utc)
        ),
    }

    with patch("src.services.edhrec_service.db", mock_db), \
         patch("src.services.edhrec_deck_service.recommendation_prices", AsyncMock(return_value=(mock_quotes, {}))):

        # 1. Sort by synergy (desc): Commander One has 100% synergy, Commander Two has 0%
        res_syn = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="synergy")
        assert res_syn.commanders[0].name == "Commander One"
        assert res_syn.commanders[1].name == "Commander Two"

        # Synergy asc: Commander Two first
        res_syn_asc = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="synergy", sort_dir="asc")
        assert res_syn_asc.commanders[0].name == "Commander Two"
        assert res_syn_asc.commanders[1].name == "Commander One"

        # 2. Sort by top_cards (desc): Commander Two has 100% top cards, Commander One has 0%
        res_top = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="top_cards")
        assert res_top.commanders[0].name == "Commander Two"
        assert res_top.commanders[1].name == "Commander One"

        # 3. Sort by owned_value (desc): Commander Two has 25.0 EUR owned value, Commander One has 10.0 EUR
        res_owned = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="owned_value")
        assert res_owned.commanders[0].name == "Commander Two"
        assert res_owned.commanders[1].name == "Commander One"

        # owned_value (asc): Commander One (10 EUR) first
        res_owned_asc = await EdhrecService.get_commander_recommendations_for_user(user_id=user_id, sort_by="owned_value", sort_dir="asc")
        assert res_owned_asc.commanders[0].name == "Commander One"
        assert res_owned_asc.commanders[1].name == "Commander Two"
