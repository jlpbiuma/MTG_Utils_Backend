from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import re
from src.services.cardmarket_cart_parser import parse_cardmarket_cart, CartImport
from src.services.simulated_purchase_service import compare_purchase
from src.schemas.import_export import ParsedCardEntry
from src.core.db import db
from fastapi import HTTPException
from src.services.card_utils import (
    normalize_card_name,
    is_basic_land,
    completion_percentages_by_deck,
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

class SimulatedCollectionService:
    @staticmethod
    async def analyze_raw_text(
        user_id: str,
        raw_text: str,
        name: str = "Simulación",
        description: Optional[str] = None,
        provider: str = "cardmarket",
        printing_overrides: Optional[Dict[str, str]] = None,
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
            raw_text=raw_text,
            printing_overrides=printing_overrides,
        )

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
    ) -> SimulatedCollectionAnalysisResponse:
        prov_info = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        symbol = prov_info.get("symbol", "€")

        # 1. Fetch user decks & deck cards
        user_decks = await db.deck.find_many(
            where={"userId": user_id, "isArchived": False},
            include={"cards": True},
        )

        # 2. Fetch real collection
        real_collection = await db.collectioncard.find_many(where={"userId": user_id})
        col_map: Dict[str, int] = {}
        for c in real_collection:
            norm = normalize_card_name(c.cardName)
            col_map[norm] = col_map.get(norm, 0) + c.quantity

        wants = await db.wantcard.find_many(where={"userId": user_id})
        want_names = {normalize_card_name(w.cardName) for w in wants}

        # 3. Calculate deck completion % and colors
        all_deck_cards = [c for d in user_decks for c in (d.cards or [])]
        completions = completion_percentages_by_deck(all_deck_cards, col_map)
        deck_colors = colors_by_deck(all_deck_cards)

        deck_non_basics_count: Dict[str, int] = {}
        deck_cards_by_norm: Dict[str, List[Dict[str, Any]]] = {}

        for d in user_decks:
            nb_count = 0
            for c in (d.cards or []):
                if not is_basic_land(c.typeLine, c.cardName):
                    nb_count += c.quantity
                norm = normalize_card_name(c.cardName)
                if norm not in deck_cards_by_norm:
                    deck_cards_by_norm[norm] = []
                deck_cards_by_norm[norm].append({
                    "deckId": d.id,
                    "deckName": d.name,
                    "completionPercentage": completions.get(d.id, 0.0),
                    "colors": deck_colors.get(d.id, []),
                    "requestedQuantity": c.quantity,
                    "assignedQuantity": c.assignedQuantity or 0,
                    "typeLine": c.typeLine,
                    "manaCost": c.manaCost,
                    "imageUri": c.imageUri,
                    "cardScryfallId": c.cardScryfallId,
                })
            deck_non_basics_count[d.id] = nb_count

        total_non_basics_all_decks = sum(deck_non_basics_count.values())

        # 4. Resolve cheapest positive printings for cards in the simulation
        sim_norms = [normalize_card_name(c["cardName"]) for c in cards_input]
        cheapest_printings: Dict[str, Any] = {}
        fallback_printings: Dict[str, Any] = {}

        all_printings = []
        if sim_norms:
            all_printings = await db.cardprinting.find_many(
                where={"catalog": {"normalizedName": {"in": sim_norms}}},
                include={"catalog": True, "set": True},
            )
            for p in all_printings:
                cat = getattr(p, "catalog", None)
                norm = getattr(cat, "normalizedName", None) if cat else None
                if not norm:
                    continue

                p_price = float(p.priceCardmarketTrend or p.priceEur or 0.0)

                if norm not in fallback_printings:
                    fallback_printings[norm] = p

                # Ignore non-positive prices to strictly adhere to cheapest non-zero print rule
                if p_price <= 0.0:
                    continue

                existing = cheapest_printings.get(norm)
                if existing is None or p_price < existing["price"]:
                    cheapest_printings[norm] = {
                        "printing": p,
                        "price": p_price,
                    }

        overrides = {normalize_card_name(name): value for name, value in (printing_overrides or {}).items()}
        selected_printings = {}
        for norm, printing_id in overrides.items():
            printing = next((p for p in all_printings if p.id == printing_id
                             and normalize_card_name(getattr(p.catalog, "normalizedName", "")) == norm), None)
            if norm not in sim_norms or printing is None:
                raise HTTPException(status_code=422, detail="La versión no pertenece a esta carta o no está en el catálogo local.")
            selected_printings[norm] = printing

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

            if norm in selected_printings:
                p = selected_printings[norm]
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
                meta = _extract_printing_meta(p)
                scryfall_id = meta["scryfall_id"]
                mana_cost = meta["mana_cost"]
                type_line = meta["type_line"]
                image_uri = meta["image_uri"]
                if not set_code:
                    set_code = meta["set_code"]
                if not collector_number:
                    collector_number = meta["collector_number"]


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
                    missing = max(0, req - asgn)
                    if missing > 0:
                        total_missing += missing
                        deck_nb = deck_non_basics_count.get(d["deckId"], 1)
                        pot_gain = round((min(quantity, missing) / deck_nb) * 100, 1) if deck_nb > 0 else 0.0
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
                            )
                        )
                        benefited_deck_ids.add(d["deckId"])

                useful_copies = min(quantity, total_missing)
                surplus_copies = max(0, quantity - useful_copies)

                if total_non_basics_all_decks > 0 and useful_copies > 0:
                    net_gain = round((useful_copies / total_non_basics_all_decks) * 100, 2)

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
                    selectedPrintingId=overrides.get(norm),
                    inWants=norm in want_names,
                    cardName=card_name,
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
            round((total_useful_copies / total_non_basics_all_decks) * 100, 2)
            if total_non_basics_all_decks > 0
            else 0.0
        )

        purchase = None
        if purchase_cart is not None:
            purchase = compare_purchase(purchase_cart, all_printings, wants, col_map, set(deck_cards_by_norm), overrides)

        return SimulatedCollectionAnalysisResponse(
            printingOverrides=overrides,
            rawText=raw_text,
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
    ) -> SimulatedCollectionAnalysisResponse:
        # Run live analysis first
        analysis = await SimulatedCollectionService.analyze_raw_text(
            user_id=user_id,
            raw_text=raw_text,
            name=name,
            description=description,
            provider=provider,
            printing_overrides=printing_overrides,
        )

        # Save to database
        saved_coll = await db.simulatedcollection.create(
            data={
                "userId": user_id,
                "name": name,
                "description": description,
                "rawText": raw_text,
            }
        )

        # Mark simulated cards at the moment they enter this saved checkpoint.
        checkpoint = datetime.now(timezone.utc)
        for c in analysis.cards:
            await db.simulatedcard.create(
                data={
                    "simulatedCollectionId": saved_coll.id,
                    "cardScryfallId": c.cardScryfallId,
                    "selectedPrintingId": analysis.printingOverrides.get(normalize_card_name(c.cardName)),
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
        return analysis

    @staticmethod
    async def _analyze_saved(user_id: str, coll: Any, provider: str, override: Optional[Dict[str, str]] = None) -> SimulatedCollectionAnalysisResponse:
        overrides = {normalize_card_name(c.cardName): c.selectedPrintingId for c in (coll.cards or [])
                     if isinstance(getattr(c, "selectedPrintingId", None), str) and c.selectedPrintingId}
        overrides.update(override or {})
        raw_text = getattr(coll, "rawText", None)
        if isinstance(raw_text, str) and raw_text.strip():
            analysis = await SimulatedCollectionService.analyze_raw_text(
                user_id, raw_text, coll.name, coll.description, provider, overrides,
            )
            analysis.id = coll.id
            SimulatedCollectionService._attach_acquisition_dates(analysis, coll.cards or [])
            return analysis
        analysis = await SimulatedCollectionService._run_analysis(
            user_id=user_id,
            cards_input=[{"cardName": c.cardName, "quantity": c.quantity,
                          "setCode": c.setCode, "collectorNumber": c.collectorNumber}
                         for c in (coll.cards or [])],
            name=coll.name, description=coll.description, provider=provider, collection_id=coll.id,
            printing_overrides=overrides,
        )
        SimulatedCollectionService._attach_acquisition_dates(analysis, coll.cards or [])
        return analysis

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

        summaries: List[SimulatedCollectionSummary] = []
        for coll in colls:
            analysis = await SimulatedCollectionService._analyze_saved(user_id, coll, provider)
            summaries.append(
                SimulatedCollectionSummary(
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

        return await SimulatedCollectionService._analyze_saved(user_id, coll, provider)

    @staticmethod
    async def update_card_version(user_id: str, collection_id: str, card_name: str,
                                  printing_id: str, provider: str = "cardmarket") -> SimulatedCollectionAnalysisResponse:
        coll = await db.simulatedcollection.find_unique(where={"id": collection_id}, include={"cards": True})
        if not coll or coll.userId != user_id:
            raise HTTPException(status_code=404, detail="Colección simulada no encontrada.")
        norm = normalize_card_name(card_name)
        matches = [c for c in (coll.cards or []) if normalize_card_name(c.cardName) == norm]
        if not matches:
            raise HTTPException(status_code=404, detail="Carta no encontrada en la simulación.")
        # Recalculate and validate the printing before persisting the explicit choice.
        analysis = await SimulatedCollectionService._analyze_saved(user_id, coll, provider, {norm: printing_id})
        await db.simulatedcard.update_many(
            where={"simulatedCollectionId": collection_id, "cardName": {"in": [c.cardName for c in matches]}},
            data={"selectedPrintingId": printing_id},
        )
        return analysis

    @staticmethod
    async def mutate_card(user_id: str, collection_id: str, card_name: str,
                          provider: str, operation: str, quantity: int = 1) -> SimulatedCollectionAnalysisResponse:
        coll = await db.simulatedcollection.find_unique(where={"id": collection_id}, include={"cards": True})
        if not coll or coll.userId != user_id:
            raise HTTPException(status_code=404, detail="Colección simulada no encontrada.")
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
                    if normalize_card_name(entry_name) == target:
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
                    if any(normalize_card_name(entry.name) == target for entry in parsed):
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
        updated_names = {normalize_card_name(entry.name) for entry in updated_entries}
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
                "selectedPrintingId": retained_overrides.get(norm),
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
        return analysis

    @staticmethod
    async def delete_simulated_collection(user_id: str, collection_id: str) -> bool:
        coll = await db.simulatedcollection.find_unique(where={"id": collection_id})
        if not coll or coll.userId != user_id:
            return False
        await db.simulatedcollection.delete(where={"id": collection_id})
        return True
