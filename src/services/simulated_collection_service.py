from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import re
import asyncio
from src.services.cardmarket_cart_parser import parse_cardmarket_cart, CartImport
from dataclasses import dataclass, replace
from src.services.upcoming_reprints import upcoming_reprints_by_name
from src.services.offer_name_resolver import resolve_offer_names
from src.services.scryfall_service import fold_search_text
from src.services.simulated_purchase_service import compare_purchase
from src.services.price_statistics import enrich_purchase_history, get_history_statistics
from src.schemas.price_statistics import PriceHistoryStatistics
from src.schemas.whatsapp_deals import WhatsAppMatchesResponse, WhatsAppDealMatch, WantMatchInfo, CollectionMatchInfo
from src.schemas.import_export import ParsedCardEntry
from src.core.db import db
from fastapi import HTTPException
from src.services.card_utils import (
    normalize_card_name, completion_owned,
    is_basic_land,
    completion_percentages_by_deck, completion_cards, completion_counts,
    colors_by_deck,
)
from src.services.image_resolver import safe_image_uri
from src.services.import_service import parse_decklist_text
from src.services.pricing_service import PRICE_PROVIDERS
from src.schemas.simulated_collections import (
    CandidateDeckInfo,
    SimulatedCardAnalysisItem,
    SimulatedCollectionSummary,
    SimulatedCollectionAnalysisResponse,
)

@dataclass
class OfferAnalysisContext:
    """User data reused within one request; never a shared or persistent cache."""
    user_id: str
    user_data: tuple
    resolved_names: dict
    printings: list
    reprints: dict
    # Deck completion, colors and demand derived from user_data, computed once
    # per request instead of once per offer.
    derived: Optional[tuple] = None
    completion: Optional[Any] = None


