from src.services.priority_price_signals import enrich_price_signals
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.services.card_utils import (
    normalize_card_name,
    is_basic_land,
    completion_percentages_by_deck,
    colors_by_deck,
    get_card_category,
)
from src.services.image_resolver import safe_image_uri
from src.services.pricing_service import PricingService, PRICE_PROVIDERS
from src.schemas.priorities import (
    PriorityItem,
    PriorityDeckInfo,
    DeckReassignOption,
    PrioritiesResponse,
    GoldenWantsResponse,
    GoldenWantsCartItem,
    GoldenWantsTargetDeck,
    CompletedDeckInfo,
    ProjectedDeckProgress,
)

class PriorityService:
    @staticmethod
    async def get_priorities(
        user_id: str,
        sort: str = "demand",
        reassignable_only: bool = False,
        hide_owned: bool = False,
        card_type: Optional[str] = None,
        page: int = 1,
        limit: int = 30,
        provider: str = "cardmarket",
        include_price_signals: bool = False,
        price_window_days: int = 30,
    ) -> PrioritiesResponse:
        prov_info = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        symbol = prov_info["symbol"]

        user_decks = await db.deck.find_many(
            where={"userId": user_id, "isArchived": False},
            include={"cards": True},
        )
        if not user_decks:
            return PrioritiesResponse(
                priceWindowDays=price_window_days,
                totalUniqueCards=0,
                totalDeficitCopies=0,
                totalDeficitCost=0.0,
                currencySymbol=symbol,
                provider=provider,
                page=page,
                limit=limit,
                totalItems=0,
                hasMore=False,
                items=[],
            )

        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map: Dict[str, int] = {}
        for c in col_cards:
            norm = normalize_card_name(c.cardName)
            col_map[norm] = col_map.get(norm, 0) + c.quantity

        all_deck_cards = [card for d in user_decks for card in (d.cards or [])]
        completions = completion_percentages_by_deck(all_deck_cards, col_map)
        deck_colors = colors_by_deck(all_deck_cards)

        # Calculate deck stats (total cards, owned cards, missing cards)
        deck_stats: Dict[str, Dict[str, int]] = {}
        for d in user_decks:
            total = 0
            owned = 0
            for c in (d.cards or []):
                if c.isSideboard and not getattr(c, "isCommander", False):
                    continue
                qty = c.quantity or 0
                if qty <= 0:
                    continue
                total += qty
                norm = normalize_card_name(c.cardName)
                if is_basic_land(c.typeLine, c.cardName):
                    owned += qty
                else:
                    assigned = getattr(c, "assignedQuantity", 0) or 0
                    in_col = col_map.get(norm, 0)
                    actual_owned = min(qty, max(assigned, min(in_col, qty)))
                    owned += actual_owned

            missing = max(0, total - owned)
            comp = completions.get(d.id, round((owned / total) * 100, 1) if total else 0.0)
            deck_stats[d.id] = {
                "totalCards": total,
                "ownedCards": owned,
                "missingCards": missing,
                "completion": comp,
            }

        total_non_basics_all_decks = sum(
            sum(c.quantity for c in (d.cards or []) if not is_basic_land(c.typeLine, c.cardName))
            for d in user_decks
        )

        # Group by normalized card name
        grouped: Dict[str, Dict[str, Any]] = {}
        for d in user_decks:
            colors = deck_colors.get(d.id, [])
            d_stat = deck_stats.get(d.id, {"totalCards": 0, "ownedCards": 0, "missingCards": 0, "completion": 0.0})
            comp_pct = d_stat["completion"]
            total_deck_cards = d_stat["totalCards"]
            missing_deck_cards = d_stat["missingCards"]

            for c in (d.cards or []):
                if is_basic_land(c.typeLine, c.cardName):
                    continue
                norm = normalize_card_name(c.cardName)
                if norm not in grouped:
                    grouped[norm] = {
                        "cardName": c.cardName,
                        "cardScryfallId": c.cardScryfallId,
                        "imageUri": safe_image_uri(c.imageUri),
                        "manaCost": c.manaCost,
                        "typeLine": c.typeLine,
                        "decks": [],
                    }
                assigned = getattr(c, "assignedQuantity", 0) or 0
                in_col = col_map.get(norm, 0)
                actual_owned = min(c.quantity, max(assigned, min(in_col, c.quantity)))
                missing = max(0, c.quantity - actual_owned)

                potential_gain = (
                    round((missing / total_deck_cards) * 100, 1)
                    if total_deck_cards > 0 and missing > 0
                    else 0.0
                )

                grouped[norm]["decks"].append(
                    PriorityDeckInfo(
                        deckId=d.id,
                        deckName=d.name,
                        completionPercentage=comp_pct,
                        colors=colors,
                        requestedQuantity=c.quantity,
                        assignedQuantity=assigned,
                        missingQuantity=missing,
                        deckCardId=c.id,
                        potentialGain=potential_gain,
                        deckTotalCards=total_deck_cards,
                        deckMissingCards=missing_deck_cards,
                    )
                )

        # Resolve the cheapest non-zero reprint for every unique card in grouped
        all_norms = list(grouped.keys())
        cheapest_printings_by_norm: Dict[str, Any] = {}
        fallback_printings_by_norm: Dict[str, Any] = {}

        if all_norms:
            by_norm = await PricingService._cheapest_printings_by_norm(all_norms)
            for norm, p in by_norm.items():
                if norm not in grouped:
                    continue
                p_price = float(p.priceCardmarketTrend or p.priceEur or 0.0)
                fallback_printings_by_norm[norm] = p
                if p_price > 0.0:
                    cheapest_printings_by_norm[norm] = {"printing": p, "price": p_price}

        # Override cardScryfallId and imageUri with the cheapest reprint by default
        for norm, g in grouped.items():
            cheapest_info = cheapest_printings_by_norm.get(norm)
            if cheapest_info:
                p = cheapest_info["printing"]
                g["cardScryfallId"] = p.id
                img = p.imageUri or p.imageUriLarge or p.imageUriSmall
                if img:
                    g["imageUri"] = safe_image_uri(img)
                g["cheapestPrice"] = cheapest_info["price"]
            elif norm in fallback_printings_by_norm:
                p = fallback_printings_by_norm[norm]
                if not g.get("cardScryfallId"):
                    g["cardScryfallId"] = p.id
                img = p.imageUri or p.imageUriLarge or p.imageUriSmall
                if img and not g.get("imageUri"):
                    g["imageUri"] = safe_image_uri(img)

        cards_to_price: List[Dict[str, Any]] = []
        for norm, g in grouped.items():
            cards_to_price.append({
                "name": g["cardName"],
                "scryfallId": g["cardScryfallId"],
                "quantity": 1,
            })

        price_quotes = {}
        if cards_to_price:
            try:
                price_summary = await PricingService.get_price_summary(
                    cards=cards_to_price,
                    provider=provider,
                    bypass_cache=False,
                )
                price_quotes = price_summary.quotes
            except Exception:
                price_quotes = {}

        items: List[PriorityItem] = []
        for norm, g in grouped.items():
            decks_info: List[PriorityDeckInfo] = g["decks"]
            copies_needed = sum(di.requestedQuantity for di in decks_info)
            copies_owned = col_map.get(norm, 0)
            deficit = max(0, copies_needed - copies_owned)

            # Check reassign options
            reassign_opts: List[DeckReassignOption] = []
            assigned_decks = [di for di in decks_info if di.assignedQuantity > 0]
            missing_decks = [di for di in decks_info if di.missingQuantity > 0]

            for s in assigned_decks:
                for t in missing_decks:
                    if s.deckId != t.deckId:
                        reassign_opts.append(
                            DeckReassignOption(
                                sourceDeckId=s.deckId,
                                sourceDeckName=s.deckName,
                                sourceDeckCompletion=s.completionPercentage,
                                assignedQuantity=s.assignedQuantity,
                                targetDeckId=t.deckId,
                                targetDeckName=t.deckName,
                                targetDeckCompletion=t.completionPercentage,
                                missingQuantity=t.missingQuantity,
                                targetDeckCardId=t.deckCardId,
                            )
                        )

            is_reassignable = len(reassign_opts) > 0 and copies_owned > 0

            quote = price_quotes.get(g["cardScryfallId"]) or price_quotes.get(norm)
            unit_price = quote.unitPrice.trend if quote else 0.0

            if unit_price <= 0.0 and "cheapestPrice" in g:
                unit_price = g["cheapestPrice"]



            card_scryfall_id = g["cardScryfallId"]
            if quote and quote.scryfallId:
                card_scryfall_id = quote.scryfallId

            total_cost = round(deficit * unit_price, 2)
            max_comp = max((di.completionPercentage for di in decks_info), default=0.0)
            max_gain = max((di.potentialGain for di in decks_info), default=0.0)
            avg_gain = round(sum(di.potentialGain for di in decks_info) / max(1, len(decks_info)), 1)

            # Net completion gain for ALL decks combined
            net_gain = round((deficit / total_non_basics_all_decks) * 100, 2) if total_non_basics_all_decks > 0 else 0.0
            sum_pts = round(sum(di.potentialGain for di in decks_info), 1)

            card_cat = get_card_category(g["typeLine"], g["cardName"])

            items.append(
                PriorityItem(
                    cardName=g["cardName"],
                    cardScryfallId=card_scryfall_id,
                    imageUri=g["imageUri"],
                    manaCost=g["manaCost"],
                    typeLine=g["typeLine"],
                    cardType=card_cat,
                    numDecks=len(decks_info),
                    decks=decks_info,
                    copiesOwned=copies_owned,
                    copiesNeeded=copies_needed,
                    deficit=deficit,
                    price=round(unit_price, 2),
                    totalDeficitCost=total_cost,
                    isReassignable=is_reassignable,
                    reassignOptions=reassign_opts,
                    maxDeckCompletion=max_comp,
                    maxPotentialGain=max_gain,
                    avgPotentialGain=avg_gain,
                    netCompletionGain=net_gain,
                    sumPointsGain=sum_pts,
                )
            )

        if sort == "price_opportunity":
            await enrich_price_signals(items, provider, prov_info["currency"], price_window_days)

        # Filters
        if reassignable_only:
            items = [item for item in items if item.isReassignable]
        elif hide_owned:
            # Filter out cards already in the collection (copiesOwned > 0)
            items = [item for item in items if item.copiesOwned == 0]

        if card_type and card_type.lower() not in ("all", "todos", ""):
            items = [item for item in items if item.cardType.lower() == card_type.lower()]

        # Sorting:
        # "demand": nº of decks that ask for it desc, deficit desc
        # "impact": deficit * price desc, numDecks desc
        # "completion": maxDeckCompletion desc (almost finished decks first)
        # Sorting:
        # "demand": nº of decks that ask for it desc, deficit desc
        # "impact": deficit * price desc, numDecks desc
        # "completion": maxDeckCompletion desc (almost finished decks first)
        # "complete_decks": cards that close decks closest to 100% first, cheaper cards first
        if sort == "price_opportunity":
            items.sort(key=lambda x: (not x.atHistoricalLow, x.priceChangePercent is None or x.priceChangePercent >= 0, x.priceChangePercent if x.priceChangePercent is not None else float("inf"), -x.sumPointsGain, x.price))
        elif sort == "impact":
            items.sort(key=lambda x: (x.totalDeficitCost, x.numDecks, x.deficit), reverse=True)
        elif sort == "completion":
            items.sort(key=lambda x: (x.maxDeckCompletion, x.numDecks, x.totalDeficitCost), reverse=True)
        elif sort == "complete_decks":
            items.sort(
                key=lambda x: (
                    x.maxDeckCompletion,
                    -x.price if x.price > 0 else -999.0,
                    x.numDecks,
                    -x.totalDeficitCost,
                ),
                reverse=True,
            )
        else:  # demand
            items.sort(key=lambda x: (x.numDecks, x.totalDeficitCost, x.deficit), reverse=True)

        total_unique = len(items)
        total_deficit_copies = sum(item.deficit for item in items)
        total_deficit_cost = round(sum(item.totalDeficitCost for item in items), 2)

        # Pagination & partial fetch
        page_val = max(1, page)
        limit_val = max(1, min(200, limit))
        start = (page_val - 1) * limit_val
        end = start + limit_val
        paged_items = items[start:end]
        if include_price_signals and sort != "price_opportunity":
            await enrich_price_signals(paged_items, provider, prov_info["currency"], price_window_days)
        has_more = end < total_unique

        return PrioritiesResponse(
            totalUniqueCards=total_unique,
            totalDeficitCopies=total_deficit_copies,
            totalDeficitCost=total_deficit_cost,
            currencySymbol=symbol,
            provider=provider,
            page=page_val,
            limit=limit_val,
            totalItems=total_unique,
            hasMore=has_more,
            items=paged_items,
            priceWindowDays=price_window_days,
            globalDeckCount=len(user_decks),
            globalCompletionBefore=sum(d["completion"] for d in deck_stats.values()) / len(user_decks),
        )

    @staticmethod
    async def get_golden_wants(
        user_id: str,
        budget: float = 50.0,
        strategy: str = "complete_decks",
        provider: str = "cardmarket",
    ) -> GoldenWantsResponse:
        budget_cents = max(0, int(round(budget * 100)))
        if budget_cents <= 0:
            return GoldenWantsResponse(
                cart=[],
                totalCost=0.0,
                budgetRemaining=budget,
                completedDecks=[],
                projectedProgress=[],
                totalCardsToBuy=0,
            )

        # 1. Fetch user decks & collection
        user_decks = await db.deck.find_many(
            where={"userId": user_id, "isArchived": False},
            include={"cards": True},
        )
        if not user_decks:
            return GoldenWantsResponse(
                cart=[],
                totalCost=0.0,
                budgetRemaining=budget,
                completedDecks=[],
                projectedProgress=[],
                totalCardsToBuy=0,
            )

        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map: Dict[str, int] = {}
        for c in col_cards:
            norm = normalize_card_name(c.cardName)
            col_map[norm] = col_map.get(norm, 0) + c.quantity

        all_deck_cards = [card for d in user_decks for card in (d.cards or [])]
        completions = completion_percentages_by_deck(all_deck_cards, col_map)

        deck_stats: Dict[str, Dict[str, Any]] = {}
        for d in user_decks:
            total = 0
            owned = 0
            for c in (d.cards or []):
                if c.isSideboard and not getattr(c, "isCommander", False):
                    continue
                qty = c.quantity or 0
                if qty <= 0:
                    continue
                total += qty
                norm = normalize_card_name(c.cardName)
                if is_basic_land(c.typeLine, c.cardName):
                    owned += qty
                else:
                    assigned = getattr(c, "assignedQuantity", 0) or 0
                    in_col = col_map.get(norm, 0)
                    actual_owned = min(qty, max(assigned, min(in_col, qty)))
                    owned += actual_owned

            missing = max(0, total - owned)
            comp = completions.get(d.id, round((owned / total) * 100, 1) if total else 0.0)
            deck_stats[d.id] = {
                "name": d.name,
                "totalCards": total,
                "ownedCards": owned,
                "missingCards": missing,
                "completion": comp,
            }

        # 2. Group candidate missing cards
        grouped: Dict[str, Dict[str, Any]] = {}
        for d in user_decks:
            comp_pct = deck_stats[d.id]["completion"]
            for c in (d.cards or []):
                if is_basic_land(c.typeLine, c.cardName):
                    continue
                norm = normalize_card_name(c.cardName)
                assigned = getattr(c, "assignedQuantity", 0) or 0
                in_col = col_map.get(norm, 0)
                actual_owned = min(c.quantity, max(assigned, min(in_col, c.quantity)))
                missing = max(0, c.quantity - actual_owned)
                if missing <= 0:
                    continue

                if norm not in grouped:
                    grouped[norm] = {
                        "cardName": c.cardName,
                        "cardScryfallId": c.cardScryfallId,
                        "imageUri": safe_image_uri(c.imageUri),
                        "manaCost": c.manaCost,
                        "typeLine": c.typeLine,
                        "targetDecks": [],
                        "totalNeeded": 0,
                    }

                grouped[norm]["totalNeeded"] += c.quantity
                grouped[norm]["targetDecks"].append({
                    "deckId": d.id,
                    "deckName": d.name,
                    "completionBefore": comp_pct,
                    "missingQuantity": missing,
                })

        if not grouped:
            return GoldenWantsResponse(
                cart=[],
                totalCost=0.0,
                budgetRemaining=budget,
                completedDecks=[],
                projectedProgress=[],
                totalCardsToBuy=0,
            )

        # 3. Filter cards with deficit > 0
        candidate_norms = []
        for norm, g in list(grouped.items()):
            copies_owned = col_map.get(norm, 0)
            deficit = max(0, g["totalNeeded"] - copies_owned)
            if deficit <= 0:
                del grouped[norm]
            else:
                candidate_norms.append(norm)

        if not candidate_norms:
            return GoldenWantsResponse(
                cart=[],
                totalCost=0.0,
                budgetRemaining=budget,
                completedDecks=[],
                projectedProgress=[],
                totalCardsToBuy=0,
            )

        # 4. Price candidates with cheapest reprints
        cheapest_printings = await PricingService._cheapest_printings_by_norm(candidate_norms)
        for norm, g in grouped.items():
            p = cheapest_printings.get(norm)
            if p:
                g["cardScryfallId"] = p.id
                img = p.imageUri or p.imageUriLarge or p.imageUriSmall
                if img:
                    g["imageUri"] = safe_image_uri(img)
                price = float(p.priceCardmarketTrend or p.priceEur or 0.0)
                if price > 0:
                    g["cheapestPrice"] = price

        cards_to_price = [
            {"name": g["cardName"], "scryfallId": g["cardScryfallId"], "quantity": 1}
            for g in grouped.values()
        ]
        price_quotes = {}
        if cards_to_price:
            try:
                summary = await PricingService.get_price_summary(
                    cards=cards_to_price,
                    provider=provider,
                    bypass_cache=False,
                )
                price_quotes = summary.quotes
            except Exception:
                price_quotes = {}

        # 5. Build units (1 per unique card)
        units: List[Dict[str, Any]] = []
        for norm, g in grouped.items():
            q = price_quotes.get(g["cardScryfallId"]) or price_quotes.get(norm)
            unit_price = q.unitPrice.trend if q else 0.0
            if unit_price <= 0.0 and "cheapestPrice" in g:
                unit_price = g["cheapestPrice"]
            if unit_price <= 0.0:
                unit_price = 0.25
            cost_cents = max(1, int(round(unit_price * 100)))

            highest_comp = max(d["completionBefore"] for d in g["targetDecks"])
            if strategy == "complete_decks":
                score = ((highest_comp / 10.0) ** 3) * (1 + len(g["targetDecks"]) * 1.5)
            else:
                score = (1.0 / max(0.5, unit_price)) * (1 + len(g["targetDecks"]) * 0.75)

            units.append({
                "norm": norm,
                "cardName": g["cardName"],
                "cardScryfallId": g["cardScryfallId"],
                "imageUri": g["imageUri"],
                "manaCost": g["manaCost"],
                "typeLine": g["typeLine"],
                "unitPrice": round(unit_price, 2),
                "costCents": cost_cents,
                "targetDecks": g["targetDecks"],
                "highestDeckCompletion": highest_comp,
                "priorityScore": score,
            })

        # 6. Solve strategy
        selected_units: List[Dict[str, Any]] = []
        selected_unit_ids = set()
        current_spend_cents = 0

        if strategy == "complete_decks":
            incomplete_decks = [
                (d_id, s) for d_id, s in deck_stats.items()
                if s["completion"] < 100.0 and s["missingCards"] > 0
            ]
            incomplete_decks.sort(
                key=lambda x: (x[1]["completion"], -x[1]["missingCards"]),
                reverse=True,
            )

            # Pass 1: Try to complete decks that CAN be fully finished within budget
            for d_id, s in incomplete_decks:
                deck_missing = s["missingCards"]
                needed = [
                    u for u in units
                    if u["cardScryfallId"] not in selected_unit_ids
                    and any(td["deckId"] == d_id for td in u["targetDecks"])
                ]
                # A deck can ONLY reach 100% if ALL its missing cards exist in candidates
                if len(needed) < deck_missing:
                    continue

                cost_to_finish = sum(u["costCents"] for u in needed)
                if cost_to_finish > 0 and current_spend_cents + cost_to_finish <= budget_cents:
                    for u in needed:
                        selected_unit_ids.add(u["cardScryfallId"])
                        selected_units.append(u)
                    current_spend_cents += cost_to_finish

            # Pass 2: Remaining budget picks cheapest units for closest-to-finish decks
            remaining_units = [u for u in units if u["cardScryfallId"] not in selected_unit_ids]
            remaining_units.sort(
                key=lambda u: (u["highestDeckCompletion"], -u["costCents"]),
                reverse=True,
            )
            for u in remaining_units:
                if current_spend_cents + u["costCents"] <= budget_cents:
                    selected_unit_ids.add(u["cardScryfallId"])
                    selected_units.append(u)
                    current_spend_cents += u["costCents"]
        else:
            # Max completion: greedy by score / cost
            units_sorted = sorted(units, key=lambda u: u["priorityScore"] / u["costCents"], reverse=True)
            for u in units_sorted:
                if current_spend_cents + u["costCents"] <= budget_cents:
                    selected_unit_ids.add(u["cardScryfallId"])
                    selected_units.append(u)
                    current_spend_cents += u["costCents"]

        # 7. Build Cart and Progress
        cart: List[GoldenWantsCartItem] = []
        for u in selected_units:
            cart.append(GoldenWantsCartItem(
                cardName=u["cardName"],
                cardScryfallId=u["cardScryfallId"],
                imageUri=u["imageUri"],
                manaCost=u["manaCost"],
                typeLine=u["typeLine"],
                quantityToBuy=1,
                unitPrice=u["unitPrice"],
                totalCost=u["unitPrice"],
                targetDecks=[
                    GoldenWantsTargetDeck(
                        deckId=td["deckId"],
                        deckName=td["deckName"],
                        completionBefore=td["completionBefore"],
                    )
                    for td in u["targetDecks"]
                ],
            ))
        cart.sort(key=lambda x: x.totalCost, reverse=True)

        fulfilled_per_deck: Dict[str, int] = {}
        for u in selected_units:
            for td in u["targetDecks"]:
                d_id = td["deckId"]
                fulfilled_per_deck[d_id] = fulfilled_per_deck.get(d_id, 0) + 1

        projected_progress: List[ProjectedDeckProgress] = []
        completed_decks: List[CompletedDeckInfo] = []

        for d_id, s in deck_stats.items():
            fulfilled = fulfilled_per_deck.get(d_id, 0)
            true_missing = s["missingCards"]
            total_cards = max(1, s["totalCards"])
            initial_owned = max(0, total_cards - true_missing)
            new_owned = min(total_cards, initial_owned + fulfilled)

            is_completed = true_missing > 0 and fulfilled >= true_missing
            after = 100.0 if is_completed else min(99.9, round((new_owned / total_cards) * 100, 1))
            gained = max(0.0, round(after - s["completion"], 1))

            if is_completed and s["completion"] < 100.0:
                completed_decks.append(CompletedDeckInfo(deckId=d_id, deckName=s["name"]))

            if fulfilled > 0 or (s["completion"] > 0 and s["completion"] < 100.0):
                projected_progress.append(ProjectedDeckProgress(
                    deckId=d_id,
                    deckName=s["name"],
                    before=round(s["completion"], 1),
                    after=after,
                    gainedPercentage=gained,
                    cardsFulfilled=fulfilled,
                    totalMissingInitially=true_missing,
                ))

        projected_progress.sort(key=lambda p: p.after, reverse=True)
        total_cost = round(current_spend_cents / 100.0, 2)
        budget_remaining = max(0.0, round(budget - total_cost, 2))

        return GoldenWantsResponse(
            cart=cart,
            totalCost=total_cost,
            budgetRemaining=budget_remaining,
            completedDecks=completed_decks,
            projectedProgress=projected_progress,
            totalCardsToBuy=len(selected_units),
        )
