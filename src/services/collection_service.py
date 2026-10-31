import logging
import functools
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from src.core.db import db
from src.schemas.collection import (
    CollectionCardCreate, CollectionCardResponse, CollectionStats,
    CollectionQueryResponse, CollectionGroupSection,
    DormantCardItem, DormantCardsResponse,
)
from src.schemas.deck import DeckRequirement
from src.services.card_utils import (
    normalize_card_name,
    get_card_category,
    CARD_TYPE_GROUPS,
    extract_cmc,
    completion_percentages_by_deck,
    colors_by_deck,
)
from src.services.scryfall_service import ScryfallService
from src.services.pricing_service import PricingService
from src.services.import_service import parse_decklist_text
from src.services.enrichment_service import trigger_async_priority_enrichment
from src.services.image_resolver import resolve_minio_image_uris, safe_image_uri

logger = logging.getLogger("mtg_backend.collection")

# Sort fields supported by the collection listing (deck-only fields excluded).
COLLECTION_SORT_FIELDS = (
    "name",
    "cmc",
    "type",
    "quantity",
    "price_trend",
    "price_subtotal",
    "requested_decks",
)

class _CollectionRow:
    """Sort/filter view of an inventory row; builds its response on demand."""
    __slots__ = ("card", "requestedInDecks", "cardName", "cardScryfallId", "quantity", "manaCost", "typeLine", "requestedInDecksCount")

    def __init__(self, card: Any, requested: List[DeckRequirement]):
        self.card = card
        self.requestedInDecks = requested
        self.requestedInDecksCount = len(requested)
        self.cardName = card.cardName
        self.cardScryfallId = card.cardScryfallId
        self.quantity = card.quantity
        self.manaCost = card.manaCost
        self.typeLine = card.typeLine

    def response(self, local_images: Dict[str, str]) -> CollectionCardResponse:
        c = self.card
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
            imageUri=local_images.get(c.cardScryfallId) or safe_image_uri(c.imageUri),
            updatedAt=c.updatedAt,
            acquiredAt=getattr(c, "acquiredAt", None),
            requestedInDecks=self.requestedInDecks,
            requestedInDecksCount=self.requestedInDecksCount,
        )


