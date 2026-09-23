"""Read-only deck previews built from the downloaded EDHREC recommendations."""
from datetime import datetime, timezone

from src.core.db import db
from src.schemas.edhrec import RecommendedDeckResponse, RecommendedDeckCard, RecommendationCoverage
from src.schemas.deck import DeckRequirement, OtherDeckAssignment
from src.schemas.pricing import PriceSummary, CardPriceQuote, UnitPriceBreakdown
from src.services.card_utils import normalize_card_name, is_basic_land, completion_percentages_by_deck, colors_by_deck
from src.services.edhrec_service import get_edhrec_card_image_url
from src.services.pricing_service import PricingService


# The same quotas used in the recommendation list; basics are excluded.
CATEGORIES = [
    (["creatures"], "creatureCount", "Creature"),
    (["instants"], "instantCount", "Instant"),
    (["sorceries"], "sorceryCount", "Sorcery"),
    (["utilityartifacts", "manaartifacts", "artifacts"], "artifactCount", "Artifact"),
    (["enchantments"], "enchantmentCount", "Enchantment"),
    (["planeswalkers"], "planeswalkerCount", "Planeswalker"),
    (["utilitylands", "lands"], "nonbasicLandCount", "Land"),
    (["battles"], "battleCount", "Battle"),
]


def select_recommended_cards(commander, owned_names):
    """Prefer owned recommendations within each quota, then fill by popularity."""
    selected, seen = [], set()
    required = 0
    for tags, count_field, type_line in CATEGORIES:
        quota = max(0, getattr(commander, count_field))
        required += quota
        candidates = {}
        for tag in tags:
            for card in (commander.cardsJson or {}).get(tag, []):
                norm = normalize_card_name(card.get("normalizedName") or card.get("name"))
                if norm and norm not in seen and not is_basic_land(None, norm):
                    candidates.setdefault(norm, card)
        # Keep owned selection deterministic, matching the summary calculation.
        ordered = sorted(candidates, key=lambda n: (
            n not in owned_names,
            0 if n in owned_names else -candidates[n].get("inclusionPct", 0),
            n,
        ))
        for norm in ordered[:quota]:
            selected.append((norm, candidates[norm], type_line))
            seen.add(norm)
    if not commander.cardsJson:
        for name in commander.canonicalCardNames or []:
            norm = normalize_card_name(name)
            if norm and norm not in seen and not is_basic_land(None, norm):
                selected.append((norm, {"name": name}, "Card"))
                seen.add(norm)
        required = len(selected)
    return selected, required


TYPE_KEYS = dict(zip([t for _, _, t in CATEGORIES], [
    "creatures", "instants", "sorceries", "artifacts", "enchantments",
    "planeswalkers", "lands", "battles",
]))


def with_commander_for_pricing(commander, selected):
    norm = normalize_card_name(commander.name)
    return [(norm, {"name": commander.name}, "Commander")] + [
        item for item in selected if item[0] != norm
    ]


def recommendation_pool(commander):
    """Merge flags/statistics from every group without trimming to type quotas."""
    merged = {}
    for tag, cards in (commander.cardsJson or {}).items():
        for card in cards:
            norm = normalize_card_name(card.get("normalizedName") or card.get("name"))
            if not norm:
                continue
            item = merged.setdefault(norm, {**card, "normalizedName": norm,
                                          "isTopCard": False, "isHighSynergy": False})
            item["isTopCard"] |= tag == "topcards"
            item["isHighSynergy"] |= tag == "highsynergycards"
            item["inclusionPct"] = max(item.get("inclusionPct", 0), card.get("inclusionPct", 0))
            item["synergy"] = max(item.get("synergy", float("-inf")), card.get("synergy", 0))
            if "edhrecCategory" not in item:
                for tags, _, type_line in CATEGORIES:
                    if tag in tags:
                        item["edhrecCategory"] = TYPE_KEYS[type_line]
                        item["fallbackType"] = type_line
                        break
    if not merged:
        for name in commander.canonicalCardNames or []:
            norm = normalize_card_name(name)
            merged.setdefault(norm, {"name": name, "normalizedName": norm})
    return merged


