import re
import logging
from types import SimpleNamespace
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.schemas.deck import (
    DeckCreate, DeckUpdate, DeckSummaryResponse, DeckDetailResponse,
    DeckCardWithOwnership, DeckCardCreate, OtherDeckAssignment, DeckRequirement
)
from src.services.card_utils import (
    normalize_card_name,
    get_card_category,
    is_basic_land,
    extract_colors_from_mana_cost,
    combine_colors_from_mana_costs,
    completion_percentages_by_deck,
    colors_by_deck,
)
from src.services.scryfall_service import ScryfallService
from src.services.import_service import parse_decklist_text
from src.services.enrichment_service import trigger_async_priority_enrichment
from src.services.image_resolver import resolve_minio_image_uris, safe_image_uri
from src.services.pricing_service import PricingService

DECK_PRICE_PROVIDER = "cardmarket"
DECK_PRICE_CURRENCY = "EUR"
DECK_PRICE_SYMBOL = "€"

logger = logging.getLogger("mtg_backend.decks")


def is_commander_candidate_type(type_line: Optional[str], oracle_text: Optional[str] = None) -> bool:
    """Return whether a card type is eligible for the commander picker.

    Legendary creatures and legendary vehicles are always candidates. Legendary
    planeswalkers are candidates only when their oracle text explicitly grants
    the ability with the "{name} can be your commander" clause.
    """
    type_value = (type_line or "").lower()
    is_legendary = "legendary" in type_value
    is_legendary_creature = is_legendary and "creature" in type_value
    is_legendary_vehicle = (
        is_legendary
        and "artifact" in type_value
        and "vehicle" in type_value
    )
    is_legendary_planeswalker = is_legendary and "planeswalker" in type_value
    allows_commander = bool(oracle_text) and "can be your commander" in (oracle_text or "").lower()
    return (
        is_legendary_creature
        or is_legendary_vehicle
        or (is_legendary_planeswalker and allows_commander)
    )


async def _is_commander_eligible(card_name: str, type_line: Optional[str]) -> bool:
    """Decide server-side whether a card may be the deck's commander.

    Legendary creatures and vehicles are decided by the type line alone.
    Legendary planeswalkers are only eligible when their oracle text states
    "{name} can be your commander"; the text is read from the local catalog
    (worker-hydrated details) with a live Scryfall lookup as last resort. The
    frontend consumes the resulting flag instead of re-deriving this rule.
    """
    type_value = (type_line or "").lower()
    if "legendary" not in type_value or "planeswalker" not in type_value:
        return is_commander_candidate_type(type_line)

    oracle_text: Optional[str] = None
    try:
        cat = await ScryfallService.get_catalog_card(card_name)
        if cat:
            oracle_text = cat.get("oracleText")
        if oracle_text is None:
            card = await ScryfallService.get_card_named(card_name)
            if card:
                oracle_text = card.get("oracle_text")
    except Exception:
        logger.warning("Could not resolve commander eligibility for %s", card_name, exc_info=True)

    return is_commander_candidate_type(type_line, oracle_text)


