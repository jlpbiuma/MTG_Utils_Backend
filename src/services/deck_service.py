import re
import logging
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.schemas.deck import (
    DeckCreate, DeckUpdate, DeckSummaryResponse, DeckDetailResponse,
    DeckCardWithOwnership, DeckCardCreate, OtherDeckAssignment
)
from src.services.card_utils import normalize_card_name, get_card_category
from src.services.scryfall_service import ScryfallService
from src.services.import_service import parse_decklist_text

logger = logging.getLogger("mtg_backend.decks")

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
        col_map = {normalize_card_name(c.cardName): c.quantity for c in collection_cards}

        summaries: List[DeckSummaryResponse] = []
        for d in decks:
            cards = d.cards or []
            total = sum(c.quantity for c in cards)
            unique = len(cards)

            # Calculate owned
            owned = 0
            for c in cards:
                norm = normalize_card_name(c.cardName)
                in_col = col_map.get(norm, 0)
                assigned = c.assignedQuantity or 0
                actual_owned = min(c.quantity, max(assigned, min(in_col, c.quantity)))
                owned += actual_owned

            missing = max(0, total - owned)
            pct = round((owned / total) * 100, 1) if total > 0 else 0.0

            summaries.append(DeckSummaryResponse(
                id=d.id,
                userId=d.userId,
                name=d.name,
                format=d.format,
                description=d.description,
                commander=d.commander,
                commanderScryfallId=d.commanderScryfallId,
                commanderImageUri=d.commanderImageUri,
                createdAt=d.createdAt,
                updatedAt=d.updatedAt,
                totalCards=total,
                uniqueCards=unique,
                ownedCards=owned,
                missingCards=missing,
                completionPercentage=pct,
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
        col_map = {normalize_card_name(c.cardName): c.quantity for c in collection_cards}

        # Fetch other decks cards to detect cross-deck assignments
        other_cards = await db.deckcard.find_many(
            where={"deckId": {"not": deck_id}, "assignedQuantity": {"gt": 0}},
            include={"deck": True}
        )
        assigned_in_others: Dict[str, List[OtherDeckAssignment]] = {}
        for oc in other_cards:
            norm = normalize_card_name(oc.cardName)
            if norm not in assigned_in_others:
                assigned_in_others[norm] = []
            assigned_in_others[norm].append(OtherDeckAssignment(
                deckId=oc.deckId,
                deckName=oc.deck.name if oc.deck else "Otro Mazo",
                quantity=oc.assignedQuantity
            ))

        cards_with_ownership: List[DeckCardWithOwnership] = []
        total = 0
        owned = 0

        for c in (deck.cards or []):
            norm = normalize_card_name(c.cardName)
            col_qty = col_map.get(norm, 0)
            other_assigned_list = assigned_in_others.get(norm, [])
            total_in_other_decks = sum(o.quantity for o in other_assigned_list)

            avail = max(0, col_qty - total_in_other_decks - c.assignedQuantity)
            missing = max(0, c.quantity - c.assignedQuantity)

            total += c.quantity
            owned += c.assignedQuantity

            # Auto-enrich type line if missing
            type_line = c.typeLine
            mana_cost = c.manaCost
            img_uri = c.imageUri

            if not type_line or not img_uri:
                cat = await ScryfallService.get_or_resolve_catalog_card(c.cardName)
                if cat:
                    type_line = type_line or cat.get("typeLine")
                    mana_cost = mana_cost or cat.get("manaCost")
                    img_uri = img_uri or cat.get("imageUri")
                    # Update in background / async
                    await db.deckcard.update(
                        where={"id": c.id},
                        data={
                            "typeLine": type_line,
                            "manaCost": mana_cost,
                            "imageUri": img_uri,
                        }
                    )

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
                ownedInCollection=col_qty,
                availableToAssign=avail,
                assignedInOtherDecks=other_assigned_list,
                missingCount=missing,
            ))

        missing_cards = max(0, total - owned)
        pct = round((owned / total) * 100, 1) if total > 0 else 0.0

        return DeckDetailResponse(
            id=deck.id,
            userId=deck.userId,
            name=deck.name,
            format=deck.format,
            description=deck.description,
            commander=deck.commander,
            commanderScryfallId=deck.commanderScryfallId,
            commanderImageUri=deck.commanderImageUri,
            createdAt=deck.createdAt,
            updatedAt=deck.updatedAt,
            totalCards=total,
            uniqueCards=len(cards_with_ownership),
            ownedCards=owned,
            missingCards=missing_cards,
            completionPercentage=pct,
            cards=cards_with_ownership,
        )

    @staticmethod
    async def create_deck(user_id: str, data: DeckCreate) -> DeckSummaryResponse:
        created = await db.deck.create(
            data={
                "userId": user_id,
                "name": data.name,
                "format": data.format,
                "description": data.description,
                "commander": data.commander,
                "commanderScryfallId": data.commanderScryfallId,
                "commanderImageUri": data.commanderImageUri,
            }
        )

        # If commander provided, add commander card to deck
        if data.commander and data.commander.strip():
            cmd_name = data.commander.strip()
            cat = await ScryfallService.get_or_resolve_catalog_card(cmd_name)
            scry_id = data.commanderScryfallId or (cat.get("id") if cat else f"cmd:{cmd_name}")
            img_uri = data.commanderImageUri or (cat.get("imageUri") if cat else None)
            mana_cost = cat.get("manaCost") if cat else None
            type_line = cat.get("typeLine") if cat else "Legendary Creature"

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

        return DeckSummaryResponse(
            id=created.id,
            userId=created.userId,
            name=created.name,
            format=created.format,
            description=created.description,
            commander=created.commander,
            commanderScryfallId=created.commanderScryfallId,
            commanderImageUri=created.commanderImageUri,
            createdAt=created.createdAt,
            updatedAt=created.updatedAt,
            totalCards=1 if data.commander else 0,
            uniqueCards=1 if data.commander else 0,
            ownedCards=0,
            missingCards=1 if data.commander else 0,
            completionPercentage=0.0,
        )

    @staticmethod
    async def update_deck(deck_id: str, user_id: str, data: DeckUpdate) -> Optional[DeckSummaryResponse]:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return None

        update_data: Dict[str, Any] = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.format is not None:
            update_data["format"] = data.format
        if data.description is not None:
            update_data["description"] = data.description
        if data.commander is not None:
            update_data["commander"] = data.commander
        if data.commanderScryfallId is not None:
            update_data["commanderScryfallId"] = data.commanderScryfallId
        if data.commanderImageUri is not None:
            update_data["commanderImageUri"] = data.commanderImageUri

        updated = await db.deck.update(
            where={"id": deck_id},
            data=update_data,
            include={"cards": True}
        )

        return await DeckService.get_user_decks(user_id)

    @staticmethod
    async def delete_deck(deck_id: str, user_id: str) -> bool:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return False

        await db.deckcard.delete_many(where={"deckId": deck_id})
        await db.deck.delete(where={"id": deck_id})
        return True

    @staticmethod
    async def set_commander(deck_id: str, user_id: str, commander_name: str, scryfall_id: Optional[str] = None, image_uri: Optional[str] = None) -> bool:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return False

        # If scryfall metadata missing, resolve
        if not scryfall_id or not image_uri:
            cat = await ScryfallService.get_or_resolve_catalog_card(commander_name)
            if cat:
                scryfall_id = scryfall_id or cat.get("id")
                image_uri = image_uri or cat.get("imageUri")

        # Reset isCommander on all cards in this deck
        await db.deckcard.update_many(
            where={"deckId": deck_id},
            data={"isCommander": False}
        )

        # Update deck commander
        await db.deck.update(
            where={"id": deck_id},
            data={
                "commander": commander_name,
                "commanderScryfallId": scryfall_id,
                "commanderImageUri": image_uri,
            }
        )

        # Check if card exists in deck
        norm = normalize_card_name(commander_name)
        existing = await db.deckcard.find_first(
            where={"deckId": deck_id, "cardName": {"equals": commander_name, "mode": "insensitive"}}
        )

        if existing:
            await db.deckcard.update(
                where={"id": existing.id},
                data={"isCommander": True}
            )
        else:
            await db.deckcard.create(
                data={
                    "deckId": deck_id,
                    "cardScryfallId": scryfall_id or f"cmd:{commander_name}",
                    "cardName": commander_name,
                    "quantity": 1,
                    "assignedQuantity": 0,
                    "isSideboard": False,
                    "isCommander": True,
                    "imageUri": image_uri,
                }
            )

        return True

    @staticmethod
    async def add_card_to_deck(deck_id: str, user_id: str, data: DeckCardCreate) -> bool:
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck or deck.userId != user_id:
            return False

        # Resolve card catalog
        cat = await ScryfallService.get_or_resolve_catalog_card(data.cardName)
        mana_cost = data.manaCost or (cat.get("manaCost") if cat else None)
        type_line = data.typeLine or (cat.get("typeLine") if cat else None)
        image_uri = data.imageUri or (cat.get("imageUri") if cat else None)
        scry_id = data.cardScryfallId or (cat.get("id") if cat else f"scry:{data.cardName}")

        existing = await db.deckcard.find_first(
            where={"deckId": deck_id, "cardScryfallId": scry_id, "isSideboard": data.isSideboard}
        )

        if existing:
            await db.deckcard.update(
                where={"id": existing.id},
                data={"quantity": existing.quantity + data.quantity}
            )
        else:
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
                }
            )

        return True

    @staticmethod
    async def update_card_quantity(card_id: str, user_id: str, quantity: int) -> bool:
        card = await db.deckcard.find_unique(where={"id": card_id}, include={"deck": True})
        if not card or not card.deck or card.deck.userId != user_id:
            return False

        if quantity <= 0:
            await db.deckcard.delete(where={"id": card_id})
        else:
            new_assigned = min(card.assignedQuantity, quantity)
            await db.deckcard.update(
                where={"id": card_id},
                data={"quantity": quantity, "assignedQuantity": new_assigned}
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
                    where={"userId_cardScryfallId": {"userId": user_id, "cardScryfallId": card.cardScryfallId}}
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

        return {"status": "success", "addedCount": added}

    @staticmethod
    async def import_deck_text(user_id: str, name: str, raw_text: str, format: str = "Commander", commander: Optional[str] = None) -> DeckSummaryResponse:
        parsed_cards = parse_decklist_text(raw_text)

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
        for item in parsed_cards:
            cat = await ScryfallService.get_or_resolve_catalog_card(item.name)
            scry_id = cat.get("id") if cat else f"pending:{item.name}"
            is_cmd = item.isCommander or (cmd_candidate and normalize_card_name(item.name) == normalize_card_name(cmd_candidate))

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

        return DeckSummaryResponse(
            id=deck.id,
            userId=deck.userId,
            name=deck.name,
            format=deck.format,
            commander=deck.commander,
            createdAt=deck.createdAt,
            updatedAt=deck.updatedAt,
            totalCards=sum(c.quantity for c in parsed_cards),
            uniqueCards=len(parsed_cards),
            ownedCards=0,
            missingCards=sum(c.quantity for c in parsed_cards),
            completionPercentage=0.0,
        )
