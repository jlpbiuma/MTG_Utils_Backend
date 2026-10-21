from types import SimpleNamespace as NS
from src.schemas.whatsapp_deals import WhatsAppDealMatch
from src.services.opportunity_completion import enrich_completion
from src.services.card_utils import completion_percentages_by_deck


def card(id, deck, name, quantity, assigned=0, sideboard=False, type_line=None):
    return NS(id=id, deckId=deck, cardName=name, quantity=quantity, assignedQuantity=assigned,
              isSideboard=sideboard, typeLine=type_line)


def match(name, quantity=1, subject="vende"):
    return WhatsAppDealMatch(id=1, card_name=name, normalized_name=name.lower(), subject=subject,
                            whatsapp_url="https://manabox.app/decks/x", source_phone="", direct_whatsapp_link="", trade_quantity=quantity)


def test_projects_average_of_all_active_decks_with_basics_owned_and_sideboards_excluded():
    decks = [NS(id=id) for id in ("a", "b", "complete", "empty")]
    cards = [card("a-land", "a", "Forest", 8), card("a-alpha", "a", "Alpha", 2),
             card("a-side", "a", "Alpha", 30, sideboard=True),
             card("b-alpha", "b", "Alpha", 1), card("b-beta", "b", "Beta", 1),
             card("complete-owned", "complete", "Owned", 1), card("archived", "archived", "Alpha", 100)]
    collection = [NS(cardName="Owned", quantity=1)]
    alpha = match("Alpha")
    count, before = enrich_completion([alpha], decks, cards, collection)
    baseline = completion_percentages_by_deck([c for c in cards if c.deckId != "archived"], {"owned": 1})
    assert count == 4 and before == sum(baseline.values()) / 4 == 37.5
    assert alpha.completion_gain == 25 and alpha.global_completion_after == 62.5
    assert {req.deck_card_id for req in alpha.completion_requirements} == {"a-alpha", "b-alpha"}
    alpha.trade_quantity = 20
    enrich_completion([alpha], decks, cards, collection)
    assert alpha.global_completion_after == 62.5  # each requirement is capped, never over 100%


def test_assignments_and_owned_reprints_use_the_existing_completion_formula():
    decks = [NS(id="a")]
    cards = [card("a-alpha", "a", "Alpha", 3, assigned=2)]
    collection = [NS(cardName="ALPHA", quantity=1), NS(cardName="Alpha", quantity=0)]
    alpha = match("Alpha", 1)
    _, before = enrich_completion([alpha], decks, cards, collection)
    assert before == 100 and alpha.completion_gain == 0
    alpha.trade_quantity = 2
    enrich_completion([alpha], decks, cards, collection)
    assert alpha.global_completion_after == 100 and alpha.completion_gain == 0


def test_want_without_deck_demand_and_sales_do_not_inflate_completion():
    wanted, sale = match("Unknown"), match("Owned", subject="compra_busca")
    count, before = enrich_completion([wanted, sale], [NS(id="a")], [card("a", "a", "Owned", 1)], [NS(cardName="Owned", quantity=1)])
    assert count == 1 and before == 100
    assert wanted.global_completion_after == 100 and wanted.completion_gain == 0
    assert sale.global_completion_after is None
    count, before = enrich_completion([match("Unknown")], [], [], [])
    assert count == before == 0


def test_one_copy_covers_seven_requested_copies_in_every_deck():
    decks = [NS(id="a"), NS(id="b")]
    cards = [card(f"{deck}-{index}", deck, f"Card {index}", 7) for deck in ("a", "b") for index in range(100)]
    # A is only in a; B is in both. Sideboard rows (even commanders) never contribute.
    cards[0].cardName = "A"
    cards[1].cardName = cards[101].cardName = "B"
    cards.append(NS(id="side", deckId="b", cardName="A", quantity=999, assignedQuantity=0, isSideboard=True, isCommander=True, typeLine=None))
    a, b = match("A"), match("B")
    count, before = enrich_completion([a, b], decks, cards, [])
    assert count == 2 and before == 0
    assert a.completion_gain == 0.5 and a.global_completion_after == 0.5
    assert b.completion_gain == 1 and b.global_completion_after == 1
    assert completion_percentages_by_deck(cards, {"b": 1}) == {"a": 1, "b": 1}
    assert completion_percentages_by_deck(cards, {"a": 1, "b": 1}) == {"a": 2, "b": 1}
    b.trade_quantity = 7
    enrich_completion([b], decks, cards, [])
    assert b.completion_gain == 1


def test_same_name_in_different_printings_counts_once_and_empty_sideboard_is_zero():
    cards = [card("1", "a", "Fire // Ice", 7), card("2", "a", " FIRE / Ice ", 1), card("3", "b", "Side", 100, sideboard=True)]
    assert completion_percentages_by_deck(cards, {"fire": 1}) == {"a": 100}
