"""Project opportunity purchases with the same ownership rules as deck completion."""
from collections import defaultdict
from typing import NamedTuple
from src.schemas.whatsapp_deals import OpportunityDeckImpact
from src.services.card_utils import normalize_card_name, is_basic_land, completion_percentages_by_deck, completion_cards, completion_owned


class CompletionBaseline(NamedTuple):
    """Completion state before any purchase; depends only on the user's data."""
    count: int
    before: float
    before_by_deck: dict
    totals: dict
    owned_totals: dict
    requirements: dict


def completion_baseline(decks, deck_cards, collection) -> CompletionBaseline:
    owned = defaultdict(int)
    for card in collection:
        owned[normalize_card_name(card.cardName)] += card.quantity
    deck_ids = {deck.id for deck in decks}
    cards = completion_cards([card for card in deck_cards if card.deckId in deck_ids])
    before_by_deck = completion_percentages_by_deck(cards, owned)
    count = len(deck_ids)
    before = sum(before_by_deck.values()) / count if count else 0.0
    totals = defaultdict(int)
    owned_totals = defaultdict(int)
    relevant = []
    for index, card in enumerate(cards):
        if getattr(card, "isSideboard", False) or card.quantity <= 0:
            continue
        norm = normalize_card_name(card.cardName)
        basic = is_basic_land(card.typeLine, card.cardName)
        assigned = getattr(card, "assignedQuantity", 0) or 0
        actual_owned = int(completion_owned(card, owned))
        totals[card.deckId] += 1
        owned_totals[card.deckId] += actual_owned
        if not basic:
            relevant.append((index, card, norm, assigned))
    requirements = defaultdict(list)
    for index, card, norm, assigned in relevant:
        requirements[norm].append(OpportunityDeckImpact(
            deck_card_id=str(getattr(card, "id", f"{card.deckId}:{index}")), deck_id=card.deckId,
            total_cards=totals[card.deckId], owned_cards=owned_totals[card.deckId],
            quantity=1, assigned_quantity=int(assigned > 0), collection_quantity=int(owned[norm] > 0),
        ))
    return CompletionBaseline(count, before, before_by_deck, totals, owned_totals, requirements)


def enrich_completion(matches, decks, deck_cards, collection, baseline: CompletionBaseline | None = None):
    """Projects each sale match onto deck completion; reuse ``baseline`` across offers."""
    count, before, before_by_deck, totals, owned_totals, requirements = (
        baseline or completion_baseline(decks, deck_cards, collection))
    for match in matches:
        if match.subject != "vende":
            continue
        match.completion_requirements = list(requirements.get(match.normalized_name, []))
        projected_owned = defaultdict(int)
        for impact in match.completion_requirements:
            old = int(impact.assigned_quantity > 0 or impact.collection_quantity > 0)
            new = int(old > 0 or match.trade_quantity > 0)
            projected_owned[impact.deck_id] += new - old
        gain = sum(
            round((owned_totals[deck_id] + added) / totals[deck_id] * 100, 1) - before_by_deck.get(deck_id, 0)
            for deck_id, added in projected_owned.items()
        ) / count if count else 0.0
        match.completion_gain = round(max(0.0, gain), 2)
        match.global_completion_after = round(min(100.0, before + gain), 2)
    return count, before