def group_coverage(commander, tag, owned_names):
    names = {normalize_card_name(c.get("normalizedName") or c.get("name"))
             for c in (commander.cardsJson or {}).get(tag, [])}
    names.discard("")
    owned = len(names & owned_names)
    return RecommendationCoverage(owned=owned, total=len(names),
                                  percentage=round(owned / len(names) * 100, 1) if names else None)


def collection_by_name(collection):
    result = {}
    for card in collection:
        if card.quantity > 0:
            result.setdefault(normalize_card_name(card.cardName), []).append(card)
    return result


async def recommendation_prices(candidates, collection, database):
    """Resolve a page of candidates in batches, using exact owned finishes."""
    owned = collection_by_name(collection)
    physical = {norm: min(owned[norm], key=lambda c: (c.isFoil, c.cardScryfallId))
                for norm in candidates if norm in owned}
    ids = list({c.cardScryfallId for c in physical.values()})
    printings = await database.cardprinting.find_many(where={"id": {"in": ids}}) if ids else []
    by_id = {p.id: p for p in printings}
    quotes = await PricingService.get_latest_quotes_batch([
        {"name": card.get("name", norm)} for norm, card in candidates.items() if norm not in owned
    ], "cardmarket", "EUR")
    # Keep only name keys; exact owned prices must not inherit cheapest-printing fallbacks.
    quotes = {norm: quotes[norm] for norm in candidates if norm not in owned and norm in quotes}
    for norm, entry in physical.items():
        printing = by_id.get(entry.cardScryfallId)
        if not printing:
            continue
        price = printing.priceEurFoil if entry.isFoil else (
            printing.priceCardmarketTrend if printing.priceCardmarketTrend is not None else printing.priceEur
        )
        if price is not None:
            quotes[norm] = CardPriceQuote(
                scryfallId=entry.cardScryfallId, cardName=entry.cardName,
                unitPrice=UnitPriceBreakdown(trend=price), subtotal=price,
                lastUpdated=printing.pricesUpdatedAt or datetime.now(timezone.utc),
            )
    return quotes, physical


def estimate_values(selected, required, owned_names, quotes):
    owned_value = missing_value = 0.0
    unpriced = 0
    for norm, _, _ in selected:
        quote = quotes.get(norm)
        if quote is None:
            unpriced += 1
        elif norm in owned_names:
            owned_value += quote.unitPrice.trend
        else:
            missing_value += quote.unitPrice.trend
    return dict(ownedValue=round(owned_value, 2), missingValue=round(missing_value, 2),
                unpricedCards=unpriced, unfilledSlots=max(0, required-len(selected)))