class DeckService:
    @staticmethod
    async def get_user_decks(user_id: str) -> List[DeckSummaryResponse]:
        decks = await db.deck.find_many(
            where={"userId": user_id},
            include={"cards": True},
            order={"updatedAt": "desc"}
        )

        # Get user collection
        collection_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map: Dict[str, int] = {}
        for c in (collection_cards or []):
            norm = normalize_card_name(c.cardName)
            col_map[norm] = col_map.get(norm, 0) + (c.quantity or 0)

        # Resolve market prices (DB only) for every unique card across all decks.
        all_cards = []
        for d in decks:
            all_cards.extend(
                {"name": c.cardName, "scryfallId": c.cardScryfallId, "quantity": c.quantity}
                for c in (d.cards or [])
            )
            if d.commander:
                cmd_in_cards = any(
                    c.isCommander or normalize_card_name(c.cardName) == normalize_card_name(d.commander)
                    for c in (d.cards or [])
                )
                if not cmd_in_cards:
                    all_cards.append({
                        "name": d.commander,
                        "scryfallId": d.commanderScryfallId,
                        "quantity": 1,
                    })

        price_memo: Dict[str, Optional[float]] = {}
        unique_cards = []
        seen_keys = set()
        for card in all_cards:
            card_id = card.get("scryfallId") or ""
            norm = normalize_card_name(card.get("name") or "")
            key = card_id or norm
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            unique_cards.append(card)

        batch_quotes = await PricingService.get_latest_quotes_batch(
            unique_cards,
            DECK_PRICE_PROVIDER,
            DECK_PRICE_CURRENCY,
        )
        for card in unique_cards:
            cid = card.get("scryfallId") or ""
            norm = normalize_card_name(card.get("name", ""))
            q = batch_quotes.get(cid) or batch_quotes.get(norm)
            price = q.unitPrice.trend if q else None
            if cid:
                price_memo[cid] = price
            if norm:
                price_memo[norm] = price

        # Resolve EDHREC Top 100 commanders to badge any user decks using them
        try:
            top100_commanders = await db.edhreccommander.find_many(where={"isTop100": True})
            top100_map: Dict[str, int] = {
                c.normalizedName: c.rank
                for c in (top100_commanders or [])
                if c.normalizedName and c.rank is not None
            }
        except Exception:
            top100_map = {}

        summaries: List[DeckSummaryResponse] = []
        for d in decks:
            cards = list(d.cards or [])
            has_cmd = any(
                c.isCommander or (d.commander and normalize_card_name(c.cardName) == normalize_card_name(d.commander))
                for c in cards
            )
            if d.commander and not has_cmd:
                cards.append(SimpleNamespace(
                    id=f"cmd:{d.id}",
                    cardName=d.commander,
                    cardScryfallId=d.commanderScryfallId,
                    quantity=1,
                    assignedQuantity=0,
                    isSideboard=False,
                    isCommander=True,
                    manaCost=None,
                    typeLine="Legendary Creature",
                ))

            unique = len(cards)

            # Calculate owned. Basic lands never count toward completion:
            # they are excluded from both the numerator (owned) and the
            # denominator (total), but still contribute to the deck's value.
            total = 0
            owned = 0
            total_value = 0.0
            missing_value = 0.0
            owned_value = 0.0
            for c in cards:
                if c.isSideboard and not getattr(c, "isCommander", False):
                    continue

                norm = normalize_card_name(c.cardName)
                is_basic = is_basic_land(c.typeLine, c.cardName)

                if is_basic:
                    actual_owned = c.quantity
                else:
                    in_col = col_map.get(norm, 0)
                    assigned = getattr(c, "assignedQuantity", 0) or 0
                    actual_owned = min(c.quantity, max(assigned, min(in_col, c.quantity)))

                total += c.quantity
                owned += actual_owned

                unit = (price_memo.get(c.cardScryfallId) if c.cardScryfallId else None) or price_memo.get(norm) or 0.0
                total_value += unit * c.quantity
                missing_value += unit * (c.quantity - actual_owned)
                owned_value += unit * actual_owned

            missing = max(0, total - owned)
            pct = round((owned / total) * 100, 1) if total > 0 else 0.0
            colors = combine_colors_from_mana_costs([c.manaCost for c in cards])

            cmd_norm = normalize_card_name(d.commander) if d.commander else ""
            is_top100 = cmd_norm in top100_map if cmd_norm else False
            top100_rank = top100_map.get(cmd_norm) if is_top100 else None

            summaries.append(DeckSummaryResponse(
                id=d.id,
                userId=d.userId,
                name=d.name,
                format=d.format,
                description=d.description,
                commander=d.commander,
                commanderScryfallId=d.commanderScryfallId,
                commanderImageUri=safe_image_uri(d.commanderImageUri),
                tags=[t.strip() for t in (d.tags or "").split(",") if t.strip()],
                isArchived=getattr(d, "isArchived", False),
                isCommanderTop100=is_top100,
                commanderEdhrecRank=top100_rank,
                createdAt=d.createdAt,
                updatedAt=d.updatedAt,
                totalCards=total,
                uniqueCards=unique,
                ownedCards=owned,
                missingCards=missing,
                completionPercentage=pct,
                colors=colors,
                colorIdentity="".join(colors),
                totalValue=round(total_value, 2) if total_value else None,
                missingValue=round(missing_value, 2) if missing_value else None,
                ownedValue=round(owned_value, 2) if owned_value else None,
                currency=DECK_PRICE_CURRENCY,
                currencySymbol=DECK_PRICE_SYMBOL,
            ))

        return summaries

    @staticmethod
    async def get_deck_detail(deck_id: str, user_id: str) -> Optional[DeckDetailResponse]:
        deck = await db.deck.find_unique(
            where={"id": deck_id},
            include={"cards": True}
        )
        if not deck:
            return None

        # Fetch owner collection cards
        collection_cards = await db.collectioncard.find_many(where={"userId": deck.userId})
        col_map: Dict[str, int] = {}
        for c in (collection_cards or []):
            norm = normalize_card_name(c.cardName)
            col_map[norm] = col_map.get(norm, 0) + (c.quantity or 0)

        deck_cards = list(deck.cards or [])
        has_cmd = any(
            c.isCommander or (deck.commander and normalize_card_name(c.cardName) == normalize_card_name(deck.commander))
            for c in deck_cards
        )
        if deck.commander and not has_cmd:
            deck_cards.append(SimpleNamespace(
                id=f"cmd:{deck.id}",
                deckId=deck.id,
                cardName=deck.commander,
                cardScryfallId=deck.commanderScryfallId,
                quantity=1,
                assignedQuantity=0,
                isSideboard=False,
                isCommander=True,
                manaCost=None,
                typeLine="Legendary Creature",
                imageUri=deck.commanderImageUri,
                setCode=None,
                tags=None,
            ))

        # Prefer locally-mirrored (MinIO) images over upstream Scryfall URLs.
        deck_card_ids = [c.cardScryfallId for c in deck_cards if c.cardScryfallId]
        if deck.commanderScryfallId and deck.commanderScryfallId not in deck_card_ids:
            deck_card_ids.append(deck.commanderScryfallId)
        local_images = await resolve_minio_image_uris(deck_card_ids)

        # Seed requested_in_decks with the current deck's cards
        decks_by_norm: Dict[str, Dict[str, DeckRequirement]] = {}
        for c in deck_cards:
            norm = normalize_card_name(c.cardName)
            if norm not in decks_by_norm:
                decks_by_norm[norm] = {}
            if deck.id not in decks_by_norm[norm]:
                decks_by_norm[norm][deck.id] = DeckRequirement(
                    deckId=deck.id,
                    deckName=deck.name,
                    quantity=c.quantity,
                )
            else:
                decks_by_norm[norm][deck.id].quantity += c.quantity

        # Fetch other cards of this user to detect cross-deck assignments and multi-deck demand
        user_deck_cards = await db.deckcard.find_many(
            where={"deck": {"userId": deck.userId}},
            include={"deck": True}
        )
        assigned_in_others: Dict[str, List[OtherDeckAssignment]] = {}
        for oc in (user_deck_cards or []):
            norm = normalize_card_name(oc.cardName)
            # 1. Assigned copies in other decks
            if oc.deckId != deck_id and (oc.assignedQuantity or 0) > 0:
                if norm not in assigned_in_others:
                    assigned_in_others[norm] = []
                assigned_in_others[norm].append(OtherDeckAssignment(
                    deckId=oc.deckId,
                    deckName=oc.deck.name if oc.deck else "Otro Mazo",
                    quantity=oc.assignedQuantity
                ))

            # 2. Decks requesting this card
            if oc.deckId != deck_id:
                deck_obj = getattr(oc, "deck", None)
                d_name = deck_obj.name if (deck_obj and isinstance(getattr(deck_obj, "name", None), str)) else "Otro Mazo"
                if norm not in decks_by_norm:
                    decks_by_norm[norm] = {}
                if oc.deckId not in decks_by_norm[norm]:
                    decks_by_norm[norm][oc.deckId] = DeckRequirement(
                        deckId=oc.deckId,
                        deckName=d_name,
                        quantity=oc.quantity,
                    )
                else:
                    decks_by_norm[norm][oc.deckId].quantity += oc.quantity

        completion = completion_percentages_by_deck(user_deck_cards, col_map)
        colors = colors_by_deck(user_deck_cards)
        for by_deck in decks_by_norm.values():
            for req in by_deck.values():
                req.completionPercentage = completion.get(req.deckId, 0.0)
                req.colors = colors.get(req.deckId, [])

        # Resolve market prices (DB only) for every unique card in this deck
        price_memo: Dict[str, Optional[float]] = {}
        unique_deck_cards = []
        seen_keys = set()
        for c in deck_cards:
            card_id = c.cardScryfallId or ""
            norm = normalize_card_name(c.cardName)
            key = card_id or norm
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            unique_deck_cards.append({"name": c.cardName, "scryfallId": card_id})

        try:
            batch_quotes = await PricingService.get_latest_quotes_batch(
                unique_deck_cards,
                DECK_PRICE_PROVIDER,
                DECK_PRICE_CURRENCY,
            )
            for card in unique_deck_cards:
                cid = card["scryfallId"]
                norm = normalize_card_name(card["name"])
                q = batch_quotes.get(cid) or batch_quotes.get(norm)
                val = q.unitPrice.trend if q else None
                if cid:
                    price_memo[cid] = val
                if norm:
                    price_memo[norm] = val
        except Exception:
            for card in unique_deck_cards:
                if card["scryfallId"]:
                    price_memo[card["scryfallId"]] = None
                norm = normalize_card_name(card["name"])
                if norm:
                    price_memo[norm] = None

        cards_with_ownership: List[DeckCardWithOwnership] = []
        total = 0
        owned = 0
        total_value = 0.0
        missing_value = 0.0
        owned_value = 0.0

        for c in deck_cards:
            norm = normalize_card_name(c.cardName)
            col_qty = col_map.get(norm, 0)
            other_assigned_list = assigned_in_others.get(norm, [])
            total_in_other_decks = sum(o.quantity for o in other_assigned_list)

            avail = max(0, col_qty - total_in_other_decks - c.assignedQuantity)

            # Basic lands count toward completion and are always 100% owned without
            # needing to be in the collection or moved to the deck.
            is_basic = is_basic_land(c.typeLine, c.cardName)
            if is_basic:
                actual_owned = c.quantity
                missing = 0
            else:
                actual_owned = min(c.quantity, max(c.assignedQuantity or 0, min(col_qty, c.quantity)))
                missing = max(0, c.quantity - actual_owned)

            if not c.isSideboard or getattr(c, "isCommander", False):
                total += c.quantity
                owned += actual_owned

            unit = (price_memo.get(c.cardScryfallId) if c.cardScryfallId else None) or price_memo.get(norm) or 0.0

            total_value += unit * c.quantity
            missing_value += unit * missing
            owned_value += unit * actual_owned

            # Auto-enrich type line if missing
            type_line = c.typeLine
            mana_cost = c.manaCost
            img_uri = local_images.get(c.cardScryfallId) or safe_image_uri(c.imageUri)

            if not type_line or not img_uri:
                # Enrichment is deliberately best effort. A catalog/Scryfall
                # outage (or a stale card row) must not turn an otherwise
                # readable deck into a 500 response.
                try:
                    cat = await ScryfallService.get_or_resolve_catalog_card(c.cardName)
                    if cat:
                        type_line = type_line or cat.get("typeLine")
                        mana_cost = mana_cost or cat.get("manaCost")
                        img_uri = img_uri or cat.get("imageUri")
                        if not str(c.id).startswith("cmd:"):
                            await db.deckcard.update(
                                where={"id": c.id},
                                data={
                                    "typeLine": type_line,
                                    "manaCost": mana_cost,
                                    "imageUri": img_uri,
                                }
                            )
                except Exception:
                    logger.warning(
                        "Could not enrich deck card %s (%s); returning stored values",
                        c.id,
                        c.cardName,
                        exc_info=True,
                    )

            can_be_commander = await _is_commander_eligible(c.cardName, type_line)

            req_dict = decks_by_norm.get(norm, {})
            req_list = sorted(
                list(req_dict.values()),
                key=lambda r: (0 if r.deckId == deck_id else 1, r.deckName.lower())
            )
            req_count = len(req_list)

            cards_with_ownership.append(DeckCardWithOwnership(
                id=c.id,
                deckId=c.deckId,
                cardScryfallId=c.cardScryfallId,
                cardName=c.cardName,
                quantity=c.quantity,
                assignedQuantity=c.assignedQuantity,
                isSideboard=c.isSideboard,
                isCommander=c.isCommander,
                manaCost=mana_cost,
                typeLine=type_line,
                imageUri=img_uri,
                setCode=c.setCode,
                ownedInCollection=c.quantity if is_basic else col_qty,
                availableToAssign=avail,
                assignedInOtherDecks=other_assigned_list,
                requestedInDecks=req_list,
                requestedInDecksCount=req_count,
                missingCount=0 if is_basic else missing,
                canBeCommander=can_be_commander,
                tags=[t.strip() for t in (c.tags or "").split(",") if t.strip()],
            ))

        missing_cards = max(0, total - owned)
        pct = round((owned / total) * 100, 1) if total > 0 else 0.0

        net_val = round(total_value, 2) if total_value else None
        miss_val = round(missing_value, 2) if missing_value else None
        own_val = round(net_val - miss_val, 2) if (net_val is not None and miss_val is not None) else (round(owned_value, 2) if owned_value else None)

        is_top100 = False
        top100_rank = None
        if deck.commander:
            try:
                top_cmd = await db.edhreccommander.find_unique(where={"normalizedName": normalize_card_name(deck.commander)})
                if top_cmd and top_cmd.isTop100:
                    is_top100 = True
                    top100_rank = top_cmd.rank
            except Exception:
                pass

        return DeckDetailResponse(
            id=deck.id,
            userId=deck.userId,
            name=deck.name,
            format=deck.format,
            description=deck.description,
            commander=deck.commander,
            commanderScryfallId=deck.commanderScryfallId,
            commanderImageUri=safe_image_uri(deck.commanderImageUri),
            tags=[t.strip() for t in (deck.tags or "").split(",") if t.strip()],
            isArchived=getattr(deck, "isArchived", False),
            isCommanderTop100=is_top100,
            commanderEdhrecRank=top100_rank,
            createdAt=deck.createdAt,
            updatedAt=deck.updatedAt,
            totalCards=total,
            uniqueCards=len(cards_with_ownership),
            ownedCards=owned,
            missingCards=missing_cards,
            completionPercentage=pct,
            totalValue=net_val,
            missingValue=miss_val,
            ownedValue=own_val,
            currency=DECK_PRICE_CURRENCY,
            currencySymbol=DECK_PRICE_SYMBOL,
            cards=cards_with_ownership,
        )

    @staticmethod
    async def create_deck(user_id: str, data: DeckCreate) -> DeckSummaryResponse:
        tags_str = ",".join([t.strip() for t in (data.tags or []) if t.strip()]) if data.tags else ""
        created = await db.deck.create(
            data={
                "userId": user_id,
                "name": data.name,
                "format": data.format,
                "description": data.description,
                "commander": data.commander,
                "commanderScryfallId": data.commanderScryfallId,
                "commanderImageUri": data.commanderImageUri,
                "tags": tags_str,
            }
        )

        # If commander provided, add commander card to deck
        commander_colors: List[str] = []
        if data.commander and data.commander.strip():
            cmd_name = data.commander.strip()
            cat = await ScryfallService.get_catalog_card(cmd_name)
            scry_id = data.commanderScryfallId or (cat.get("id") if cat else f"pending:{cmd_name}")
            img_uri = data.commanderImageUri or (cat.get("imageUri") if cat else None)
            mana_cost = cat.get("manaCost") if cat else None
            type_line = cat.get("typeLine") if cat else "Legendary Creature"
            commander_colors = extract_colors_from_mana_cost(mana_cost)

            await db.deckcard.create(
                data={
                    "deckId": created.id,
                    "cardScryfallId": scry_id,
                    "cardName": cmd_name,
                    "quantity": 1,
                    "assignedQuantity": 0,
                    "isSideboard": False,
                    "isCommander": True,
                    "manaCost": mana_cost,
                    "typeLine": type_line,
                    "imageUri": img_uri,
                }
            )

            # Prioritize resolution of the freshly added commander immediately
            trigger_async_priority_enrichment([cmd_name])

        return DeckSummaryResponse(
            id=created.id,
            userId=created.userId,
            name=created.name,
            format=created.format,
            description=created.description,
            commander=created.commander,
            commanderScryfallId=created.commanderScryfallId,
            commanderImageUri=created.commanderImageUri,
            tags=[t.strip() for t in (data.tags or []) if t.strip()],
            isArchived=getattr(created, "isArchived", False),
            createdAt=created.createdAt,
            updatedAt=created.updatedAt,
            totalCards=1 if data.commander else 0,
            uniqueCards=1 if data.commander else 0,
            ownedCards=0,
            missingCards=1 if data.commander else 0,
            completionPercentage=0.0,
            colors=commander_colors,
            colorIdentity="".join(commander_colors),
            totalValue=None,
            missingValue=None,
            ownedValue=None,
            currency=DECK_PRICE_CURRENCY,
            currencySymbol=DECK_PRICE_SYMBOL,
        )

    @staticmethod
    async def update_deck(deck_id: str, user_id: str, data: DeckUpdate) -> Optional[DeckDetailResponse]:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return None

        update_data: Dict[str, Any] = {}
        if data.name is not None:
            update_data["name"] = data.name.strip()
        if data.format is not None:
            update_data["format"] = data.format
        if data.description is not None:
            update_data["description"] = data.description.strip()
        if data.tags is not None:
            update_data["tags"] = ",".join([t.strip() for t in data.tags if t.strip()])
        if data.isArchived is not None:
            update_data["isArchived"] = data.isArchived

        # Handle commander update if provided
        new_cmd = None
        if data.commander is not None:
            new_cmd = data.commander.strip()
            if new_cmd:
                scry_id = data.commanderScryfallId
                img_uri = data.commanderImageUri
                mana_cost = None
                type_line = "Legendary Creature"

                if not scry_id or not img_uri:
                    cat = await ScryfallService.get_catalog_card(new_cmd)
                    if cat:
                        scry_id = scry_id or cat.get("id")
                        img_uri = img_uri or cat.get("imageUri")
                        mana_cost = cat.get("manaCost")
                        type_line = cat.get("typeLine") or "Legendary Creature"

                update_data["commander"] = new_cmd
                update_data["commanderScryfallId"] = scry_id
                update_data["commanderImageUri"] = img_uri

                # Reset isCommander on existing cards in this deck
                await db.deckcard.update_many(
                    where={"deckId": deck_id},
                    data={"isCommander": False}
                )

                # Check if card already exists in the deck
                existing_card = await db.deckcard.find_first(
                    where={"deckId": deck_id, "cardName": {"equals": new_cmd, "mode": "insensitive"}}
                )

                if existing_card:
                    await db.deckcard.update(
                        where={"id": existing_card.id},
                        data={
                            "isCommander": True,
                            "cardName": new_cmd,
                            "imageUri": img_uri or existing_card.imageUri,
                        }
                    )
                else:
                    await db.deckcard.create(
                        data={
                            "deckId": deck_id,
                            "cardScryfallId": scry_id or f"cmd:{new_cmd}",
                            "cardName": new_cmd,
                            "quantity": 1,
                            "assignedQuantity": 0,
                            "isSideboard": False,
                            "isCommander": True,
                            "manaCost": mana_cost,
                            "typeLine": type_line,
                            "imageUri": img_uri,
                        }
                    )
            else:
                # Explicitly empty string - clear commander
                update_data["commander"] = None
                update_data["commanderScryfallId"] = None
                update_data["commanderImageUri"] = None
                await db.deckcard.update_many(
                    where={"deckId": deck_id},
                    data={"isCommander": False}
                )

        if update_data and new_cmd:
            trigger_async_priority_enrichment([new_cmd])

        if update_data:
            await db.deck.update(
                where={"id": deck_id},
                data=update_data,
            )

        return await DeckService.get_deck_detail(deck_id, user_id)

    @staticmethod
    async def get_deletion_impact(deck_id: str, user_id: str) -> Dict[str, Any]:
        deck = await db.deck.find_unique(where={"id": deck_id}, include={"cards": True})
        if not deck or deck.userId != user_id:
            return {"error": "Mazo no encontrado"}

        assigned_cards = [c for c in (deck.cards or []) if (c.assignedQuantity or 0) > 0]
        if not assigned_cards:
            return {
                "deckId": deck_id,
                "deckName": deck.name,
                "totalAssignedCards": 0,
                "uniqueAssignedCards": 0,
                "dominoCandidates": [],
            }

        other_decks = await db.deck.find_many(
            where={"userId": user_id, "id": {"not": deck_id}, "isArchived": False},
            include={"cards": True},
        )
        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map = {normalize_card_name(c.cardName): c.quantity for c in col_cards}
        all_deck_cards = [c for d in other_decks for c in (d.cards or [])]
        completions = completion_percentages_by_deck(all_deck_cards, col_map)

        domino_candidates = []
        for ac in assigned_cards:
            ac_norm = normalize_card_name(ac.cardName)
            released_qty = ac.assignedQuantity

            for od in other_decks:
                for oc in (od.cards or []):
                    if normalize_card_name(oc.cardName) == ac_norm:
                        missing = max(0, oc.quantity - (oc.assignedQuantity or 0))
                        if missing > 0:
                            qty_to_give = min(released_qty, missing)
                            domino_candidates.append({
                                "cardName": oc.cardName,
                                "cardScryfallId": oc.cardScryfallId,
                                "releasedQuantity": released_qty,
                                "targetDeckId": od.id,
                                "targetDeckName": od.name,
                                "targetDeckCardId": oc.id,
                                "targetDeckCompletion": completions.get(od.id, 0.0),
                                "neededQuantity": missing,
                                "canReassign": qty_to_give,
                            })

        return {
            "deckId": deck_id,
            "deckName": deck.name,
            "totalAssignedCards": sum(c.assignedQuantity for c in assigned_cards),
            "uniqueAssignedCards": len(assigned_cards),
            "dominoCandidates": domino_candidates,
        }

    @staticmethod
    async def delete_deck(
        deck_id: str,
        user_id: str,
        reassign_card_ids: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return False

        if reassign_card_ids:
            for item in reassign_card_ids:
                target_card_id = item.get("targetDeckCardId")
                qty = item.get("quantity", 1)
                if target_card_id:
                    target_card = await db.deckcard.find_unique(where={"id": target_card_id}, include={"deck": True})
                    if target_card and target_card.deck and target_card.deck.userId == user_id:
                        new_assigned = min(target_card.quantity, (target_card.assignedQuantity or 0) + qty)
                        await db.deckcard.update(
                            where={"id": target_card_id},
                            data={"assignedQuantity": new_assigned},
                        )

        await db.deckcard.delete_many(where={"deckId": deck_id})
        await db.deck.delete(where={"id": deck_id})
        return True

    @classmethod
    async def get_decks_overlap(cls, user_id: str) -> Dict[str, Any]:
        decks = await db.deck.find_many(
            where={"userId": user_id, "isArchived": False},
            include={"cards": True},
            order={"name": "asc"}
        )

        deck_summaries = []
        deck_cards_map = {}

        for d in decks:
            non_basic_cards = [c for c in (d.cards or []) if not is_basic_land(c.typeLine, c.cardName)]
            deck_summaries.append({
                "id": d.id,
                "name": d.name,
                "commander": d.commander,
                "commanderImageUri": d.commanderImageUri,
                "colors": d.colors or [],
                "totalCards": sum(c.quantity for c in (d.cards or [])),
                "nonBasicCardsCount": len(non_basic_cards),
            })

            card_dict = {}
            for c in non_basic_cards:
                norm = normalize_card_name(c.cardName)
                card_dict[norm] = {
                    "cardId": c.id,
                    "cardName": c.cardName,
                    "cardScryfallId": c.cardScryfallId,
                    "quantity": c.quantity,
                    "assignedQuantity": c.assignedQuantity or 0,
                    "manaCost": c.manaCost,
                    "typeLine": c.typeLine,
                    "imageUri": c.imageUri,
                }
            deck_cards_map[d.id] = card_dict

        # Calculate pairwise overlap
        pairs = []
        for i in range(len(decks)):
            for j in range(i + 1, len(decks)):
                dA = decks[i]
                dB = decks[j]
                mapA = deck_cards_map[dA.id]
                mapB = deck_cards_map[dB.id]

                shared_keys = set(mapA.keys()).intersection(set(mapB.keys()))
                shared_cards = []
                for k in sorted(shared_keys):
                    itemA = mapA[k]
                    itemB = mapB[k]
                    shared_cards.append({
                        "cardName": itemA["cardName"],
                        "cardScryfallId": itemA["cardScryfallId"],
                        "imageUri": itemA["imageUri"] or itemB["imageUri"],
                        "manaCost": itemA["manaCost"] or itemB["manaCost"],
                        "typeLine": itemA["typeLine"] or itemB["typeLine"],
                        "deckACardId": itemA["cardId"],
                        "deckAQuantity": itemA["quantity"],
                        "deckAAssigned": itemA["assignedQuantity"],
                        "deckBCardId": itemB["cardId"],
                        "deckBQuantity": itemB["quantity"],
                        "deckBAssigned": itemB["assignedQuantity"],
                    })

                min_cards = min(len(mapA), len(mapB))
                overlap_pct = round((len(shared_keys) / min_cards * 100), 1) if min_cards > 0 else 0.0

                pairs.append({
                    "deckAId": dA.id,
                    "deckAName": dA.name,
                    "deckBId": dB.id,
                    "deckBName": dB.name,
                    "sharedCount": len(shared_keys),
                    "overlapPercentage": overlap_pct,
                    "sharedCards": shared_cards,
                })

        return {
            "decks": deck_summaries,
            "pairs": pairs,
        }


    @staticmethod
    async def set_commander(
        deck_id: str,
        user_id: str,
        commander_name: str,
        scryfall_id: Optional[str] = None,
        image_uri: Optional[str] = None,
        partner_name: Optional[str] = None,
        partner_scryfall_id: Optional[str] = None,
        partner_image_uri: Optional[str] = None,
    ) -> bool:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return False

        partner_name = partner_name.strip() if partner_name and partner_name.strip() else None
        if partner_name:
            partner_scryfall_id = partner_scryfall_id or None
            partner_image_uri = partner_image_uri or None

        # If scryfall metadata missing, resolve
        if not scryfall_id or not image_uri:
            cat = await ScryfallService.get_catalog_card(commander_name)
            if cat:
                scryfall_id = scryfall_id or cat.get("id")
                image_uri = image_uri or cat.get("imageUri")
        if partner_name and (not partner_scryfall_id or not partner_image_uri):
            cat = await ScryfallService.get_catalog_card(partner_name)
            if cat:
                partner_scryfall_id = partner_scryfall_id or cat.get("id")
                partner_image_uri = partner_image_uri or cat.get("imageUri")

        commander_value = f"{commander_name} // {partner_name}" if partner_name else commander_name

        # Reset isCommander on all cards in this deck
        await db.deckcard.update_many(
            where={"deckId": deck_id},
            data={"isCommander": False}
        )

        # Update deck commander
        await db.deck.update(
            where={"id": deck_id},
            data={
                "commander": commander_value,
                "commanderScryfallId": scryfall_id,
                "commanderImageUri": image_uri,
            }
        )

        # Check if card exists in deck
        commanders = [(commander_name, scryfall_id, image_uri)]
        if partner_name:
            commanders.append((partner_name, partner_scryfall_id, partner_image_uri))
        for name, card_id, card_image in commanders:
            existing = await db.deckcard.find_first(
                where={"deckId": deck_id, "cardName": {"equals": name, "mode": "insensitive"}}
            )
            if existing:
                await db.deckcard.update(where={"id": existing.id}, data={"isCommander": True})
            else:
                await db.deckcard.create(data={
                    "deckId": deck_id,
                    "cardScryfallId": card_id or f"pending:{name}",
                    "cardName": name,
                    "quantity": 1,
                    "assignedQuantity": 0,
                    "isSideboard": False,
                    "isCommander": True,
                    "imageUri": card_image,
                })

        # Prioritize resolution of the newly set commander immediately
        trigger_async_priority_enrichment([name for name, _, _ in commanders])

        return True

    @staticmethod
    async def add_card_to_deck(deck_id: str, user_id: str, data: DeckCardCreate) -> bool:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return False

        # The request path is database-only. The priority worker owns the
        # Scryfall fallback when this card is not in CardCatalog yet.
        cat = await ScryfallService.get_catalog_card(data.cardName)
        mana_cost = data.manaCost or (cat.get("manaCost") if cat else None)
        type_line = data.typeLine or (cat.get("typeLine") if cat else None)
        image_uri = data.imageUri or (cat.get("imageUri") if cat else None)
        scry_id = data.cardScryfallId or (cat.get("id") if cat else f"pending:{data.cardName}")

        existing = await db.deckcard.find_first(
            where={"deckId": deck_id, "cardScryfallId": scry_id, "isSideboard": data.isSideboard}
        )

        if existing:
            await db.deckcard.update(
                where={"id": existing.id},
                data={"quantity": existing.quantity + data.quantity}
            )
        else:
            tags_str = ",".join([t.strip() for t in (data.tags or []) if t.strip()]) if data.tags else ""
            await db.deckcard.create(
                data={
                    "deckId": deck_id,
                    "cardScryfallId": scry_id,
                    "cardName": data.cardName,
                    "quantity": data.quantity,
                    "assignedQuantity": 0,
                    "isSideboard": data.isSideboard,
                    "isCommander": data.isCommander,
                    "manaCost": mana_cost,
                    "typeLine": type_line,
                    "imageUri": image_uri,
                    "setCode": data.setCode,
                    "tags": tags_str,
                }
            )

        # Prioritize resolution of the freshly added card immediately
        trigger_async_priority_enrichment([data.cardName])

        return True

    @staticmethod
    async def update_card_quantity(
        card_id: str,
        user_id: str,
        quantity: int,
        set_code: Optional[str] = None,
        *,
        set_code_provided: bool = False,
    ) -> bool:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return False

        if quantity <= 0:
            await db.deckcard.delete(where={"id": card_id})
        else:
            new_assigned = min(card.assignedQuantity, quantity)
            update_data: Dict[str, Any] = {
                "quantity": quantity,
                "assignedQuantity": new_assigned,
            }
            if set_code_provided:
                update_data["setCode"] = set_code or None
            await db.deckcard.update(
                where={"id": card_id},
                data=update_data
            )
        return True

    @staticmethod
    async def update_card_tags(deck_card_id: str, user_id: str, tags: List[str]) -> Optional[Dict[str, Any]]:
        card = await db.deckcard.find_unique(where={"id": deck_card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return None
        clean_tags = [t.strip() for t in tags if t.strip()]
        tags_str = ",".join(clean_tags)
        await db.deckcard.update(
            where={"id": deck_card_id},
            data={"tags": tags_str}
        )
        return {"status": "success", "cardId": deck_card_id, "tags": clean_tags}

    @classmethod
    async def move_card_sideboard(cls, card_id: str, user_id: str, is_sideboard: bool) -> Optional[Dict[str, Any]]:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return None
        if card.isSideboard == is_sideboard:
            return {"status": "success", "cardId": card.id, "isSideboard": card.isSideboard, "merged": False}

        # Check if destination board already contains this card
        conflict = await db.deckcard.find_first(
            where={
                "deckId": card.deckId,
                "cardScryfallId": card.cardScryfallId,
                "isSideboard": is_sideboard,
                "id": {"not": card_id},
            }
        )
        if conflict:
            await db.deckcard.update(
                where={"id": conflict.id},
                data={"quantity": conflict.quantity + card.quantity},
            )
            await db.deckcard.delete(where={"id": card_id})
            return {"status": "success", "cardId": conflict.id, "isSideboard": is_sideboard, "merged": True}
        else:
            await db.deckcard.update(
                where={"id": card_id},
                data={"isSideboard": is_sideboard},
            )
            return {"status": "success", "cardId": card.id, "isSideboard": is_sideboard, "merged": False}

    @staticmethod
    async def update_deck_tags(deck_id: str, user_id: str, tags: List[str]) -> Optional[Dict[str, Any]]:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return None
        clean_tags = [t.strip() for t in tags if t.strip()]
        tags_str = ",".join(clean_tags)
        await db.deck.update(
            where={"id": deck_id},
            data={"tags": tags_str}
        )
        return {"status": "success", "deckId": deck_id, "tags": clean_tags}

    @staticmethod
    async def update_card_version(
        card_id: str,
        user_id: str,
        card_scryfall_id: str,
        image_uri: Optional[str] = None,
        set_code: Optional[str] = None,
    ) -> bool:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return False

        effective_image = safe_image_uri(image_uri) or image_uri

        if card.cardScryfallId == card_scryfall_id:
            update_data: Dict[str, Any] = {}
            if effective_image:
                update_data["imageUri"] = effective_image
            if set_code is not None:
                update_data["setCode"] = set_code
            if update_data:
                await db.deckcard.update(where={"id": card_id}, data=update_data)
        else:
            conflict = await db.deckcard.find_first(
                where={
                    "deckId": card.deckId,
                    "cardScryfallId": card_scryfall_id,
                    "isSideboard": card.isSideboard,
                    "id": {"not": card_id},
                }
            )
            if conflict:
                await db.deckcard.update(
                    where={"id": conflict.id},
                    data={
                        "quantity": conflict.quantity + card.quantity,
                        "assignedQuantity": conflict.assignedQuantity + card.assignedQuantity,
                        "imageUri": effective_image or conflict.imageUri,
                        "setCode": set_code or conflict.setCode,
                    },
                )
                await db.deckcard.delete(where={"id": card_id})
            else:
                update_data = {
                    "cardScryfallId": card_scryfall_id,
                }
                if effective_image:
                    update_data["imageUri"] = effective_image
                if set_code is not None:
                    update_data["setCode"] = set_code
                await db.deckcard.update(
                    where={"id": card_id},
                    data=update_data,
                )

        is_cmd = card.isCommander
        if not is_cmd and card.deck.commander:
            is_cmd = (
                normalize_card_name(card.deck.commander) == normalize_card_name(card.cardName)
                or card.deck.commanderScryfallId == card.cardScryfallId
            )

        if is_cmd:
            await db.deck.update(
                where={"id": card.deckId},
                data={
                    "commanderScryfallId": card_scryfall_id,
                    "commanderImageUri": effective_image or card.imageUri,
                },
            )

        return True

    @staticmethod
    async def remove_card(card_id: str, user_id: str) -> bool:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return False

        await db.deckcard.delete(where={"id": card_id})
        return True

    @staticmethod
    async def assign_card(card_id: str, user_id: str, quantity: int = 1) -> bool:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return False

        new_qty = min(card.quantity, card.assignedQuantity + quantity)
        await db.deckcard.update(where={"id": card_id}, data={"assignedQuantity": new_qty})
        return True

    @staticmethod
    async def release_card(card_id: str, user_id: str, quantity: int = 1) -> bool:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return False

        new_qty = max(0, card.assignedQuantity - quantity)
        await db.deckcard.update(where={"id": card_id}, data={"assignedQuantity": new_qty})
        return True

    @staticmethod
    async def reassign_card(card_id: str, user_id: str, source_deck_id: str, quantity: int = 1) -> bool:
        dest_card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not dest_card or not dest_card.deck or dest_card.deck.userId != user_id:
            return False

        norm = normalize_card_name(dest_card.cardName)
        source_cards = await db.deckcard.find_many(
            where={"deckId": source_deck_id, "assignedQuantity": {"gt": 0}}
        )
        source_card = next((c for c in source_cards if normalize_card_name(c.cardName) == norm), None)
        if not source_card:
            return False

        transfer = min(quantity, source_card.assignedQuantity)
        await db.deckcard.update(
            where={"id": source_card.id},
            data={"assignedQuantity": max(0, source_card.assignedQuantity - transfer)}
        )
        await db.deckcard.update(
            where={"id": dest_card.id},
            data={"assignedQuantity": min(dest_card.quantity, dest_card.assignedQuantity + transfer)}
        )
        return True

    @staticmethod
    async def add_missing_cards_to_collection(deck_id: str, user_id: str) -> Dict[str, Any]:
        detail = await DeckService.get_deck_detail(deck_id, user_id)
        if not detail:
            return {"status": "error", "addedCount": 0}

        added = 0
        for card in detail.cards:
            missing = card.missingCount
            if missing > 0:
                existing = await db.collectioncard.find_unique(
                    where={"userId_cardScryfallId": {"userId": user_id, "cardScryfallId": card.cardScryfallId, "isFoil": False}}
                )
                if not existing:
                    existing = await db.collectioncard.find_first(
                        where={"userId": user_id, "cardName": {"equals": card.cardName, "mode": "insensitive"}, "isFoil": False}
                    )

                if existing:
                    await db.collectioncard.update(
                        where={"id": existing.id},
                        data={"quantity": existing.quantity + missing}
                    )
                else:
                    await db.collectioncard.create(
                        data={
                            "userId": user_id,
                            "cardScryfallId": card.cardScryfallId,
                            "cardName": card.cardName,
                            "quantity": missing,
                            "manaCost": card.manaCost,
                            "typeLine": card.typeLine,
                            "imageUri": card.imageUri,
                        }
                    )
                added += missing

            # Ensure the card is assigned to this deck
            if card.assignedQuantity < card.quantity:
                await db.deckcard.update(
                    where={"id": card.id},
                    data={"assignedQuantity": card.quantity}
                )

        return {"status": "success", "addedCount": added}

    @staticmethod
    async def add_missing_card_to_collection(card_id: str, user_id: str) -> Dict[str, Any]:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return {"status": "error", "message": "Carta o mazo no encontrado", "addedCount": 0}

        detail = await DeckService.get_deck_detail(card.deckId, user_id)
        if not detail:
            return {"status": "error", "message": "No se pudo cargar el detalle del mazo", "addedCount": 0}

        deck_card = next((c for c in detail.cards if c.id == card_id), None)
        missing = deck_card.missingCount if deck_card else max(0, card.quantity - card.assignedQuantity)

        added = 0
        if missing > 0:
            existing = await db.collectioncard.find_unique(
                where={"userId_cardScryfallId": {"userId": user_id, "cardScryfallId": card.cardScryfallId, "isFoil": False}}
            )
            if not existing:
                existing = await db.collectioncard.find_first(
                    where={"userId": user_id, "cardName": {"equals": card.cardName, "mode": "insensitive"}, "isFoil": False}
                )

            if existing:
                await db.collectioncard.update(
                    where={"id": existing.id},
                    data={"quantity": existing.quantity + missing}
                )
            else:
                await db.collectioncard.create(
                    data={
                        "userId": user_id,
                        "cardScryfallId": card.cardScryfallId,
                        "cardName": card.cardName,
                        "quantity": missing,
                        "manaCost": card.manaCost,
                        "typeLine": card.typeLine,
                        "imageUri": card.imageUri,
                    }
                )
            added = missing

        # Ensure assigned in this deck
        await db.deckcard.update(
            where={"id": card.id},
            data={"assignedQuantity": card.quantity}
        )

        return {"status": "success", "addedCount": added}

    @staticmethod
    async def import_deck_text(user_id: str, name: str, raw_text: str, format: str = "Commander", commander: Optional[str] = None) -> DeckSummaryResponse:
        parsed_cards = parse_decklist_text(raw_text)

        # Exporters can repeat a card in multiple sections/lines. Consolidate
        # equivalent entries before inserting because DeckCard has a unique
        # constraint on (deck, card id, sideboard).
        consolidated = {}
        for item in parsed_cards:
            key = (normalize_card_name(item.name), item.isSideboard, item.setCode, item.collectorNumber)
            if key not in consolidated:
                consolidated[key] = item
            else:
                consolidated[key].quantity += item.quantity
                consolidated[key].isCommander = consolidated[key].isCommander or item.isCommander
        parsed_cards = list(consolidated.values())

        # Detect commander from parsed cards if marked or not provided
        cmd_candidate = commander
        for p in parsed_cards:
            if p.isCommander and not cmd_candidate:
                cmd_candidate = p.name
                break

        deck = await db.deck.create(
            data={
                "userId": user_id,
                "name": name,
                "format": format,
                "commander": cmd_candidate,
            }
        )

        # Bulk create cards
        imported_mana_costs: List[Optional[str]] = []
        for item in parsed_cards:
            cat = await ScryfallService.get_catalog_card(item.name)
            scry_id = cat.get("id") if cat else f"pending:{item.name}"
            is_cmd = item.isCommander or (cmd_candidate and normalize_card_name(item.name) == normalize_card_name(cmd_candidate))
            imported_mana_costs.append(cat.get("manaCost") if cat else None)

            await db.deckcard.create(
                data={
                    "deckId": deck.id,
                    "cardScryfallId": scry_id,
                    "cardName": item.name,
                    "quantity": item.quantity,
                    "assignedQuantity": 0,
                    "isSideboard": item.isSideboard,
                    "isCommander": bool(is_cmd),
                    "manaCost": cat.get("manaCost") if cat else None,
                    "typeLine": cat.get("typeLine") if cat else None,
                    "imageUri": cat.get("imageUri") if cat else None,
                }
            )

        # Prioritize resolution of all freshly imported cards immediately
        trigger_async_priority_enrichment([item.name for item in parsed_cards])

        colors = combine_colors_from_mana_costs(imported_mana_costs)
        return DeckSummaryResponse(
            id=deck.id,
            userId=deck.userId,
            name=deck.name,
            format=deck.format,
            commander=deck.commander,
            tags=[],
            isArchived=False,
            createdAt=deck.createdAt,
            updatedAt=deck.updatedAt,
            totalCards=sum(c.quantity for c in parsed_cards),
            uniqueCards=len(parsed_cards),
            ownedCards=0,
            missingCards=sum(c.quantity for c in parsed_cards),
            completionPercentage=0.0,
            colors=colors,
            colorIdentity="".join(colors),
            totalValue=None,
            missingValue=None,
            ownedValue=None,
            currency=DECK_PRICE_CURRENCY,
            currencySymbol=DECK_PRICE_SYMBOL,
        )
