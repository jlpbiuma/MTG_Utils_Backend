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
    ) -> PrioritiesResponse:
        prov_info = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        symbol = prov_info["symbol"]

        user_decks = await db.deck.find_many(
            where={"userId": user_id, "isArchived": False},
            include={"cards": True},
        )
        if not user_decks:
            return PrioritiesResponse(
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

        # Calculate non-basic card count per deck for completion gain formula
        deck_non_basics_count: Dict[str, int] = {}
        for d in user_decks:
            deck_non_basics_count[d.id] = sum(
                c.quantity for c in (d.cards or [])
                if not is_basic_land(c.typeLine, c.cardName)
            )
        total_non_basics_all_decks = sum(deck_non_basics_count.values())

        # Group by normalized card name
        grouped: Dict[str, Dict[str, Any]] = {}
        for d in user_decks:
            comp_pct = completions.get(d.id, 0.0)
            colors = deck_colors.get(d.id, [])
            total_nb = deck_non_basics_count.get(d.id, 0)

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
                missing = max(0, c.quantity - (c.assignedQuantity or 0))
                # Potential completion gain: % of the deck's completion that adding these missing copies would add
                potential_gain = round((missing / total_nb) * 100, 1) if total_nb > 0 and missing > 0 else (
                    round((c.quantity / total_nb) * 100, 1) if total_nb > 0 else 0.0
                )

                grouped[norm]["decks"].append(
                    PriorityDeckInfo(
                        deckId=d.id,
                        deckName=d.name,
                        completionPercentage=comp_pct,
                        colors=colors,
                        requestedQuantity=c.quantity,
                        assignedQuantity=c.assignedQuantity or 0,
                        missingQuantity=missing,
                        deckCardId=c.id,
                        potentialGain=potential_gain,
                    )
                )

        # Resolve the cheapest non-zero reprint for every unique card in grouped
        all_norms = list(grouped.keys())
        cheapest_printings_by_norm: Dict[str, Any] = {}
        fallback_printings_by_norm: Dict[str, Any] = {}

        if all_norms:
            all_printings = await db.cardprinting.find_many(
                where={"catalog": {"normalizedName": {"in": all_norms}}},
                include={"catalog": True},
            )
            for p in all_printings:
                cat = getattr(p, "catalog", None)
                norm = getattr(cat, "normalizedName", None) if cat else None
                if not norm or norm not in grouped:
                    continue

                p_price = float(p.priceCardmarketTrend or p.priceEur or 0.0)

                # Keep any valid printing as a fallback
                if norm not in fallback_printings_by_norm:
                    fallback_printings_by_norm[norm] = p

                # Skip 0.0 EUR prices to guarantee non-zero price when available
                if p_price <= 0.0:
                    continue

                existing = cheapest_printings_by_norm.get(norm)
                if existing is None or p_price < existing["price"]:
                    cheapest_printings_by_norm[norm] = {
                        "printing": p,
                        "price": p_price,
                    }

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

            # Fallback: if price is 0.0, find cheapest non-zero print in database
            if unit_price <= 0.0:
                cheapest_p = await db.cardprinting.find_first(
                    where={
                        "catalog": {"normalizedName": norm},
                        "OR": [
                            {"priceCardmarketTrend": {"gt": 0.0}},
                            {"priceEur": {"gt": 0.0}},
                        ],
                    },
                    order={"priceCardmarketTrend": "asc"},
                )
                if cheapest_p:
                    unit_price = cheapest_p.priceCardmarketTrend or cheapest_p.priceEur or 0.0
                    g["cardScryfallId"] = cheapest_p.id
                    if cheapest_p.imageUri or cheapest_p.imageUriLarge:
                        g["imageUri"] = safe_image_uri(cheapest_p.imageUri or cheapest_p.imageUriLarge)

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
        if sort == "impact":
            items.sort(key=lambda x: (x.totalDeficitCost, x.numDecks, x.deficit), reverse=True)
        elif sort == "completion":
            items.sort(key=lambda x: (x.maxDeckCompletion, x.numDecks, x.totalDeficitCost), reverse=True)
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
        )