async def get_recommended_deck(slug: str, user_id: str):
    commander = await db.edhreccommander.find_unique(where={"slug": slug})
    if not commander or commander.status != "synced":
        return None
    collection = await db.collectioncard.find_many(where={"userId": user_id})
    owned = collection_by_name(collection)
    selected, required = select_recommended_cards(commander, set(owned))
    pool = recommendation_pool(commander)
    commander_norm = normalize_card_name(commander.name)
    pool[commander_norm] = {**pool.get(commander_norm, {}), "name": commander.name,
                            "edhrecCategory": "commanders", "fallbackType": "Legendary Creature"}
    deck_cards = await db.deckcard.find_many(where={"deck": {"userId": user_id}}, include={"deck": True})
    wants = await db.wantcard.find_many(where={"userId": user_id})
    wanted_names = {normalize_card_name(c.cardName) for c in wants}
    quantities = {norm: sum(c.quantity for c in entries) for norm, entries in owned.items()}
    completion = completion_percentages_by_deck(deck_cards, quantities)
    colors = colors_by_deck(deck_cards)
    requests, assignments = {}, {}
    for card in deck_cards:
        norm = normalize_card_name(card.cardName)
        if norm not in pool:
            continue
        deck_name = card.deck.name if card.deck else "Otro mazo"
        if card.deck and card.deck.isArchived:
            deck_name += " (archivado)"
        by_deck = requests.setdefault(norm, {})
        if card.deckId not in by_deck:
            by_deck[card.deckId] = DeckRequirement(deckId=card.deckId, deckName=deck_name,
                quantity=0, completionPercentage=completion.get(card.deckId, 0), colors=colors.get(card.deckId, []))
        by_deck[card.deckId].quantity += card.quantity
        if card.assignedQuantity > 0:
            assigned = assignments.setdefault(norm, {})
            if card.deckId not in assigned:
                assigned[card.deckId] = OtherDeckAssignment(deckId=card.deckId, deckName=deck_name, quantity=0)
            assigned[card.deckId].quantity += card.assignedQuantity
    catalog = await db.cardcatalog.find_many(where={"normalizedName": {"in": list(pool)}})
    catalog_by_name = {c.normalizedName: c for c in catalog}
    quotes, physical = await recommendation_prices(pool, collection, db)
    values = estimate_values(with_commander_for_pricing(commander, selected), required + 1, set(owned), quotes)
    rows = []
    for norm, card in pool.items():
        entry = physical.get(norm)
        cat = catalog_by_name.get(norm)
        quote = quotes.get(norm)
        # EDHREC image ids are not guaranteed to be Scryfall printing ids.
        cid = entry.cardScryfallId if entry else (quote.scryfallId if quote else "")
        type_line = cat.typeLine if cat else card.get("fallbackType")
        category = card.get("edhrecCategory")
        if not category:
            category = next((key for kind, key in TYPE_KEYS.items()
                             if kind.lower() in (type_line or "").lower()), "other")
        rows.append(RecommendedDeckCard(
            id=f"edhrec:{slug}:{norm}", deckId=f"edhrec:{slug}", cardScryfallId=cid or "",
            cardName=card.get("name") or (cat.name if cat else norm), quantity=1,
            assignedQuantity=0, isSideboard=False, isCommander=norm == commander_norm,
            ownedInCollection=sum(c.quantity for c in owned.get(norm, [])),
            availableToAssign=max(0, quantities.get(norm, 0) - sum(a.quantity for a in assignments.get(norm, {}).values())),
            requestedInDecks=list(requests.get(norm, {}).values()), requestedInDecksCount=len(requests.get(norm, {})),
            assignedInOtherDecks=list(assignments.get(norm, {}).values()), isInWant=norm in wanted_names,
            missingCount=0 if entry else 1, manaCost=cat.manaCost if cat else None,
            typeLine=type_line, imageUri=(entry.imageUri if entry else None)
                or (cat.imageUri if cat else None) or get_edhrec_card_image_url(card.get("id", "")),
            setCode=entry.setCode if entry else None, edhrecCategory=category,
            inclusionPct=card.get("inclusionPct", 0), synergy=card.get("synergy", 0),
            isTopCard=card.get("isTopCard", False), isHighSynergy=card.get("isHighSynergy", False),
        ))
    now = datetime.now(timezone.utc)
    summary = PriceSummary(
        provider="cardmarket", currency="EUR", currencySymbol="€", totalCards=required,
        totalNetValue=round(values["ownedValue"] + values["missingValue"], 2),
        totalOwnedValue=values["ownedValue"], totalMissingValue=values["missingValue"],
        quotes=quotes, lastUpdated=now,
    )
    owned_count = sum(norm in owned for norm, _, _ in selected)
    quotas = {TYPE_KEYS[kind]: getattr(commander, field) for _, field, kind in CATEGORIES}
    return RecommendedDeckResponse(
        id=f"edhrec:{slug}", userId=user_id, name=f"Mazo {commander.name}", format="Commander",
        description="Todas las alternativas de EDHREC por tipo. Elige las cartas necesarias para cubrir cada cupo. Los importes estiman una selección que prioriza tu colección; incluyen al comandante y excluyen las tierras básicas.",
        commander=commander.name, commanderImageUri=get_edhrec_card_image_url(commander.id),
        createdAt=commander.createdAt, updatedAt=commander.updatedAt,
        totalCards=required, uniqueCards=len(rows), ownedCards=owned_count,
        missingCards=max(0, required-owned_count), completionPercentage=round(owned_count / required * 100, 1) if required else 0,
        cards=rows, totalValue=summary.totalNetValue, priceSummary=summary, **values,
        typeQuotas=quotas, basicLandQuota=getattr(commander, "basicLandCount", 0),
        colors=[c for c in commander.colorIdentity if c in "WUBRG"],
    )
