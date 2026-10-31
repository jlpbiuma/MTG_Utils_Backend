"""Before/after equivalence for the card rules shared by every screen.

``tests/fixtures/legacy/card_utils_v1.py`` is the implementation before the
multi-face fixes. Every output over the shared corpus must stay identical,
except the bug fixes listed in EXPECTED_CHANGES. A new difference, or a listed
fix that no longer happens, fails the test. The benchmark asserts the new code
is not slower on a collection-sized workload.
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace

from src.services import card_utils as current
from tests.fixtures.legacy import card_utils_v1 as legacy

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "card_semantics_corpus.json").read_text())
CARDS = CORPUS["cards"]

EXPECTED_CHANGES = {
    # Front-face categorization: a battle or a land that transforms.
    ("category", "Invasion of Ikoria"): ("creatures", "battles"),
    ("category", "Invasion of Zendikar"): ("creatures", "battles"),
    ("category", "Westvale Abbey // Ormendahl, Profane Prince"): ("creatures", "lands"),
    # Adventure cards count only the main half; {2/W} costs 2; {HW} costs 0.5.
    ("cmc", "Bonecrusher Giant // Stomp"): (5, 3),
    ("cmc", "Brazen Borrower // Petty Theft"): (5, 3),
    ("cmc", "Spectral Procession"): (3, 6),
    ("cmc", "Reaper King"): (5, 10),
    ("cmc", "Little Girl"): (1, 0.5),
    # Basic lands by exact name: no more always-owned dual lands or creatures.
    ("basic", "Tropical Island"): (True, False),
    ("basic", "Volcanic Island"): (True, False),
    ("basic", "Mountain Goat"): (True, False),
    ("basic", "Island Fish Jasconius"): (True, False),
    ("basic", "Sapseep Forest"): (True, False),
    ("basic", "Swamp Mosquito"): (True, False),
    ("basic", "Isla del tesoro"): (True, False),
    # Without a type line those names were grouped as lands only because of it.
    ("category", "Volcanic Island"): ("lands", "other"),
    ("category", "Mountain Goat"): ("lands", "other"),
    ("category", "Island Fish Jasconius"): ("lands", "other"),
    ("category", "Sapseep Forest"): ("lands", "other"),
    ("category", "Isla del tesoro"): ("lands", "other"),
}


def _outputs(module, card):
    name, cost, type_line = card["name"], card["manaCost"], card["typeLine"]
    cmc = module.extract_cmc(cost, type_line) if module is current else module.extract_cmc(cost)
    return {
        "normalized": module.normalize_card_name(name),
        "category": module.get_card_category(type_line, name),
        "basic": module.is_basic_land(type_line, name),
        "cmc": cmc,
        "colors": module.extract_colors_from_mana_cost(cost),
    }


def test_card_rules_match_legacy_except_documented_fixes():
    changes = {}
    for card in CARDS:
        before, after = _outputs(legacy, card), _outputs(current, card)
        for field, value in before.items():
            if after[field] != value:
                changes[(field, card["name"])] = (value, after[field])
    assert changes == EXPECTED_CHANGES


def _deck_rows():
    by_name = {card["name"]: card for card in CARDS}
    rows = []
    for deck in CORPUS["decks"]:
        for name in deck["cards"]:
            card = by_name.get(name, {"manaCost": None, "typeLine": None})
            rows.append(SimpleNamespace(
                deckId=deck["deckId"], cardName=name, quantity=1, assignedQuantity=0,
                isSideboard=False, manaCost=card["manaCost"], typeLine=card["typeLine"],
            ))
    return rows


def test_completion_and_colors_change_only_through_basic_land_fix():
    rows, owned = _deck_rows(), CORPUS["collection"]
    assert current.colors_by_deck(rows) == legacy.colors_by_deck(rows)
    assert current.completion_cards(rows) == legacy.completion_cards(rows)
    # d1: Tropical Island and Mountain Goat are no longer covered for free;
    # d2: Volcanic Island; d3: Sapseep Forest.
    assert legacy.completion_percentages_by_deck(rows, owned) == {"d1": 75.0, "d2": 50.0, "d3": 50.0}
    assert current.completion_percentages_by_deck(rows, owned) == {"d1": 50.0, "d2": 33.3, "d3": 33.3}

    # Same results as legacy once the fixed names are really owned.
    fixed_owned = {**owned, "tropical island": 1, "mountain goat": 1, "volcanic island": 1, "sapseep forest": 1}
    assert current.completion_percentages_by_deck(rows, fixed_owned) == legacy.completion_percentages_by_deck(rows, fixed_owned)


def _best_of(fn, repeat=5):
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def test_completion_workload_is_not_slower_than_legacy():
    """Collection-sized pass: 6,000 deck rows across 40 decks."""
    rows = [
        SimpleNamespace(
            deckId=f"deck-{index % 40}", cardName=f"Generated Card {index % 1500}",
            quantity=1, assignedQuantity=0, isSideboard=False, manaCost="{1}{G}", typeLine="Creature",
        )
        for index in range(6000)
    ]
    owned = {current.normalize_card_name(row.cardName): 1 for row in rows[::3]}

    before = _best_of(lambda: legacy.completion_percentages_by_deck(rows, owned))
    after = _best_of(lambda: current.completion_percentages_by_deck(rows, owned))
    print(f"\ncompletion 6000 rows: legacy {before * 1000:.1f} ms -> current {after * 1000:.1f} ms ({before / after:.1f}x)")
    assert current.completion_percentages_by_deck(rows, owned) == legacy.completion_percentages_by_deck(rows, owned)
    assert after <= before
