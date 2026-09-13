import logging
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.schemas.collection import CollectionCardCreate, CollectionCardResponse, CollectionStats
from src.services.scryfall_service import ScryfallService
from src.services.import_service import parse_decklist_text
from src.services.enrichment_service import trigger_async_priority_enrichment
from src.services.image_resolver import resolve_minio_image_uris, safe_image_uri

logger = logging.getLogger("mtg_backend.collection")

class CollectionService:
    @staticmethod
    async def get_user_collection(
        user_id: str,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[CollectionCardResponse]:
        where_clause: Dict[str, Any] = {"userId": user_id}
        if query and query.strip():
            where_clause["cardName"] = {"contains": query.strip(), "mode": "insensitive"}

        find_args: Dict[str, Any] = {
            "where": where_clause,
            "order": [{"cardName": "asc"}, {"id": "asc"}],
        }
        if limit is not None:
            find_args["take"] = limit
        if offset > 0:
            find_args["skip"] = offset

        cards = await db.collectioncard.find_many(**find_args)

        # Resolve local (MinIO) image URLs from card_printings; Scryfall URLs
        # are fetched server-side by next/image and fail, so we prefer the
        # locally-mirrored image when available.
        local_images = await resolve_minio_image_uris(
            [c.cardScryfallId for c in cards]
        )

        return [
            CollectionCardResponse(
                id=c.id,
                userId=c.userId,
                cardScryfallId=c.cardScryfallId,
                cardName=c.cardName,
                quantity=c.quantity,
                isFoil=getattr(c, "isFoil", False),
                setCode=c.setCode,
                collectorNumber=c.collectorNumber,
                manaCost=c.manaCost,
                typeLine=c.typeLine,
                imageUri=local_images.get(c.cardScryfallId) or safe_image_uri(c.imageUri),
                updatedAt=c.updatedAt,
            )
            for c in cards
        ]

    @staticmethod
    async def add_or_increment_card(
        user_id: str,
        data: CollectionCardCreate,
        *,
        prioritize: bool = True,
    ) -> CollectionCardResponse:
        # Check if already in collection
        existing = await db.collectioncard.find_unique(
            where={"userId_cardScryfallId": {"userId": user_id, "cardScryfallId": data.cardScryfallId, "isFoil": data.isFoil}}
        )

        if existing:
            updated = await db.collectioncard.update(
                where={"id": existing.id},
                data={"quantity": {"increment": data.quantity}}
            )
            c = updated
        else:
            # Keep creation responsive: only read the local catalog here. A
            # missing card is resolved remotely by the priority worker.
            mana_cost = data.manaCost
            type_line = data.typeLine
            image_uri = safe_image_uri(data.imageUri)
            set_code = data.setCode
            collector_num = data.collectorNumber

            if not type_line or not image_uri:
                cat = await ScryfallService.get_catalog_card(data.cardName)
                if cat:
                    mana_cost = mana_cost or cat.get("manaCost")
                    type_line = type_line or cat.get("typeLine")
                    image_uri = image_uri or cat.get("imageUri")
                    set_code = set_code or cat.get("setCode")
                    collector_num = collector_num or cat.get("collectorNumber")

            created = await db.collectioncard.create(
                data={
                    "userId": user_id,
                    "isFoil": data.isFoil,
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

        # Every user-driven add is sent to the priority worker, including
        # existing rows whose metadata may still be incomplete. Imports batch
        # their names and schedule one worker run after all rows are stored.
        if prioritize:
            trigger_async_priority_enrichment([data.cardName])

        return CollectionCardResponse(
            id=c.id,
            userId=c.userId,
            cardScryfallId=c.cardScryfallId,
            cardName=c.cardName,
            quantity=c.quantity,
                isFoil=getattr(c, "isFoil", False),
            setCode=c.setCode,
            collectorNumber=c.collectorNumber,
            manaCost=c.manaCost,
            typeLine=c.typeLine,
            imageUri=safe_image_uri(c.imageUri),
            updatedAt=c.updatedAt,
        )

    @staticmethod
    async def update_quantity(
        user_id: str,
        card_id: str,
        quantity: int,
        set_code: Optional[str] = None,
        *,
        set_code_provided: bool = False,
    ) -> Optional[CollectionCardResponse]:
        card = await db.collectioncard.find_unique(where={"id": card_id})
        if not card or card.userId != user_id:
            return None

        if quantity <= 0:
            await db.collectioncard.delete(where={"id": card_id})
            return None

        update_data: Dict[str, Any] = {"quantity": quantity}
        if set_code_provided:
            update_data["setCode"] = set_code or None
        updated = await db.collectioncard.update(
            where={"id": card_id},
            data=update_data
        )
        return CollectionCardResponse(
            id=updated.id,
            userId=updated.userId,
            cardScryfallId=updated.cardScryfallId,
            cardName=updated.cardName,
            quantity=updated.quantity,
            isFoil=getattr(updated, "isFoil", False),
            setCode=updated.setCode,
            collectorNumber=updated.collectorNumber,
            manaCost=updated.manaCost,
            typeLine=updated.typeLine,
            imageUri=safe_image_uri(updated.imageUri),
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
    async def import_collection_text(user_id: str, raw_text: str, request_key: Optional[str] = None) -> Dict[str, Any]:
        from src.services.bulk_import import import_text
        return await import_text(db, user_id, raw_text, request_key)
