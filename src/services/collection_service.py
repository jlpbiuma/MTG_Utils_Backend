import logging
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.schemas.collection import CollectionCardCreate, CollectionCardResponse, CollectionStats
from src.services.scryfall_service import ScryfallService
from src.services.import_service import parse_decklist_text

logger = logging.getLogger("mtg_backend.collection")

class CollectionService:
    @staticmethod
    async def get_user_collection(
        user_id: str,
        query: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[CollectionCardResponse]:
        where_clause: Dict[str, Any] = {"userId": user_id}
        if query and query.strip():
            where_clause["cardName"] = {"contains": query.strip(), "mode": "insensitive"}

        cards = await db.collectioncard.find_many(
            where=where_clause,
            take=limit,
            skip=offset,
            order={"updatedAt": "desc"}
        )

        return [
            CollectionCardResponse(
                id=c.id,
                userId=c.userId,
                cardScryfallId=c.cardScryfallId,
                cardName=c.cardName,
                quantity=c.quantity,
                setCode=c.setCode,
                collectorNumber=c.collectorNumber,
                manaCost=c.manaCost,
                typeLine=c.typeLine,
                imageUri=c.imageUri,
                updatedAt=c.updatedAt,
            )
            for c in cards
        ]

    @staticmethod
    async def add_or_increment_card(user_id: str, data: CollectionCardCreate) -> CollectionCardResponse:
        # Check if already in collection
        existing = await db.collectioncard.find_unique(
            where={"userId_cardScryfallId": {"userId": user_id, "cardScryfallId": data.cardScryfallId}}
        )

        if existing:
            updated = await db.collectioncard.update(
                where={"id": existing.id},
                data={"quantity": existing.quantity + data.quantity}
            )
            c = updated
        else:
            # If metadata missing, resolve from catalog or Scryfall
            mana_cost = data.manaCost
            type_line = data.typeLine
            image_uri = data.imageUri
            set_code = data.setCode
            collector_num = data.collectorNumber

            if not type_line or not image_uri:
                cat = await ScryfallService.get_or_resolve_catalog_card(data.cardName)
                if cat:
                    mana_cost = mana_cost or cat.get("manaCost")
                    type_line = type_line or cat.get("typeLine")
                    image_uri = image_uri or cat.get("imageUri")
                    set_code = set_code or cat.get("setCode")
                    collector_num = collector_num or cat.get("collectorNumber")

            created = await db.collectioncard.create(
                data={
                    "userId": user_id,
                    "cardScryfallId": data.cardScryfallId,
                    "cardName": data.cardName,
                    "quantity": data.quantity,
                    "manaCost": mana_cost,
                    "typeLine": type_line,
                    "imageUri": image_uri,
                    "setCode": set_code,
                    "collectorNumber": collector_num,
                }
            )
            c = created

        return CollectionCardResponse(
            id=c.id,
            userId=c.userId,
            cardScryfallId=c.cardScryfallId,
            cardName=c.cardName,
            quantity=c.quantity,
            setCode=c.setCode,
            collectorNumber=c.collectorNumber,
            manaCost=c.manaCost,
            typeLine=c.typeLine,
            imageUri=c.imageUri,
            updatedAt=c.updatedAt,
        )

    @staticmethod
    async def update_quantity(user_id: str, card_id: str, quantity: int) -> Optional[CollectionCardResponse]:
        card = await db.collectioncard.find_unique(where={"id": card_id})
        if not card or card.userId != user_id:
            return None

        if quantity <= 0:
            await db.collectioncard.delete(where={"id": card_id})
            return None

        updated = await db.collectioncard.update(
            where={"id": card_id},
            data={"quantity": quantity}
        )
        return CollectionCardResponse(
            id=updated.id,
            userId=updated.userId,
            cardScryfallId=updated.cardScryfallId,
            cardName=updated.cardName,
            quantity=updated.quantity,
            setCode=updated.setCode,
            collectorNumber=updated.collectorNumber,
            manaCost=updated.manaCost,
            typeLine=updated.typeLine,
            imageUri=updated.imageUri,
            updatedAt=updated.updatedAt,
        )

    @staticmethod
    async def delete_card(user_id: str, card_id: str) -> bool:
        card = await db.collectioncard.find_unique(where={"id": card_id})
        if not card or card.userId != user_id:
            return False
        await db.collectioncard.delete(where={"id": card_id})
        return True

    @staticmethod
    async def get_stats(user_id: str) -> CollectionStats:
        cards = await db.collectioncard.find_many(where={"userId": user_id})
        total = sum(c.quantity for c in cards)
        unique = len(cards)
        return CollectionStats(
            totalCards=total,
            uniqueCards=unique,
            completionPercentage=100.0,
        )

    @staticmethod
    async def import_collection_text(user_id: str, raw_text: str) -> Dict[str, Any]:
        parsed = parse_decklist_text(raw_text)
        imported_count = 0

        for item in parsed:
            cat = await ScryfallService.get_or_resolve_catalog_card(item.name)
            scryfall_id = cat.get("id") if cat else f"pending:{item.name}"
            card_create = CollectionCardCreate(
                cardScryfallId=scryfall_id,
                cardName=item.name,
                quantity=item.quantity,
                setCode=item.setCode or (cat.get("setCode") if cat else None),
                collectorNumber=item.collectorNumber or (cat.get("collectorNumber") if cat else None),
                manaCost=cat.get("manaCost") if cat else None,
                typeLine=cat.get("typeLine") if cat else None,
                imageUri=cat.get("imageUri") if cat else None,
            )
            await CollectionService.add_or_increment_card(user_id, card_create)
            imported_count += item.quantity

        return {"status": "success", "importedCount": imported_count, "uniqueCards": len(parsed)}
