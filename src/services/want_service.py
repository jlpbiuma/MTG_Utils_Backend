import functools
import logging
from typing import List, Dict, Any, Optional

from src.core.db import db
from src.schemas.wants import (
    WantCardCreate,
    WantCardResponse,
    WantStats,
    WantQueryResponse,
    WantGroupSection,
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
from src.services.enrichment_service import trigger_async_priority_enrichment
from src.services.image_resolver import resolve_minio_image_uris, safe_image_uri

logger = logging.getLogger("mtg_backend.wants")


class WantAlreadyOwnedError(Exception):
    def __init__(self, card_name: str):
        self.card_name = card_name
        super().__init__(card_name)

WANT_SORT_FIELDS = (
    "name",
    "cmc",
    "type",
    "quantity",
    "price_trend",
    "price_subtotal",
    "requested_decks",
)


class WantService:
    @staticmethod
    def _to_response(
        c: Any,
        *,
        image_uri: Optional[str] = None,
        requested: Optional[List[DeckRequirement]] = None,
    ) -> WantCardResponse:
        req = requested or []
        return WantCardResponse(
            id=c.id,
            userId=c.userId,
            cardScryfallId=c.cardScryfallId,
            cardName=c.cardName,
            quantity=c.quantity,
            setCode=c.setCode,
            collectorNumber=c.collectorNumber,
            manaCost=c.manaCost,
            typeLine=c.typeLine,
            imageUri=image_uri if image_uri is not None else safe_image_uri(c.imageUri),
            updatedAt=c.updatedAt,
            requestedInDecks=req,
            requestedInDecksCount=len(req),
        )

    @staticmethod
    async def _deck_demand_by_name(user_id: str) -> Dict[str, Dict[str, DeckRequirement]]:
        user_deck_cards = await db.deckcard.find_many(
            where={"deck": {"userId": user_id}},
            include={"deck": True},
        )
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
    async def get_user_wants(
        user_id: str,
        query: Optional[str] = None,
    ) -> List[WantCardResponse]:
        where_clause: Dict[str, Any] = {"userId": user_id}
        if query and query.strip():
            where_clause["cardName"] = {"contains": query.strip(), "mode": "insensitive"}

        cards = await db.wantcard.find_many(
            where=where_clause,
            order=[{"cardName": "asc"}, {"id": "asc"}],
        )
        local_images = await resolve_minio_image_uris([c.cardScryfallId for c in cards])
        decks_by_norm = await WantService._deck_demand_by_name(user_id)

        result: List[WantCardResponse] = []
        for c in cards:
            norm = normalize_card_name(c.cardName)
            req_list = sorted(
                list(decks_by_norm.get(norm, {}).values()),
                key=lambda r: r.deckName.lower(),
            )
            result.append(
                WantService._to_response(
                    c,
                    image_uri=local_images.get(c.cardScryfallId) or safe_image_uri(c.imageUri),
                    requested=req_list,
                )
            )
        return result

    @staticmethod
    async def get_user_wants_query(
        user_id: str,
        query: Optional[str] = None,
        sort: str = "name",
        direction: str = "asc",
        grouped: bool = True,
        price_provider: str = "cardmarket",
    ) -> WantQueryResponse:
        items = await WantService.get_user_wants(user_id, query)

        price_summary = None
        try:
            if sort in WANT_SORT_FIELDS or grouped:
                price_summary = await PricingService.get_price_summary(
                    cards=[
                        {
                            "name": c.cardName,
                            "scryfallId": c.cardScryfallId,
                            "quantity": c.quantity,
                            "ownedQuantity": 0,
                            "missingQuantity": c.quantity,
                            "isMissing": True,
                        }
                        for c in items
                    ],
                    provider=price_provider,
                    bypass_cache=False,
                )
        except Exception:
            price_summary = None

        sorted_items = WantService._sort_items(items, sort, direction, price_summary)
        sections: List[WantGroupSection] = []
        if grouped:
            sections = WantService._group_sections(sorted_items, price_summary)

        total_cards = sum(c.quantity for c in sorted_items)
        return WantQueryResponse(
            query=query or "",
            grouped=grouped,
            provider=price_provider,
            currencySymbol=(
                price_summary.currencySymbol if price_summary is not None else "€"
            ),
            totalCards=total_cards,
            uniqueCards=len(sorted_items),
            sections=sections,
            cards=[] if grouped else sorted_items,
        )

    @staticmethod
    def _quote_for(item: WantCardResponse, price_summary: Optional[Any]):
        if not price_summary or not getattr(price_summary, "quotes", None):
            return None
        quotes = price_summary.quotes
        return quotes.get(item.cardScryfallId) or quotes.get(
            normalize_card_name(item.cardName)
        )

    @staticmethod
    def _sort_items(
        items: List[WantCardResponse],
        sort: str,
        direction: str,
        price_summary: Optional[Any],
    ) -> List[WantCardResponse]:
        multiplier = -1 if direction == "desc" else 1

        def primary(item: WantCardResponse):
            if sort == "cmc":
                return extract_cmc(item.manaCost)
            if sort == "type":
                return (item.typeLine or "").lower()
            if sort == "quantity":
                return item.quantity
            if sort == "requested_decks":
                return item.requestedInDecksCount
            if sort == "price_trend":
                quote = WantService._quote_for(item, price_summary)
                return quote.unitPrice.trend if quote else 0.0
            if sort == "price_subtotal":
                quote = WantService._quote_for(item, price_summary)
                if not quote:
                    return 0.0
                return quote.subtotal if hasattr(quote, "subtotal") else (
                    (quote.unitPrice.trend or 0.0) * item.quantity
                )
            return item.cardName.lower()

        def compare(a: WantCardResponse, b: WantCardResponse):
            pa, pb = primary(a), primary(b)
            if pa != pb:
                return multiplier * (1 if pa > pb else -1)
            return 1 if a.cardName.lower() > b.cardName.lower() else (
                -1 if a.cardName.lower() < b.cardName.lower() else 0
            )

        return sorted(items, key=functools.cmp_to_key(compare))

    @staticmethod
    def _group_sections(
        items: List[WantCardResponse],
        price_summary: Optional[Any],
    ) -> List[WantGroupSection]:
        buckets: Dict[str, List[WantCardResponse]] = {}
        for item in items:
            cat = get_card_category(item.typeLine, item.cardName)
            buckets.setdefault(cat, []).append(item)

        currency_symbol = getattr(price_summary, "currencySymbol", "€") if price_summary else "€"
        sections: List[WantGroupSection] = []
        for key, group_info in CARD_TYPE_GROUPS.items():
            cat_cards = buckets.get(key)
            if not cat_cards:
                continue
            total_cards = sum(c.quantity for c in cat_cards)
            section_total_price = 0.0
            for c in cat_cards:
                quote = WantService._quote_for(c, price_summary)
                trend = quote.unitPrice.trend if quote else 0.0
                section_total_price += trend * c.quantity
            sections.append(
                WantGroupSection(
                    key=key,
                    label=group_info["label"],
                    order=group_info["order"],
                    totalCards=total_cards,
                    uniqueCards=len(cat_cards),
                    ownedCards=0,
                    missingCards=total_cards,
                    completionPercentage=0.0,
                    sectionTotalPrice=round(section_total_price, 2),
                    sectionMissingPrice=round(section_total_price, 2),
                    sectionOwnedPrice=0.0,
                    currencySymbol=currency_symbol,
                    cards=cat_cards,
                )
            )
        sections.sort(key=lambda s: s.order)
        return sections

    @staticmethod
    async def add_or_increment(
        user_id: str,
        data: WantCardCreate,
        *,
        prioritize: bool = True,
    ) -> WantCardResponse:
        owned = await db.collectioncard.find_many(where={"userId": user_id})
        owned_names = {
            normalize_card_name(card.cardName) for card in (owned or [])
        }
        if normalize_card_name(data.cardName) in owned_names:
            raise WantAlreadyOwnedError(data.cardName)

        existing = await db.wantcard.find_unique(
            where={
                "userId_cardScryfallId_want": {
                    "userId": user_id,
                    "cardScryfallId": data.cardScryfallId,
                }
            }
        )

        if existing:
            updated = await db.wantcard.update(
                where={"id": existing.id},
                data={"quantity": {"increment": data.quantity}},
            )
            c = updated
        else:
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

            created = await db.wantcard.create(
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

        if prioritize:
            trigger_async_priority_enrichment([data.cardName])

        return WantService._to_response(c)

    @staticmethod
    async def update_quantity(
        user_id: str,
        card_id: str,
        quantity: int,
        set_code: Optional[str] = None,
        *,
        set_code_provided: bool = False,
    ) -> Optional[WantCardResponse]:
        existing = await db.wantcard.find_unique(where={"id": card_id})
        if not existing or existing.userId != user_id:
            return None

        if quantity <= 0:
            await db.wantcard.delete(where={"id": card_id})
            return None

        data: Dict[str, Any] = {"quantity": quantity}
        if set_code_provided:
            data["setCode"] = set_code

        updated = await db.wantcard.update(where={"id": card_id}, data=data)
        return WantService._to_response(updated)

    @staticmethod
    async def update_card_version(
        user_id: str,
        card_id: str,
        card_scryfall_id: str,
        image_uri: Optional[str] = None,
        set_code: Optional[str] = None,
        collector_number: Optional[str] = None,
    ) -> Optional[WantCardResponse]:
        existing = await db.wantcard.find_unique(where={"id": card_id})
        if not existing or existing.userId != user_id:
            return None

        effective_image = safe_image_uri(image_uri) or image_uri

        if existing.cardScryfallId == card_scryfall_id:
            update_data: Dict[str, Any] = {}
            if effective_image:
                update_data["imageUri"] = effective_image
            if set_code is not None:
                update_data["setCode"] = set_code
            if collector_number is not None:
                update_data["collectorNumber"] = collector_number
            if update_data:
                updated = await db.wantcard.update(where={"id": card_id}, data=update_data)
            else:
                updated = existing
            return WantService._to_response(updated)

        conflict = await db.wantcard.find_first(
            where={
                "userId": user_id,
                "cardScryfallId": card_scryfall_id,
                "id": {"not": card_id},
            }
        )
        if conflict:
            updated = await db.wantcard.update(
                where={"id": conflict.id},
                data={
                    "quantity": conflict.quantity + existing.quantity,
                    "imageUri": effective_image or conflict.imageUri,
                    "setCode": set_code if set_code is not None else conflict.setCode,
                    "collectorNumber": (
                        collector_number
                        if collector_number is not None
                        else conflict.collectorNumber
                    ),
                },
            )
            await db.wantcard.delete(where={"id": card_id})
        else:
            update_data = {"cardScryfallId": card_scryfall_id}
            if effective_image:
                update_data["imageUri"] = effective_image
            if set_code is not None:
                update_data["setCode"] = set_code
            if collector_number is not None:
                update_data["collectorNumber"] = collector_number
            updated = await db.wantcard.update(where={"id": card_id}, data=update_data)

        return WantService._to_response(updated)

    @staticmethod
    async def delete_card(user_id: str, card_id: str) -> bool:
        existing = await db.wantcard.find_unique(where={"id": card_id})
        if not existing or existing.userId != user_id:
            return False
        await db.wantcard.delete(where={"id": card_id})
        return True

    @staticmethod
    async def get_stats(user_id: str) -> WantStats:
        cards = await db.wantcard.find_many(where={"userId": user_id})
        total = sum(c.quantity for c in cards)
        return WantStats(totalCards=total, uniqueCards=len(cards))

    @staticmethod
    async def get_want_normalized_names(user_id: str) -> set:
        cards = await db.wantcard.find_many(where={"userId": user_id})
        return {normalize_card_name(c.cardName) for c in cards}

    @staticmethod
    async def add_deck_missing_to_wants(user_id: str, deck_id: str) -> Dict[str, Any]:
        from src.services.card_utils import is_basic_land
        deck = await db.deck.find_unique(where={"id": deck_id}, include={"cards": True})
        if not deck or deck.userId != user_id:
            return {"status": "error", "message": "Mazo no encontrado", "addedCount": 0}

        col_cards = await db.collectioncard.find_many(where={"userId": user_id})
        col_map = {normalize_card_name(c.cardName): c.quantity for c in col_cards}

        added_count = 0
        added_cards: List[str] = []

        for card in (deck.cards or []):
            if is_basic_land(card.typeLine, card.cardName):
                continue
            norm = normalize_card_name(card.cardName)
            owned_in_col = col_map.get(norm, 0)
            assigned = card.assignedQuantity or 0
            actual_owned = min(card.quantity, max(assigned, min(owned_in_col, card.quantity)))
            missing = max(0, card.quantity - actual_owned)

            if missing > 0:
                existing = await db.wantcard.find_unique(
                    where={
                        "userId_cardScryfallId_want": {
                            "userId": user_id,
                            "cardScryfallId": card.cardScryfallId,
                        }
                    }
                )
                if existing:
                    await db.wantcard.update(
                        where={"id": existing.id},
                        data={"quantity": {"increment": missing}},
                    )
                else:
                    await db.wantcard.create(
                        data={
                            "userId": user_id,
                            "cardScryfallId": card.cardScryfallId,
                            "cardName": card.cardName,
                            "quantity": missing,
                            "manaCost": card.manaCost,
                            "typeLine": card.typeLine,
                            "imageUri": card.imageUri,
                            "setCode": card.setCode,
                        }
                    )
                added_count += missing
                added_cards.append(card.cardName)

        return {
            "status": "success",
            "addedCount": added_count,
            "uniqueCards": len(added_cards),
            "cards": added_cards,
        }