class SimulatedCollectionService:
    @staticmethod
    async def _prepare_context(user_id, names):
        decks, collection, wants, resolved = await asyncio.gather(
            db.deck.find_many(where={"userId": user_id, "isArchived": False}, include={"cards": True}),
            db.collectioncard.find_many(where={"userId": user_id}),
            db.wantcard.find_many(where={"userId": user_id}),
            resolve_offer_names(names, db, allow_remote=False),
        )
        canonical = [resolved.get(fold_search_text(name), name) for name in names]
        norms = sorted({normalize_card_name(name) for name in canonical})
        printings = await SimulatedCollectionService._printings_for_norms(norms)
        reprints = await upcoming_reprints_by_name(canonical, printings, db)
        return OfferAnalysisContext(user_id, (decks, collection, wants), resolved, printings, reprints)

    @staticmethod
    async def _printings_for_norms(norms):
        """Printings with their catalog and set, each related row loaded once.

        Including the catalog per printing repeated its large localized
        details for every edition (15k printings for 2k cards). The printing
        query keeps its previous filter, so results and order are unchanged.
        """
        if not norms:
            return []
        printings = await db.cardprinting.find_many(where={"catalog": {"normalizedName": {"in": list(norms)}}})
        catalog_ids = list({p.catalogId for p in printings if getattr(p, "catalogId", None)})
        set_ids = list({p.setId for p in printings if getattr(p, "setId", None)})
        catalogs, sets = await asyncio.gather(
            db.cardcatalog.find_many(where={"id": {"in": catalog_ids}}) if catalog_ids else asyncio.sleep(0, []),
            db.cardset.find_many(where={"id": {"in": set_ids}}) if set_ids else asyncio.sleep(0, []),
        )
        catalogs_by_id = {catalog.id: catalog for catalog in catalogs}
        sets_by_id = {card_set.id: card_set for card_set in sets}
        for printing in printings:
            if getattr(printing, "catalogId", None) in catalogs_by_id:
                printing.catalog = catalogs_by_id[printing.catalogId]
            if getattr(printing, "setId", None) in sets_by_id:
                printing.set = sets_by_id[printing.setId]
        return printings

    @staticmethod
    def _saved_names(coll):
        raw = getattr(coll, "rawText", None)
        if isinstance(raw, str) and raw.strip():
            cart = parse_cardmarket_cart(raw)
            names = [entry.name for entry in (cart.entries if cart else parse_decklist_text(raw))]
        else:
            names = [card.cardName for card in (coll.cards or [])]
        return names + [normalize_card_name(card.cardName) for card in (coll.cards or [])
                        if isinstance(getattr(card, "selectedPrintingId", None), str) and card.selectedPrintingId]

    @staticmethod
    async def analyze_raw_text(
        user_id: str,
        raw_text: str,
        name: str = "Simulación",
        description: Optional[str] = None,
        provider: str = "cardmarket",
        printing_overrides: Optional[Dict[str, str]] = None,
        include_history: bool = True,
        context: Optional[OfferAnalysisContext] = None,
    ) -> SimulatedCollectionAnalysisResponse:
        cart = parse_cardmarket_cart(raw_text)
        if cart is not None:
            parsed_entries = [
                ParsedCardEntry(name=e.name, quantity=e.quantity, collectorNumber=e.collector_number)
                for e in cart.entries
            ]
        else:
            parsed_entries = parse_decklist_text(raw_text)
        if not parsed_entries:
            return SimulatedCollectionAnalysisResponse(
                id=None,
                name=name,
                description=description,
                totalEconomicValue=0.0,
                economicValueExcludingOwned=0.0,
                sellableValue=0.0,
                sellableCardsCount=0,
                currencySymbol="€",
                globalNetGain=0.0,
                totalCards=0,
                uniqueCards=0,
                usefulCardsCount=0,
                alreadyOwnedCardsCount=0,
                benefitedDecksCount=0,
                cards=[],
            )



        # Aggregate raw entries by normalized name while retaining display name
        aggregated: Dict[str, Dict[str, Any]] = {}
        for entry in parsed_entries:
            norm = normalize_card_name(entry.name)
            if norm not in aggregated:
                aggregated[norm] = {
                    "cardName": entry.name,
                    "quantity": 0,
                    "setCode": entry.setCode,
                    "collectorNumber": entry.collectorNumber,
                }
            aggregated[norm]["quantity"] += entry.quantity

        return await SimulatedCollectionService._run_analysis(
            user_id=user_id,
            cards_input=list(aggregated.values()),
            name=name,
            description=description,
            provider=provider,
            collection_id=None,
            purchase_cart=cart,
            include_history=include_history,
            raw_text=raw_text,
            printing_overrides=printing_overrides, context=context,
        )

    @staticmethod
    def _derive_user_data(user_decks, real_collection, wants):
        """Owned copies, wanted names and per-name deck demand; read-only for callers."""
        col_map: Dict[str, int] = {}
        for c in real_collection:
            norm = normalize_card_name(c.cardName)
            col_map[norm] = col_map.get(norm, 0) + c.quantity

        want_names = {normalize_card_name(w.cardName) for w in wants}

        # Deck completion % and colors
        all_deck_cards = [c for d in user_decks for c in (d.cards or [])]
        completions = completion_percentages_by_deck(all_deck_cards, col_map)
        deck_colors = colors_by_deck(all_deck_cards)

        deck_completion_totals: Dict[str, int] = {}
        deck_cards_by_norm: Dict[str, List[Dict[str, Any]]] = {}

        for d in user_decks:
            total_count, owned_count = completion_counts(d.cards or [], col_map)
            for c in completion_cards(d.cards or []):
                norm = normalize_card_name(c.cardName)
                if norm not in deck_cards_by_norm:
                    deck_cards_by_norm[norm] = []
                deck_cards_by_norm[norm].append({
                    "deckId": d.id,
                    "deckName": d.name,
                    "completionPercentage": completions.get(d.id, 0.0),
                    "ownedCards": owned_count,
                    "colors": deck_colors.get(d.id, []),
                    "requestedQuantity": c.quantity,
                    "assignedQuantity": c.assignedQuantity or 0,
                    "typeLine": c.typeLine,
                    "manaCost": c.manaCost,
                    "imageUri": c.imageUri,
                    "cardScryfallId": c.cardScryfallId,
                })
            deck_completion_totals[d.id] = total_count
        return col_map, want_names, deck_cards_by_norm, deck_completion_totals

    @staticmethod
    async def _run_analysis(
        user_id: str,
        cards_input: List[Dict[str, Any]],
        name: str,
        description: Optional[str] = None,
        provider: str = "cardmarket",
        collection_id: Optional[str] = None,
        purchase_cart: Optional[CartImport] = None,
        raw_text: Optional[str] = None,
        printing_overrides: Optional[Dict[str, str]] = None,
        include_history: bool = True,
        context: Optional[OfferAnalysisContext] = None,
    ) -> SimulatedCollectionAnalysisResponse:
        # Translate exact local aliases before demand, inventory and pricing lookups.
        if context is not None and context.user_id != user_id:
            raise ValueError("Offer analysis context belongs to another user")
        resolved_names = context.resolved_names if context is not None else await resolve_offer_names([card["cardName"] for card in cards_input] + list(printing_overrides or {}), db)
        def canonical_name(value):
            return resolved_names.get(fold_search_text(value), value)
        canonical_cards = {}
        for card in cards_input:
            canonical = canonical_name(card["cardName"])
            key = normalize_card_name(canonical)
            if key not in canonical_cards:
                canonical_cards[key] = {**card, "cardName": canonical, "quantity": 0, "sourceNames": []}
            canonical_cards[key]["quantity"] += card["quantity"]
            for source in card.get("sourceNames") or [card["cardName"]]:
                if source not in canonical_cards[key]["sourceNames"]:
                    canonical_cards[key]["sourceNames"].append(source)
        cards_input = list(canonical_cards.values())
        if purchase_cart is not None:
            purchase_cart = replace(purchase_cart, entries=[replace(entry, name=canonical_name(entry.name)) for entry in purchase_cart.entries])
        printing_overrides = {normalize_card_name(canonical_name(key)): value for key, value in (printing_overrides or {}).items()}

        prov_info = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        symbol = prov_info.get("symbol", "€")

        # These user-scoped reads are independent; dispatch them together.
        user_decks, real_collection, wants = context.user_data if context is not None else await asyncio.gather(
            db.deck.find_many(where={"userId": user_id, "isArchived": False}, include={"cards": True}),
            db.collectioncard.find_many(where={"userId": user_id}),
            db.wantcard.find_many(where={"userId": user_id}),
        )
        if context is not None and context.derived is not None:
            col_map, want_names, deck_cards_by_norm, deck_completion_totals = context.derived
        else:
            col_map, want_names, deck_cards_by_norm, deck_completion_totals = SimulatedCollectionService._derive_user_data(
                user_decks, real_collection, wants)
            if context is not None:
                context.derived = (col_map, want_names, deck_cards_by_norm, deck_completion_totals)

        all_deck_cards = [c for d in user_decks for c in (d.cards or [])]
        total_completion_cards = sum(deck_completion_totals.values())

        # 4. Resolve cheapest positive printings for cards in the simulation
        sim_norms = [normalize_card_name(c["cardName"]) for c in cards_input]
        cheapest_printings: Dict[str, Any] = {}
        fallback_printings: Dict[str, Any] = {}

        all_printings = []
        if sim_norms:
            all_printings = [p for p in context.printings if p.catalog.normalizedName in sim_norms] if context is not None else await db.cardprinting.find_many(
                where={"catalog": {"normalizedName": {"in": sim_norms}}},
                include={"catalog": True, "set": True},
            )
            all_printings = [p for p in all_printings if getattr(getattr(p, "set", None), "setType", None) not in ("art_series", "memorabilia") and not (isinstance(getattr(getattr(p, "set", None), "code", None), str) and len(p.set.code) == 4 and p.set.code.lower().startswith("a"))]
            for p in all_printings:
                cat = getattr(p, "catalog", None)
                norm = normalize_card_name(cat.normalizedName) if (cat and isinstance(getattr(cat, "normalizedName", None), str)) else None
                name_norm = normalize_card_name(cat.name) if (cat and isinstance(getattr(cat, "name", None), str)) else None
                if not norm:
                    norm = name_norm
                if not norm:
                    continue

                p_price = float(p.priceCardmarketTrend or p.priceEur or 0.0)

                for k in filter(None, {norm, name_norm}):
                    if k not in fallback_printings:
                        fallback_printings[k] = p

                # Ignore non-positive prices to strictly adhere to cheapest non-zero print rule
                if p_price <= 0.0:
                    continue

                for k in filter(None, {norm, name_norm}):
                    existing = cheapest_printings.get(k)
                    if existing is None or p_price < existing["price"]:
                        cheapest_printings[k] = {
                            "printing": p,
                            "price": p_price,
                        }

        upcoming_reprints = context.reprints if context is not None else await upcoming_reprints_by_name([card["cardName"] for card in cards_input], all_printings, db)

        overrides = {normalize_card_name(name): value for name, value in (printing_overrides or {}).items()}
        selected_printings = {}
        for norm, printing_id in overrides.items():
            printing = next((p for p in all_printings if p.id == printing_id
                             and (normalize_card_name(getattr(getattr(p, "catalog", None), "normalizedName", "")) == norm
                                  or normalize_card_name(getattr(getattr(p, "catalog", None), "name", "")) == norm)), None)
            if printing is None and printing_id:
                try:
                    p_db = await db.cardprinting.find_first(
                        where={"id": printing_id},
                        include={"catalog": True, "set": True},
                    )
                    if p_db:
                        printing = p_db
                        all_printings.append(p_db)
                except Exception:
                    pass
            cat_norm = normalize_card_name(getattr(getattr(printing, "catalog", None), "normalizedName", "")) if printing else None
            cat_name = normalize_card_name(getattr(getattr(printing, "catalog", None), "name", "")) if printing else None
            matches_sim = norm in sim_norms or (cat_norm and cat_norm in sim_norms) or (cat_name and cat_name in sim_norms)
            if not matches_sim or printing is None:
                if context is not None:
                    continue
                raise HTTPException(status_code=422, detail="La versión no pertenece a esta carta o no está en el catálogo local.")
            target_key = norm if norm in sim_norms else (cat_norm if cat_norm in sim_norms else (cat_name if cat_name in sim_norms else norm))
            selected_printings[target_key] = printing
            selected_printings[norm] = printing
            if cat_norm:
                selected_printings[cat_norm] = printing
            if cat_name:
                selected_printings[cat_name] = printing

        # 5. Build analysis for each card
        analysis_cards: List[SimulatedCardAnalysisItem] = []
        benefited_deck_ids = set()

        def _extract_printing_meta(p: Any) -> Dict[str, Any]:
            cat = getattr(p, "catalog", None)
            set_obj = getattr(p, "set", None)
            m_cost = getattr(cat, "manaCost", None) if cat else None
            t_line = getattr(cat, "typeLine", None) if cat else None
            s_code = (getattr(set_obj, "code", None) or getattr(cat, "setCode", None)) if (set_obj or cat) else None
            c_num = getattr(p, "collectorNumber", None) or (getattr(cat, "collectorNumber", None) if cat else None)
            img = (
                getattr(p, "imageUri", None)
                or getattr(p, "imageUriLarge", None)
                or getattr(p, "imageUriSmall", None)
                or (getattr(cat, "imageUri", None) if cat else None)
            )
            return {
                "scryfall_id": getattr(p, "id", None),
                "mana_cost": m_cost,
                "type_line": t_line,
                "set_code": s_code,
                "collector_number": c_num,
                "image_uri": safe_image_uri(img) if img else None,
            }

        # First printing per (name, set, collector number), as the linear scan returned.
        printings_by_identity: Dict[tuple, Any] = {}
        for p in all_printings:
            identity = (normalize_card_name(getattr(p.catalog, "normalizedName", "")),
                        (getattr(getattr(p, "set", None), "code", "") or "").lower(), str(getattr(p, "collectorNumber", "")))
            printings_by_identity.setdefault(identity, p)

        for c in cards_input:
            card_name = c["cardName"]
            quantity = c["quantity"]
            norm = normalize_card_name(card_name)

            # Printing resolution
            best_info = cheapest_printings.get(norm)
            unit_price = 0.0
            image_uri = None
            mana_cost = None
            type_line = None
            scryfall_id = None
            set_code = c.get("setCode")
            collector_number = c.get("collectorNumber")
            selected_printing = None

            exact_source = printings_by_identity.get((norm, c["setCode"].lower(), str(c["collectorNumber"]))) if (
                c.get("setCode") and c.get("collectorNumber")) else None
            if norm in selected_printings or exact_source:
                p = selected_printings.get(norm) or exact_source
                selected_printing = p
                unit_price = max(0.0, float(p.priceCardmarketTrend or p.priceEur or 0.0))
                meta = _extract_printing_meta(p)
                scryfall_id = meta["scryfall_id"]
                mana_cost = meta["mana_cost"]
                type_line = meta["type_line"]
                image_uri = meta["image_uri"]
                set_code = meta["set_code"]
                collector_number = meta["collector_number"]
            elif best_info:
                p = best_info["printing"]
                selected_printing = p
                unit_price = best_info["price"]
                meta = _extract_printing_meta(p)
                scryfall_id = meta["scryfall_id"]
                mana_cost = meta["mana_cost"]
                type_line = meta["type_line"]
                image_uri = meta["image_uri"]
                if not set_code:
                    set_code = meta["set_code"]
                if not collector_number:
                    collector_number = meta["collector_number"]
            elif norm in fallback_printings:
                p = fallback_printings[norm]
                selected_printing = p
                meta = _extract_printing_meta(p)
                scryfall_id = meta["scryfall_id"]
                mana_cost = meta["mana_cost"]
                type_line = meta["type_line"]
                image_uri = meta["image_uri"]
                if not set_code:
                    set_code = meta["set_code"]
                if not collector_number:
                    collector_number = meta["collector_number"]

            if selected_printing is None and context is None:
                try:
                    fallback_p = await db.cardprinting.find_first(
                        where={"OR": [
                            {"catalog": {"normalizedName": norm}},
                            {"catalog": {"name": {"equals": card_name, "mode": "insensitive"}}}
                        ]},
                        include={"catalog": True, "set": True},
                        order={"priceCardmarketTrend": "asc"}
                    )
                    if fallback_p:
                        selected_printing = fallback_p
                        meta = _extract_printing_meta(fallback_p)
                        scryfall_id = meta["scryfall_id"]
                        mana_cost = meta["mana_cost"]
                        type_line = meta["type_line"]
                        image_uri = meta["image_uri"]
                        if not set_code:
                            set_code = meta["set_code"]
                        if not collector_number:
                            collector_number = meta["collector_number"]
                        unit_price = max(0.0, float(fallback_p.priceCardmarketTrend or fallback_p.priceEur or 0.0))
                except Exception:
                    pass


            # Fallback metadata from deck cards if catalog had none
            deck_matches = deck_cards_by_norm.get(norm, [])
            if deck_matches:
                first_match = deck_matches[0]
                if not image_uri and first_match.get("imageUri"):
                    image_uri = safe_image_uri(first_match["imageUri"])
                if not mana_cost and first_match.get("manaCost"):
                    mana_cost = first_match["manaCost"]
                if not type_line and first_match.get("typeLine"):
                    type_line = first_match["typeLine"]
                if not scryfall_id and first_match.get("cardScryfallId"):
                    scryfall_id = first_match["cardScryfallId"]

            # Quantities and deficits
            copies_needed_total = sum(d["requestedQuantity"] for d in deck_matches)
            copies_owned_real = col_map.get(norm, 0)
            is_basic = is_basic_land(type_line, card_name)

            candidate_decks: List[CandidateDeckInfo] = []
            useful_copies = 0
            surplus_copies = quantity
            net_gain = 0.0

            # Cards already in real collection (copies_owned_real > 0) or basic lands
            # DO NOT contribute to deck completion, candidate decks, or net gain.
            if not is_basic and copies_owned_real == 0:
                total_missing = 0
                for d in deck_matches:
                    req = d["requestedQuantity"]
                    asgn = d["assignedQuantity"]
                    missing = int(asgn <= 0)
                    if missing > 0:
                        total_missing += missing
                        deck_nb = deck_completion_totals.get(d["deckId"], 1)
                        owned_count = d["ownedCards"]
                        pot_gain = round((owned_count + 1) / deck_nb * 100, 1) - d["completionPercentage"] if deck_nb > 0 else 0.0
                        candidate_decks.append(
                            CandidateDeckInfo(
                                deckId=d["deckId"],
                                deckName=d["deckName"],
                                completionPercentage=d["completionPercentage"],
                                colors=d["colors"],
                                requestedQuantity=req,
                                assignedQuantity=asgn,
                                missingQuantity=missing,
                                potentialGain=pot_gain,
                                totalCards=deck_nb,
                                ownedCards=owned_count,
                            )
                        )
                        benefited_deck_ids.add(d["deckId"])

                useful_copies = min(quantity, 1) if total_missing else 0
                surplus_copies = max(0, quantity - useful_copies)

                if total_completion_cards > 0 and useful_copies > 0:
                    net_gain = round(sum(cd.potentialGain for cd in candidate_decks) / len(user_decks), 2) if user_decks else 0

            # Sanitize strings to avoid non-string types
            def _to_str(val):
                return str(val) if val is not None and isinstance(val, str) else None

            set_code_str = _to_str(set_code)
            col_num_str = _to_str(collector_number)
            mana_cost_str = _to_str(mana_cost)
            type_line_str = _to_str(type_line)
            scryfall_id_str = _to_str(scryfall_id)

            sellable_copies = surplus_copies
            sellable_value = round(sellable_copies * unit_price, 2)

            total_price = round(unit_price * quantity, 2)

            analysis_cards.append(
                SimulatedCardAnalysisItem(
                    upcomingReprints=upcoming_reprints.get(norm, []),
                    selectedPrintingId=getattr(selected_printing, "id", None),
                    inWants=norm in want_names,
                    cardName=card_name,
                    sourceNames=c.get("sourceNames", []),
                    cardScryfallId=scryfall_id_str,
                    quantity=quantity,
                    setCode=set_code_str,
                    collectorNumber=col_num_str,
                    manaCost=mana_cost_str,
                    typeLine=type_line_str,
                    imageUri=image_uri,
                    unitPrice=unit_price,
                    totalPrice=total_price,
                    copiesOwnedReal=copies_owned_real,
                    copiesNeededTotal=copies_needed_total,
                    usefulCopies=useful_copies,
                    surplusCopies=surplus_copies,
                    sellableCopies=sellable_copies,
                    sellableValue=sellable_value,
                    netCompletionGain=net_gain,
                    candidateDeckCount=len(candidate_decks),
                    candidateDecks=candidate_decks,
                )
            )


        # 6. Global metrics
        total_economic_value = round(sum(c.totalPrice for c in analysis_cards), 2)
        economic_value_excluding_owned = round(
            sum(c.totalPrice for c in analysis_cards if c.copiesOwnedReal == 0), 2
        )
        total_sellable_value = round(sum(c.sellableValue for c in analysis_cards), 2)
        total_sellable_cards = sum(c.sellableCopies for c in analysis_cards)
        total_useful_copies = sum(c.usefulCopies for c in analysis_cards)
        already_owned_cards_count = sum(c.quantity for c in analysis_cards if c.copiesOwnedReal > 0)
        total_cards_count = sum(c.quantity for c in analysis_cards)
        global_net_gain = (
            round(sum(sum(cd.potentialGain for cd in card.candidateDecks) for card in analysis_cards) / len(user_decks), 2)
            if user_decks
            else 0.0
        )

        purchase = None
        if purchase_cart is not None:
            purchase = compare_purchase(purchase_cart, all_printings, wants, col_map, set(deck_cards_by_norm), overrides)
            if include_history:
                await enrich_purchase_history(db, purchase)
            history_statistics = purchase.historyStatistics
            history_covered = purchase.historyCoveredCopies
            history_missing = purchase.missingHistoryCopies
        elif include_history:
            statistics, history_statistics = await get_history_statistics(db,
                [(card.cardScryfallId, False, card.quantity) for card in analysis_cards], include_total=True)
            for card in analysis_cards:
                card.historyStatistics = statistics.get((card.cardScryfallId, False), PriceHistoryStatistics())
            history_covered = sum(card.quantity for card in analysis_cards if card.historyStatistics.observations)
            history_missing = sum(card.quantity for card in analysis_cards if not card.historyStatistics.observations)
            if history_missing:
                history_statistics = PriceHistoryStatistics()

        if not include_history:
            history_statistics = None
            history_covered = history_missing = 0

        from src.services.opportunity_completion import completion_baseline, enrich_completion
        matches = []
        wants_by_name = {}
        for want in wants:
            norm = normalize_card_name(want.cardName)
            wants_by_name.setdefault(norm, []).append(want)
        for index, card in enumerate(analysis_cards):
            norm = normalize_card_name(card.cardName)
            requested = wants_by_name.get(norm, [])
            if not card.candidateDecks and not requested:
                continue
            capacity = sum(want.quantity for want in requested) if requested else 1
            matches.append(WhatsAppDealMatch(
                id=index + 1, card_name=card.cardName, normalized_name=norm,
                subject="vende", whatsapp_url=f"offer:{collection_id or 'temporary'}", source_phone="", direct_whatsapp_link="",
                set_code=card.setCode, extra={"source": "manual", "quantity": card.quantity},
                unit_price=card.unitPrice if card.unitPrice > 0 else None,
                trend_unit_price=card.unitPrice if card.unitPrice > 0 else None,
                price_source="cardmarket" if card.unitPrice > 0 else None,
                trade_quantity=min(card.quantity, capacity),
                matched_want=WantMatchInfo(want_id=getattr(requested[0], "id", "") if requested else "", quantity_wanted=capacity,
                                          requested_decks=[deck.deckName for deck in card.candidateDecks]),
            ))
        baseline = None
        if context is not None:
            if context.completion is None:
                context.completion = completion_baseline(user_decks, all_deck_cards, real_collection)
            baseline = context.completion
        deck_count, before = enrich_completion(matches, user_decks, all_deck_cards, real_collection, baseline)
        projected = dict(col_map)
        for card in analysis_cards:
            if card.quantity > 0:
                projected[normalize_card_name(card.cardName)] = max(1, projected.get(normalize_card_name(card.cardName), 0))
        after_by_deck = completion_percentages_by_deck(all_deck_cards, projected)
        after = sum(after_by_deck.get(deck.id, 0) for deck in user_decks) / deck_count if deck_count else 0
        global_net_gain = round(max(0, after - before), 2)
        match_by_name = {match.normalized_name: match for match in matches}
        for card in analysis_cards:
            match = match_by_name.get(normalize_card_name(card.cardName))
            card.netCompletionGain = match.completion_gain if match else 0
        if purchase:
            purchase_by_name = {}
            for row in purchase.cards:
                purchase_by_name.setdefault(normalize_card_name(row.cardName), []).append(row)
            for match in matches:
                entries = purchase_by_name.get(match.normalized_name, [])
                if entries and all(row.purchaseUnitPrice is not None for row in entries):
                    copies = sum(row.quantity for row in entries)
                    match.unit_price = sum(row.purchaseUnitPrice * row.quantity for row in entries) / copies
                    match.price = str(match.unit_price)
                    match.price_source = "link"
        opportunities = WhatsAppMatchesResponse(wants_matches=matches, total_wants_matches=len(matches),
            total_cards_scanned=len(analysis_cards), global_deck_count=deck_count, global_completion_before=before)

        return SimulatedCollectionAnalysisResponse(
            printingOverrides=overrides,
            rawText=raw_text,
            historyStatistics=history_statistics,
            historyCoveredCopies=history_covered,
            missingHistoryCopies=history_missing,
            purchaseAnalysis=purchase,
            id=collection_id,
            name=name,
            description=description,
            totalEconomicValue=total_economic_value,
            economicValueExcludingOwned=economic_value_excluding_owned,
            sellableValue=total_sellable_value,
            sellableCardsCount=total_sellable_cards,
            currencySymbol=symbol,
            globalNetGain=global_net_gain,
            opportunityMatches=opportunities,
            globalCompletionBefore=before,
            globalCompletionAfter=after,
            totalCards=total_cards_count,
            uniqueCards=len(analysis_cards),
            usefulCardsCount=total_useful_copies,
            alreadyOwnedCardsCount=already_owned_cards_count,
            benefitedDecksCount=len(benefited_deck_ids),
            cards=analysis_cards,
        )



    @staticmethod
    async def create_simulated_collection(
        user_id: str,
        name: str,
        description: Optional[str],
        raw_text: str,
        provider: str = "cardmarket",
        printing_overrides: Optional[Dict[str, str]] = None,
        origin: str = "other",
    ) -> SimulatedCollectionAnalysisResponse:
        # Run live analysis first
        analysis = await SimulatedCollectionService.analyze_raw_text(
            user_id=user_id,
            raw_text=raw_text,
            name=name,
            description=description,
            provider=provider,
            printing_overrides=printing_overrides,
            include_history=origin == "cardmarket",
        )

        # Save to database
        saved_coll = await db.simulatedcollection.create(
            data={
                "userId": user_id,
                "name": name,
                "description": description,
                "rawText": raw_text,
                "origin": origin,
            }
        )

        # Mark simulated cards at the moment they enter this saved checkpoint.
        checkpoint = datetime.now(timezone.utc)
        for c in analysis.cards:
            await db.simulatedcard.create(
                data={
                    "simulatedCollectionId": saved_coll.id,
                    "cardScryfallId": c.cardScryfallId,
                    "selectedPrintingId": c.selectedPrintingId,
                    "cardName": c.cardName,
                    "quantity": c.quantity,
                    "setCode": c.setCode,
                    "collectorNumber": c.collectorNumber,
                    "manaCost": c.manaCost,
                    "typeLine": c.typeLine,
                    "imageUri": c.imageUri,
                    "price": c.unitPrice,
                    "acquiredAt": checkpoint,
                }
            )
            c.acquiredAt = checkpoint

        analysis.id = saved_coll.id
        analysis.origin = origin
        from src.services.acquisition_finance import valuation_revision
        analysis.valuationRevision = valuation_revision(analysis)
        return analysis

    @staticmethod
    async def _analyze_saved(user_id: str, coll: Any, provider: str, override: Optional[Dict[str, str]] = None, include_history: Optional[bool] = None, context: Optional[OfferAnalysisContext] = None) -> SimulatedCollectionAnalysisResponse:
        if include_history is None:
            include_history = getattr(coll, "origin", "other") == "cardmarket"
        overrides = {normalize_card_name(c.cardName): c.selectedPrintingId for c in (coll.cards or [])
                     if isinstance(getattr(c, "selectedPrintingId", None), str) and c.selectedPrintingId}
        overrides.update(override or {})
        raw_text = getattr(coll, "rawText", None)
        if isinstance(raw_text, str) and raw_text.strip():
            analysis = await SimulatedCollectionService.analyze_raw_text(
                user_id, raw_text, coll.name, coll.description, provider, overrides,
                include_history=include_history, context=context,
            )
            analysis.id = coll.id
            SimulatedCollectionService._attach_offer(analysis, coll)
            SimulatedCollectionService._attach_acquisition_dates(analysis, coll.cards or [])
            await SimulatedCollectionService._attach_opportunities(user_id, analysis, coll, context=context)
            from src.services.acquisition_finance import valuation_revision
            analysis.valuationRevision = valuation_revision(analysis)
            return analysis
        analysis = await SimulatedCollectionService._run_analysis(
            user_id=user_id,
            cards_input=[{"cardName": c.cardName, "quantity": c.quantity,
                          "setCode": c.setCode, "collectorNumber": c.collectorNumber}
                         for c in (coll.cards or [])],
            name=coll.name, description=coll.description, provider=provider, collection_id=coll.id,
            include_history=include_history,
            printing_overrides=overrides, context=context,
        )
        SimulatedCollectionService._attach_acquisition_dates(analysis, coll.cards or [])
        SimulatedCollectionService._attach_offer(analysis, coll)
        await SimulatedCollectionService._attach_opportunities(user_id, analysis, coll, context=context)
        from src.services.acquisition_finance import valuation_revision
        analysis.valuationRevision = valuation_revision(analysis)
        return analysis

    @staticmethod
    async def _attach_opportunities(user_id, analysis, coll, context=None):
        import json
        from src.services.opportunity_valuation import _amount
        metadata = getattr(coll, "sourceMetadata", None)
        original = metadata.get("rows", []) if isinstance(metadata, dict) else []
        aliases = {normalize_card_name(source): normalize_card_name(card.cardName) for card in analysis.cards for source in card.sourceNames}
        by_name = {aliases.get(normalize_card_name(row["card_name"]), normalize_card_name(row["card_name"])): row for row in original}
        opportunities = analysis.opportunityMatches
        if opportunities is None:
            return
        if analysis.opportunitySubject == "compra_busca":
            # Allocation remains physical: completion sharing never creates free sale copies.
            collection = context.user_data[1] if context is not None else await db.collectioncard.find_many(where={"userId": user_id})
            assignments = [card for deck in context.user_data[0] for card in (deck.cards or [])] if context is not None else await db.deckcard.find_many(where={"deck": {"userId": user_id, "isArchived": False}})
            owned, assigned = {}, {}
            for card in collection:
                norm = normalize_card_name(card.cardName)
                owned[norm] = owned.get(norm, 0) + card.quantity
            for card in assignments:
                norm = normalize_card_name(card.cardName)
                assigned[norm] = assigned.get(norm, 0) + (card.assignedQuantity or 0)
            sale = []
            for index, card in enumerate(analysis.cards):
                norm = normalize_card_name(card.cardName)
                free = max(0, owned.get(norm, 0) - assigned.get(norm, 0))
                if free:
                    sale.append(WhatsAppDealMatch(id=index + 1, card_name=card.cardName, normalized_name=norm,
                        subject="compra_busca", whatsapp_url="", source_phone="", direct_whatsapp_link="",
                        set_code=card.setCode, extra={"source": "manual", "quantity": card.quantity},
                        unit_price=card.unitPrice if card.unitPrice > 0 else None,
                        trend_unit_price=card.unitPrice if card.unitPrice > 0 else None,
                        price_source="cardmarket" if card.unitPrice > 0 else None,
                        trade_quantity=min(card.quantity, free),
                        matched_collection=CollectionMatchInfo(collection_id="", quantity_owned=owned[norm], available_quantity=free)))
            analysis.globalNetGain = 0
            analysis.globalCompletionAfter = analysis.globalCompletionBefore
            analysis.usefulCardsCount = analysis.benefitedDecksCount = 0
            for card in analysis.cards:
                norm = normalize_card_name(card.cardName)
                card.candidateDecks = []
                card.candidateDeckCount = 0
                card.netCompletionGain = 0
                card.usefulCopies = 0
                card.sellableCopies = min(card.quantity, max(0, owned.get(norm, 0) - assigned.get(norm, 0)))
                card.sellableValue = round(card.sellableCopies * card.unitPrice, 2)
            analysis.sellableCardsCount = sum(card.sellableCopies for card in analysis.cards)
            analysis.sellableValue = round(sum(card.sellableValue for card in analysis.cards), 2)
            opportunities.wants_matches = []
            opportunities.collection_matches = sale
            opportunities.total_wants_matches = 0
            opportunities.total_collection_matches = len(sale)
        from src.services.whatsapp_deals_service import _format_whatsapp_link
        for match in opportunities.wants_matches + opportunities.collection_matches:
            source = by_name.get(match.normalized_name, {})
            extra = json.loads(source.get("extra_json") or "{}")
            match.whatsapp_url = analysis.sourceUrl or f"offer:{analysis.id}"
            match.source_phone = source.get("source_phone") or ""
            match.source_chat = source.get("source_chat")
            match.raw_message = source.get("raw_message")
            match.detected_at = source.get("detected_at")
            match.condition = source.get("condition")
            if match.source_phone:
                match.direct_whatsapp_link = _format_whatsapp_link(match.source_phone, f"Hola, te escribo por tu lista de Magic: {match.card_name}.")
            match.price = source.get("price") if source else match.price
            amount = _amount(match.price)
            if amount is None:
                amount = _amount(extra.get("reference_price"))
            if amount is not None:
                match.unit_price = amount
                match.price_source = "link"
                match.price_currency = {"€": "EUR", "$": "USD"}.get(source.get("currency"), source.get("currency") or "EUR")
            match.extra.update(extra)

    @staticmethod
    def _attach_offer(analysis, coll):
        for key in ["origin", "opportunitySubject", "status", "seller", "shipping", "sourceUrl", "paidTotal", "confirmedBatchId"]:
            value = getattr(coll, key, None)
            if isinstance(value, (str, int, float)) or value is None:
                if value is not None:
                    setattr(analysis, key, value)
            elif key in ("shipping", "paidTotal"):
                try:
                    setattr(analysis, key, float(value))
                except (ValueError, TypeError):
                    pass

    @staticmethod
    def _attach_acquisition_dates(analysis: SimulatedCollectionAnalysisResponse, cards: List[Any]) -> None:
        acquired_by_name = {
            normalize_card_name(card.cardName): getattr(card, "acquiredAt", None)
            for card in cards
        }
        for card in analysis.cards:
            card.acquiredAt = acquired_by_name.get(normalize_card_name(card.cardName))

    @staticmethod
    async def list_simulated_collections(
        user_id: str,
        provider: str = "cardmarket",
    ) -> List[SimulatedCollectionSummary]:
        colls = await db.simulatedcollection.find_many(
            where={"userId": user_id},
            include={"cards": True},
            order={"createdAt": "desc"},
        )

        context = await SimulatedCollectionService._prepare_context(user_id, [name for coll in colls for name in SimulatedCollectionService._saved_names(coll)]) if colls else None
        summaries: List[SimulatedCollectionSummary] = []
        for coll in colls:
            analysis = await SimulatedCollectionService._analyze_saved(user_id, coll, provider, include_history=False, context=context)
            summaries.append(
                SimulatedCollectionSummary(
                    origin=analysis.origin, opportunitySubject=analysis.opportunitySubject, globalCompletionBefore=analysis.globalCompletionBefore, globalCompletionAfter=analysis.globalCompletionAfter, opportunityMatches=analysis.opportunityMatches,
                    status=analysis.status, seller=analysis.seller, shipping=analysis.shipping, sourceUrl=analysis.sourceUrl, paidTotal=analysis.paidTotal, confirmedBatchId=analysis.confirmedBatchId,
                    id=coll.id,
                    name=coll.name,
                    description=coll.description,
                    purchaseAnalysis=analysis.purchaseAnalysis,
                    totalCards=analysis.totalCards,
                    uniqueCards=analysis.uniqueCards,
                    totalEconomicValue=analysis.totalEconomicValue,
                    economicValueExcludingOwned=analysis.economicValueExcludingOwned,
                    sellableValue=analysis.sellableValue,
                    sellableCardsCount=analysis.sellableCardsCount,
                    globalNetGain=analysis.globalNetGain,
                    usefulCardsCount=analysis.usefulCardsCount,
                    alreadyOwnedCardsCount=analysis.alreadyOwnedCardsCount,
                    benefitedDecksCount=analysis.benefitedDecksCount,
                    createdAt=coll.createdAt.isoformat(),
                    updatedAt=coll.updatedAt.isoformat(),
                )


            )

        return summaries

    @staticmethod
    async def get_simulated_collection(
        user_id: str,
        collection_id: str,
        provider: str = "cardmarket",
    ) -> Optional[SimulatedCollectionAnalysisResponse]:
        coll = await db.simulatedcollection.find_unique(
            where={"id": collection_id},
            include={"cards": True},
        )
        if not coll or coll.userId != user_id:
            return None

        analysis = await SimulatedCollectionService._analyze_saved(user_id, coll, provider)
        from src.services.acquisition_finance import valuation_revision
        analysis.valuationRevision = valuation_revision(analysis)
        return analysis

    @staticmethod
    async def update_card_version(user_id: str, collection_id: str, card_name: str,
                                  printing_id: str, provider: str = "cardmarket") -> SimulatedCollectionAnalysisResponse:
        coll = await db.simulatedcollection.find_unique(where={"id": collection_id}, include={"cards": True})
        if not coll or coll.userId != user_id:
            raise HTTPException(status_code=404, detail="Colección simulada no encontrada.")
        if getattr(coll, "status", None) == "purchased":
            raise HTTPException(status_code=409, detail="La oferta ya está comprada; edita las cartas en tu inventario.")
        norm = normalize_card_name(card_name)
        matches = [c for c in (coll.cards or []) if normalize_card_name(c.cardName) == norm]
        if not matches:
            # Offers saved before bilingual resolution can still store Spanish names.
            current = await SimulatedCollectionService._analyze_saved(user_id, coll, provider)
            target = next((card for card in current.cards if normalize_card_name(card.cardName) == norm
                           or any(fold_search_text(source) == fold_search_text(card_name) for source in card.sourceNames)), None)
            if target:
                source_names = {normalize_card_name(source) for source in target.sourceNames} | {normalize_card_name(target.cardName)}
                matches = [card for card in (coll.cards or []) if normalize_card_name(card.cardName) in source_names]
                norm = normalize_card_name(target.cardName)
        if not matches:
            raise HTTPException(status_code=404, detail="Carta no encontrada en la simulación.")
        # Recalculate and validate the printing before persisting the explicit choice.
        analysis = await SimulatedCollectionService._analyze_saved(user_id, coll, provider, {norm: printing_id})
        await db.simulatedcard.update_many(
            where={"simulatedCollectionId": collection_id, "cardName": {"in": [c.cardName for c in matches]}},
            data={"selectedPrintingId": printing_id},
        )
        from src.services.acquisition_finance import valuation_revision
        analysis.valuationRevision = valuation_revision(analysis)
        return analysis

    @staticmethod
    async def mutate_card(user_id: str, collection_id: str, card_name: str,
                          provider: str, operation: str, quantity: int = 1) -> SimulatedCollectionAnalysisResponse:
        coll = await db.simulatedcollection.find_unique(where={"id": collection_id}, include={"cards": True})
        if not coll or coll.userId != user_id:
            raise HTTPException(status_code=404, detail="Colección simulada no encontrada.")
        if getattr(coll, "status", None) == "purchased":
            raise HTTPException(status_code=409, detail="La oferta ya está comprada; edita las cartas en tu inventario.")
        name = card_name.strip()
        if not name or "\n" in name or "\r" in name:
            raise HTTPException(status_code=422, detail="El nombre de la carta no es válido.")
        raw_text = getattr(coll, "rawText", None) or ""
        if not raw_text.strip():
            raw_text = "\n".join(f"{c.quantity} {c.cardName}" for c in (coll.cards or []))

        cart = parse_cardmarket_cart(raw_text)
        if operation == "add":
            if cart is not None:
                updated_text = f"{raw_text.rstrip()}\n{quantity}x {name}\n"
            else:
                updated_text = f"{raw_text.rstrip()}\n// Mainboard\n{quantity} {name}\n"
        elif operation == "remove":
            target = normalize_card_name(name)
            current = await SimulatedCollectionService._analyze_saved(user_id, coll, provider)
            targets = {target}
            for card in current.cards:
                if normalize_card_name(card.cardName) == target or any(fold_search_text(source) == fold_search_text(name) for source in card.sourceNames):
                    targets.add(normalize_card_name(card.cardName))
                    targets.update(normalize_card_name(source) for source in card.sourceNames)
            if cart is not None:
                markers = list(re.finditer(r"(?im)^\s*(\d+)x\b", raw_text))
                if not markers:
                    raise HTTPException(status_code=404, detail="Carta no encontrada en la colección simulada.")
                chunks = [raw_text[:markers[0].start()]]
                removed = False
                for index, marker in enumerate(markers):
                    end = markers[index + 1].start() if index + 1 < len(markers) else len(raw_text)
                    chunk = raw_text[marker.start():end]
                    parsed = parse_cardmarket_cart(chunk + "\n0,00 €")
                    entry_name = parsed.entries[0].name if parsed and parsed.entries else ""
                    if normalize_card_name(entry_name) in targets:
                        removed = True
                    else:
                        chunks.append(chunk)
                if not removed:
                    raise HTTPException(status_code=404, detail="Carta no encontrada en la colección simulada.")
                updated_text = "".join(chunks)
            else:
                kept_lines = []
                removed = False
                for line in raw_text.splitlines(keepends=True):
                    parsed = parse_decklist_text(line)
                    if any(normalize_card_name(entry.name) in targets for entry in parsed):
                        removed = True
                    else:
                        kept_lines.append(line)
                if not removed:
                    raise HTTPException(status_code=404, detail="Carta no encontrada en la colección simulada.")
                updated_text = "".join(kept_lines)
        else:
            raise HTTPException(status_code=422, detail="Operación no válida.")

        old_overrides = {
            normalize_card_name(c.cardName): c.selectedPrintingId
            for c in (coll.cards or [])
            if getattr(c, "selectedPrintingId", None)
        }
        old_acquisition_dates = {
            normalize_card_name(c.cardName): getattr(c, "acquiredAt", None)
            for c in (coll.cards or []) if getattr(c, "acquiredAt", None)
        }
        checkpoint = datetime.now(timezone.utc)
        updated_cart = parse_cardmarket_cart(updated_text)
        updated_entries = updated_cart.entries if updated_cart is not None else parse_decklist_text(updated_text)
        updated_aliases = await resolve_offer_names([entry.name for entry in updated_entries], db)
        updated_names = {normalize_card_name(updated_aliases.get(fold_search_text(entry.name), entry.name)) for entry in updated_entries}
        retained_overrides = {key: value for key, value in old_overrides.items() if key in updated_names}
        analysis = await SimulatedCollectionService.analyze_raw_text(
            user_id, updated_text, coll.name, coll.description, provider, retained_overrides,
        )
        await db.simulatedcollection.update(
            where={"id": collection_id}, data={"rawText": updated_text},
        )
        await db.simulatedcard.delete_many(where={"simulatedCollectionId": collection_id})
        for card in analysis.cards:
            norm = normalize_card_name(card.cardName)
            await db.simulatedcard.create(data={
                "simulatedCollectionId": collection_id,
                "cardScryfallId": card.cardScryfallId,
                "selectedPrintingId": card.selectedPrintingId,
                "cardName": card.cardName,
                "quantity": card.quantity,
                "setCode": card.setCode,
                "collectorNumber": card.collectorNumber,
                "manaCost": card.manaCost,
                "typeLine": card.typeLine,
                "imageUri": card.imageUri,
                "price": card.unitPrice,
                "acquiredAt": old_acquisition_dates.get(norm) or checkpoint,
            })
            card.acquiredAt = old_acquisition_dates.get(norm) or checkpoint
        analysis.id = collection_id
        from src.services.acquisition_finance import valuation_revision
        analysis.valuationRevision = valuation_revision(analysis)
        return analysis

    @staticmethod
    async def delete_simulated_collection(user_id: str, collection_id: str) -> bool:
        coll = await db.simulatedcollection.find_unique(where={"id": collection_id})
        if not coll or coll.userId != user_id:
            return False
        if getattr(coll, "status", None) == "purchased":
            raise HTTPException(409, "Conserva la oferta comprada como justificante del lote.")
        await db.simulatedcollection.delete(where={"id": collection_id})
        return True