class CollectionService:
    @staticmethod
    async def _deck_demand_by_name(
        user_id: str,
        col_cards: Optional[List[Any]] = None,
    ) -> Dict[str, Dict[str, DeckRequirement]]:
        user_deck_cards = await db.deckcard.find_many(
            where={"deck": {"userId": user_id}},
            include={"deck": True},
        )
        if col_cards is None:
            col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map: Dict[str, int] = {}
        for cc in col_cards or []:
            norm_col = normalize_card_name(cc.cardName)
            col_map[norm_col] = col_map.get(norm_col, 0) + (getattr(cc, "quantity", 0) or 0)
        completion = completion_percentages_by_deck(user_deck_cards, col_map)
        colors = colors_by_deck(user_deck_cards)
        decks_by_norm: Dict[str, Dict[str, DeckRequirement]] = {}
        for oc in user_deck_cards or []:
            if getattr(oc, "isSideboard", False) is True or getattr(getattr(oc, "deck", None), "isArchived", False) is True:
                continue
            norm = normalize_card_name(oc.cardName)
            deck_obj = getattr(oc, "deck", None)
            d_id = getattr(oc, "deckId", None)
            if not d_id:
                continue
            d_name = (
                deck_obj.name
                if (deck_obj and isinstance(getattr(deck_obj, "name", None), str))
                else "Otro Mazo"
            )
            qty = getattr(oc, "quantity", None) or 0
            if norm not in decks_by_norm:
                decks_by_norm[norm] = {}
            if d_id not in decks_by_norm[norm]:
                decks_by_norm[norm][d_id] = DeckRequirement(
                    deckId=d_id,
                    deckName=d_name,
                    quantity=qty,
                    completionPercentage=completion.get(d_id, 0.0),
                    colors=colors.get(d_id, []),
                    isSideboard=bool(getattr(oc, "isSideboard", False)),
                )
            else:
                decks_by_norm[norm][d_id].quantity += qty
                decks_by_norm[norm][d_id].isSideboard |= bool(getattr(oc, "isSideboard", False))
        return decks_by_norm

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
                acquiredAt=getattr(c, "acquiredAt", None),
            )
            for c in cards
        ]

    @staticmethod
    async def get_user_collection_query(
        user_id: str,
        query: Optional[str] = None,
        sort: str = "name",
        direction: str = "asc",
        grouped: bool = True,
        price_provider: str = "cardmarket",
        page: int = 1,
        limit: Optional[int] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> CollectionQueryResponse:
        """
        Filters the WHOLE collection by name and price, then sorts and
        optionally groups it into MTG type sections on the backend, so the
        frontend never operates over a partial card set.

        With ``limit`` every mode is paginated the same way: grouped pages are
        consecutive slices of the section-ordered list, and section totals
        always describe the whole filtered section. Only the requested page is
        materialized (images and response models).
        """
        where_clause: Dict[str, Any] = {"userId": user_id}
        if query and query.strip():
            where_clause["cardName"] = {"contains": query.strip(), "mode": "insensitive"}

        cards = await db.collectioncard.find_many(
            where=where_clause,
            order=[{"cardName": "asc"}, {"id": "asc"}],
        )
        decks_by_norm = await CollectionService._deck_demand_by_name(
            user_id, col_cards=cards if not (query and query.strip()) else None
        )

        has_price_filter = min_price is not None or max_price is not None
        price_summary: Optional[Any] = None
        if sort in ("price_trend", "price_subtotal") or grouped or has_price_filter:
            price_summary = await PricingService.get_price_summary(
                cards=[
                    {
                        "name": c.cardName,
                        "scryfallId": c.cardScryfallId,
                        "quantity": c.quantity,
                        "ownedQuantity": c.quantity,
                        "missingQuantity": 0,
                        "isMissing": False,
                    }
                    for c in cards
                ],
                provider=price_provider,
                bypass_cache=False,
            )

        # Lightweight rows: the response model is only built for the page.
        rows = [
            _CollectionRow(c, sorted(decks_by_norm.get(normalize_card_name(c.cardName), {}).values(), key=lambda r: r.deckName.lower()))
            for c in cards
        ]
        if has_price_filter:
            rows = [row for row in rows if CollectionService._matches_price(row, price_summary, min_price, max_price)]
        if sort in COLLECTION_SORT_FIELDS:
            rows = CollectionService._sort_collection_cards(rows, sort, direction, price_summary)

        unique_cards = len(rows)
        total_cards = sum(row.quantity for row in rows)
        currency_symbol = price_summary.currencySymbol if price_summary is not None else "€"
        page_limit = limit if limit and limit > 0 else None
        safe_page = max(1, page) if page_limit is not None else 1

        sections_meta: List[tuple] = []
        if grouped:
            # Section order first, then the requested sort inside each section.
            buckets: Dict[str, List[_CollectionRow]] = {}
            for row in rows:
                buckets.setdefault(get_card_category(row.typeLine, row.cardName), []).append(row)
            rows = []
            for key, info in sorted(CARD_TYPE_GROUPS.items(), key=lambda item: item[1]["order"]):
                if buckets.get(key):
                    sections_meta.append((key, info, buckets[key]))
                    rows.extend(buckets[key])

        start = (safe_page - 1) * page_limit if page_limit is not None else 0
        end = start + page_limit if page_limit is not None else unique_cards
        page_rows = rows[start:end]
        local_images = await resolve_minio_image_uris([row.cardScryfallId for row in page_rows])
        responses = {id(row): row.response(local_images) for row in page_rows}

        result = CollectionQueryResponse(
            query=(query or "").strip(),
            grouped=grouped,
            provider=price_provider,
            currencySymbol=currency_symbol,
            totalCards=total_cards,
            uniqueCards=unique_cards,
            page=safe_page,
            limit=page_limit,
            hasMore=end < unique_cards,
        )
        if not grouped:
            result.cards = [responses[id(row)] for row in page_rows]
            return result
        result.sections = [
            CollectionService._section(key, info, section_rows, price_summary, currency_symbol,
                                       [responses[id(row)] for row in section_rows if id(row) in responses])
            for key, info, section_rows in sections_meta
        ]
        if page_limit is not None:
            result.sections = [section for section in result.sections if section.cards]
        return result

    @staticmethod
    def _matches_price(item: Any, price_summary: Optional[Any], min_price: Optional[float], max_price: Optional[float]) -> bool:
        """Mirrors the web ``matchesPriceFilter``: unpriced cards never match."""
        quote = CollectionService._quote_for(item, price_summary)
        price = None
        if quote is not None:
            price = quote.unitPrice.trend if quote.unitPrice.trend is not None else quote.subtotal
        if price is None:
            return False
        if min_price is not None and price < min_price:
            return False
        if max_price is not None and price > max_price:
            return False
        return True

    @staticmethod
    def _section(key, info, rows, price_summary, currency_symbol, page_cards) -> CollectionGroupSection:
        total_cards = sum(row.quantity for row in rows)
        section_total_price = 0.0
        for row in rows:
            quote = CollectionService._quote_for(row, price_summary)
            section_total_price += (quote.unitPrice.trend if quote else 0.0) * row.quantity
        return CollectionGroupSection(
            key=key,
            label=info["label"],
            order=info["order"],
            totalCards=total_cards,
            uniqueCards=len(rows),
            ownedCards=total_cards,
            missingCards=0,
            completionPercentage=100.0,
            sectionTotalPrice=round(section_total_price, 2),
            sectionMissingPrice=0.0,
            sectionOwnedPrice=round(section_total_price, 2),
            currencySymbol=currency_symbol,
            cards=page_cards,
        )

    @staticmethod
    def _quote_for(
        item: CollectionCardResponse,
        price_summary: Optional[Any],
    ) -> Optional[Any]:
        """Returns the price quote for a card by scryfall id or normalized name."""
        if price_summary is None:
            return None
        if item.cardScryfallId and item.cardScryfallId in price_summary.quotes:
            return price_summary.quotes[item.cardScryfallId]
        return price_summary.quotes.get(normalize_card_name(item.cardName))

    @staticmethod
    def _sort_collection_cards(
        items: List[Any],
        sort: str,
        direction: str,
        price_summary: Optional[Any],
    ) -> List[Any]:
        """Sorts the full collection server-side, mirroring the frontend sortCards."""
        multiplier = -1 if direction == "desc" else 1

        def primary(item: CollectionCardResponse):
            if sort == "cmc":
                return extract_cmc(item.manaCost, item.typeLine)
            if sort == "type":
                return (item.typeLine or "").lower()
            if sort == "quantity":
                return item.quantity
            if sort == "requested_decks":
                return item.requestedInDecksCount
            if sort == "price_trend":
                quote = CollectionService._quote_for(item, price_summary)
                return quote.unitPrice.trend if quote else 0.0
            if sort == "price_subtotal":
                quote = CollectionService._quote_for(item, price_summary)
                return quote.subtotal if quote else ((quote.unitPrice.trend if quote else 0.0) * item.quantity)
            return item.cardName.lower()

        def tiebreak(item: CollectionCardResponse):
            return item.cardName.lower()

        def compare(a: CollectionCardResponse, b: CollectionCardResponse):
            pa, pb = primary(a), primary(b)
            if pa != pb:
                return multiplier * (1 if pa > pb else -1)
            return 1 if tiebreak(a) > tiebreak(b) else (-1 if tiebreak(a) < tiebreak(b) else 0)

        return sorted(items, key=functools.cmp_to_key(compare))

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
            update_data = {"quantity": {"increment": data.quantity}}
            if data.acquiredAt is not None:
                update_data["acquiredAt"] = data.acquiredAt
            elif getattr(existing, "acquiredAt", None) is None:
                update_data["acquiredAt"] = datetime.now(timezone.utc)
            updated = await db.collectioncard.update(
                where={"id": existing.id},
                data=update_data
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
                    "acquiredAt": data.acquiredAt or datetime.now(timezone.utc),
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
            acquiredAt=getattr(c, "acquiredAt", None),
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
        if quantity > card.quantity and getattr(card, "acquiredAt", None) is None:
            update_data["acquiredAt"] = datetime.now(timezone.utc)
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
            acquiredAt=getattr(updated, "acquiredAt", None),
        )

    @staticmethod
    async def update_acquired_at(user_id: str, card_id: str, acquired_at: datetime) -> Optional[CollectionCardResponse]:
        card = await db.collectioncard.find_unique(where={"id": card_id})
        if not card or card.userId != user_id:
            return None
        updated = await db.collectioncard.update(where={"id": card_id}, data={"acquiredAt": acquired_at})
        return CollectionCardResponse(
            id=updated.id, userId=updated.userId, cardScryfallId=updated.cardScryfallId,
            cardName=updated.cardName, quantity=updated.quantity, isFoil=getattr(updated, "isFoil", False),
            setCode=updated.setCode, collectorNumber=updated.collectorNumber, manaCost=updated.manaCost,
            typeLine=updated.typeLine, imageUri=safe_image_uri(updated.imageUri), updatedAt=updated.updatedAt,
            acquiredAt=getattr(updated, "acquiredAt", None),
        )

    @staticmethod
    async def update_card_version(
        user_id: str,
        card_id: str,
        card_scryfall_id: str,
        image_uri: Optional[str] = None,
        set_code: Optional[str] = None,
        collector_number: Optional[str] = None,
    ) -> Optional[CollectionCardResponse]:
        card = await db.collectioncard.find_unique(where={"id": card_id})
        if not card or card.userId != user_id:
            return None

        effective_image = safe_image_uri(image_uri) or image_uri
        is_foil = getattr(card, "isFoil", False)

        if card.cardScryfallId == card_scryfall_id:
            update_data: Dict[str, Any] = {}
            if effective_image:
                update_data["imageUri"] = effective_image
            if set_code is not None:
                update_data["setCode"] = set_code
            if collector_number is not None:
                update_data["collectorNumber"] = collector_number
            if update_data:
                updated = await db.collectioncard.update(where={"id": card_id}, data=update_data)
            else:
                updated = card
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
                acquiredAt=getattr(updated, "acquiredAt", None),
            )

        conflict = await db.collectioncard.find_first(
            where={
                "userId": user_id,
                "cardScryfallId": card_scryfall_id,
                "isFoil": is_foil,
                "id": {"not": card_id},
            }
        )
        if conflict:
            async with db.tx() as tx:
                await tx.query_raw("SELECT set_config('mtg.suppress_acquisition', 'on', true)")
                updated = await tx.collectioncard.update(
                    where={"id": conflict.id},
                    data={
                        "quantity": conflict.quantity + card.quantity,
                        "imageUri": effective_image or conflict.imageUri,
                        "setCode": set_code if set_code is not None else conflict.setCode,
                        "collectorNumber": (
                            collector_number
                            if collector_number is not None
                            else conflict.collectorNumber
                        ),
                        "acquiredAt": (
                            getattr(conflict, "acquiredAt", None)
                            or getattr(card, "acquiredAt", None)
                            or datetime.now(timezone.utc)
                        ),
                    },
                )
                await tx.collectioncard.delete(where={"id": card_id})
        else:
            update_data = {"cardScryfallId": card_scryfall_id}
            if effective_image:
                update_data["imageUri"] = effective_image
            if set_code is not None:
                update_data["setCode"] = set_code
            if collector_number is not None:
                update_data["collectorNumber"] = collector_number
            updated = await db.collectioncard.update(where={"id": card_id}, data=update_data)

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
            acquiredAt=getattr(updated, "acquiredAt", None),
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
        decks_count = 0
        deck_model = getattr(db, "deck", None)
        if deck_model is not None and hasattr(deck_model, "count"):
            count_res = deck_model.count(where={"userId": user_id})
            import inspect
            if inspect.isawaitable(count_res):
                decks_count = await count_res
            elif isinstance(count_res, int):
                decks_count = count_res
        return CollectionStats(
            totalCards=total,
            uniqueCards=unique,
            completionPercentage=100.0,
            decksCount=decks_count,
        )

    @staticmethod
    async def import_collection_text(user_id: str, raw_text: str, request_key: Optional[str] = None) -> Dict[str, Any]:
        from src.services.bulk_import import import_text
        return await import_text(db, user_id, raw_text, request_key)

    @staticmethod
    async def _edhrec_recommended_norms(commanders: List[str]) -> set:
        """Normalized names EDHREC recommends for any of these commanders."""
        norms = sorted({normalize_card_name(name) for name in commanders if name})
        if not norms:
            return set()
        rows = await db.edhreccommander.find_many(
            where={"normalizedName": {"in": norms}, "status": "synced"},
        )
        recommended: set = set()
        for row in rows:
            lists = row.cardsJson if isinstance(row.cardsJson, dict) else {}
            for cards in lists.values():
                for card in cards if isinstance(cards, list) else []:
                    name = card.get("normalizedName") or card.get("name") if isinstance(card, dict) else None
                    if name:
                        recommended.add(normalize_card_name(name))
        return recommended

    @staticmethod
    async def get_dormant_cards(
        user_id: str,
        min_price: float = 0.0,
        provider: str = "cardmarket",
    ) -> DormantCardsResponse:
        from src.services.card_utils import is_basic_land
        from src.services.pricing_service import PRICE_PROVIDERS

        prov_info = PRICE_PROVIDERS.get(provider, PRICE_PROVIDERS["cardmarket"])
        symbol = prov_info["symbol"]

        user_decks = await db.deck.find_many(where={"userId": user_id, "isArchived": False}, include={"cards": True})
        deck_card_norms: set = set()
        active_commanders: List[str] = []

        for d in user_decks:
            if d.commander and d.commander not in active_commanders:
                active_commanders.append(d.commander)
            for c in (d.cards or []):
                deck_card_norms.add(normalize_card_name(c.cardName))

        # EDHREC lists come from the worker-synced table: one local query
        # instead of one sequential download per commander on every request.
        # (The previous lookup read a "cardlists" key that the parsed EDHREC
        # payload never has, so recommendations were never excluded.)
        edhrec_norms = await CollectionService._edhrec_recommended_norms(active_commanders)

        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        candidate_cards = []
        for c in col_cards:
            if is_basic_land(c.typeLine, c.cardName):
                continue
            norm = normalize_card_name(c.cardName)
            if norm not in deck_card_norms and norm not in edhrec_norms:
                candidate_cards.append(c)

        if not candidate_cards:
            return DormantCardsResponse(
                totalCards=0,
                uniqueCards=0,
                totalValue=0.0,
                currencySymbol=symbol,
                provider=provider,
                activeCommanders=active_commanders,
                cards=[],
            )

        # Price candidate dormant cards
        cards_to_price = [
            {"name": c.cardName, "scryfallId": c.cardScryfallId, "quantity": c.quantity}
            for c in candidate_cards
        ]
        price_quotes = {}
        try:
            summary = await PricingService.get_price_summary(cards=cards_to_price, provider=provider)
            price_quotes = summary.quotes
        except Exception as e:
            logger.warning(f"Failed to price dormant cards: {e}")

        dormant_items: List[DormantCardItem] = []
        for c in candidate_cards:
            quote = price_quotes.get(c.cardScryfallId) or price_quotes.get(normalize_card_name(c.cardName))
            unit_price = quote.unitPrice.trend if quote else 0.0
            if unit_price < min_price:
                continue
            tot_val = round(c.quantity * unit_price, 2)
            dormant_items.append(
                DormantCardItem(
                    id=c.id,
                    cardScryfallId=c.cardScryfallId,
                    cardName=c.cardName,
                    isFoil=c.isFoil,
                    quantity=c.quantity,
                    setCode=c.setCode,
                    collectorNumber=c.collectorNumber,
                    manaCost=c.manaCost,
                    typeLine=c.typeLine,
                    imageUri=safe_image_uri(c.imageUri),
                    price=round(unit_price, 2),
                    totalValue=tot_val,
                )
            )

        dormant_items.sort(key=lambda x: (x.totalValue, x.price, x.cardName), reverse=True)
        total_qty = sum(item.quantity for item in dormant_items)
        total_val = round(sum(item.totalValue for item in dormant_items), 2)

        return DormantCardsResponse(
            totalCards=total_qty,
            uniqueCards=len(dormant_items),
            totalValue=total_val,
            currencySymbol=symbol,
            provider=provider,
            activeCommanders=active_commanders,
            cards=dormant_items,
        )
