import logging
import functools
import asyncio
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
                )
            else:
                decks_by_norm[norm][d_id].quantity += qty
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
    ) -> CollectionQueryResponse:
        """
        Filters the WHOLE collection by name, then sorts and optionally groups
        it into MTG type sections entirely on the backend, so the frontend never
        performs these operations over a partial (paginated) card set.
        Flat (non-grouped) responses can be page-sliced after sort.
        """
        where_clause: Dict[str, Any] = {"userId": user_id}
        if query and query.strip():
            where_clause["cardName"] = {"contains": query.strip(), "mode": "insensitive"}

        cards = await db.collectioncard.find_many(
            where=where_clause,
            order=[{"cardName": "asc"}, {"id": "asc"}],
        )

        local_images, decks_by_norm = await asyncio.gather(
            resolve_minio_image_uris([c.cardScryfallId for c in cards]),
            CollectionService._deck_demand_by_name(
                user_id, col_cards=cards if not (query and query.strip()) else None
            ),
        )

        items: List[CollectionCardResponse] = []
        for c in cards:
            req_list = sorted(
                list(decks_by_norm.get(normalize_card_name(c.cardName), {}).values()),
                key=lambda r: r.deckName.lower(),
            )
            items.append(
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
                    requestedInDecks=req_list,
                    requestedInDecksCount=len(req_list),
                )
            )

        normalized_query = (query or "").strip()
        price_summary: Optional[Any] = None
        if sort in ("price_trend", "price_subtotal") or grouped:
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

        if sort in COLLECTION_SORT_FIELDS:
            items = CollectionService._sort_collection_cards(
                items, sort, direction, price_summary
            )

        unique_cards = len(items)
        total_cards = sum(c.quantity for c in items)
        currency_symbol = (
            price_summary.currencySymbol if price_summary is not None else "€"
        )
        safe_page = max(1, page)
        page_limit = limit if limit and limit > 0 else None

        if not grouped:
            has_more = False
            page_items = items
            if page_limit is not None:
                start = (safe_page - 1) * page_limit
                end = start + page_limit
                page_items = items[start:end]
                has_more = end < unique_cards
            return CollectionQueryResponse(
                query=normalized_query,
                grouped=False,
                provider=price_provider,
                currencySymbol=currency_symbol,
                totalCards=total_cards,
                uniqueCards=unique_cards,
                page=safe_page,
                limit=page_limit,
                hasMore=has_more,
                cards=page_items,
            )

        sections = CollectionService._group_collection_sections(
            items, price_summary
        )
        return CollectionQueryResponse(
            query=normalized_query,
            grouped=True,
            provider=price_provider,
            currencySymbol=currency_symbol,
            totalCards=total_cards,
            uniqueCards=unique_cards,
            page=1,
            limit=None,
            hasMore=False,
            sections=sections,
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
        items: List[CollectionCardResponse],
        sort: str,
        direction: str,
        price_summary: Optional[Any],
    ) -> List[CollectionCardResponse]:
        """Sorts the full collection server-side, mirroring the frontend sortCards."""
        multiplier = -1 if direction == "desc" else 1

        def primary(item: CollectionCardResponse):
            if sort == "cmc":
                return extract_cmc(item.manaCost)
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
    def _group_collection_sections(
        items: List[CollectionCardResponse],
        price_summary: Optional[Any],
    ) -> List[CollectionGroupSection]:
        """Groups the filtered/sorted collection into MTG type sections with stats."""
        buckets: Dict[str, List[CollectionCardResponse]] = {}
        for item in items:
            cat = get_card_category(item.typeLine, item.cardName)
            buckets.setdefault(cat, []).append(item)

        currency_symbol = (
            price_summary.currencySymbol if price_summary is not None else "€"
        )
        sections: List[CollectionGroupSection] = []
        for key, group_info in CARD_TYPE_GROUPS.items():
            cat_cards = buckets.get(key)
            if not cat_cards:
                continue

            total_cards = sum(c.quantity for c in cat_cards)
            unique = len(cat_cards)
            section_total_price = 0.0
            for c in cat_cards:
                quote = CollectionService._quote_for(c, price_summary)
                trend = quote.unitPrice.trend if quote else 0.0
                section_total_price += trend * c.quantity

            sections.append(CollectionGroupSection(
                key=key,
                label=group_info["label"],
                order=group_info["order"],
                totalCards=total_cards,
                uniqueCards=unique,
                ownedCards=total_cards,
                missingCards=0,
                completionPercentage=100.0,
                sectionTotalPrice=round(section_total_price, 2),
                sectionMissingPrice=0.0,
                sectionOwnedPrice=round(section_total_price, 2),
                currencySymbol=currency_symbol,
                cards=cat_cards,
            ))

        sections.sort(key=lambda s: s.order)
        return sections

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
            updated = await db.collectioncard.update(
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
                },
            )
            await db.collectioncard.delete(where={"id": card_id})
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
    async def get_dormant_cards(
        user_id: str,
        min_price: float = 0.0,
        provider: str = "cardmarket",
    ) -> DormantCardsResponse:
        from src.services.edhrec_service import EdhrecService
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

        edhrec_norms: set = set()
        for cmd in active_commanders:
            try:
                data = await EdhrecService.fetch_edhrec_data(cmd)
                if data and "cardlists" in data:
                    for cl in data["cardlists"]:
                        for cr in cl.get("cardviews", []):
                            if cr.get("name"):
                                edhrec_norms.add(normalize_card_name(cr["name"]))
            except Exception as e:
                logger.warning(f"Error fetching EDHREC recommendations for commander {cmd}: {e}")

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

