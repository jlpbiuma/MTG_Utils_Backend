"""Local-only deck reads. Never resolve/enrich cards or write during a GET."""
import json
from datetime import datetime, timezone
from src.core.db import db
from src.schemas.deck import OtherDeckAssignment
from src.schemas.deck_view import DeckViewCard, DeckViewResponse
from src.schemas.pricing import PriceSummary
from src.services.card_utils import normalize_card_name, is_basic_land, extract_colors_from_mana_cost
from src.services.image_resolver import resolve_minio_image_uris, safe_image_uri
from src.services.pricing_service import PricingService

# Same front-face, whitespace and case normalization as normalize_card_name.
# Only this static expression is interpolated; all user data is bound separately.
_NORMALIZED_NAME = "btrim(split_part(regexp_replace(lower(btrim(card_name)), '\\s+', ' ', 'g'), '/', 1))"


class DeckViewService:
    @staticmethod
    async def get_view(deck_id: str, user_id: str, provider: str = "cardmarket"):
        deck = await db.deck.find_first(
            where={"id": deck_id, "userId": user_id}, include={"cards": True}
        )
        if deck is None:
            return None
        cards = deck.cards or []
        names = json.dumps(sorted({normalize_card_name(c.cardName) for c in cards}))
        collection = []
        assignments = []
        if cards:
            collection = await db.query_raw(
                f'''SELECT {_NORMALIZED_NAME} AS name, SUM(quantity)::int AS quantity
                    FROM user_collections WHERE user_id=$1
                    AND {_NORMALIZED_NAME} IN (SELECT jsonb_array_elements_text($2::jsonb))
                    GROUP BY 1''', user_id, names,
            )
            assignments = await db.query_raw(
                f'''SELECT {_NORMALIZED_NAME} AS name, d.id AS "deckId", d.name AS "deckName",
                           SUM(c.assigned_quantity)::int AS quantity
                    FROM deck_cards c JOIN decks d ON d.id=c.deck_id
                    WHERE d.user_id=$1 AND d.id<>$2 AND c.assigned_quantity>0
                    AND {_NORMALIZED_NAME} IN (SELECT jsonb_array_elements_text($3::jsonb))
                    GROUP BY 1, d.id, d.name''', user_id, deck_id, names,
            )
        owned_by_name = {r["name"]: r["quantity"] for r in collection}
        other_by_name = {}
        for row in assignments:
            other_by_name.setdefault(row["name"], []).append(OtherDeckAssignment(**{
                key: row[key] for key in ("deckId", "deckName", "quantity")
            }))
        images = await resolve_minio_image_uris(list({c.cardScryfallId for c in cards if c.cardScryfallId}))
        currency = "USD" if provider == "mtggoldfish" else "EUR"
        symbol = "$" if currency == "USD" else "€"
        unique_cards = {c.cardScryfallId or normalize_card_name(c.cardName): {
            "name": c.cardName, "scryfallId": c.cardScryfallId
        } for c in cards}
        quotes = await PricingService.get_latest_quotes_batch(list(unique_cards.values()), provider, currency)
        # Send each unit quote once. Quantities/subtotals belong to the visible rows.
        visible_quotes = {}
        result = []
        total = owned = 0
        total_value = owned_value = missing_value = 0.0
        for card in cards:
            norm = normalize_card_name(card.cardName)
            collection_qty = owned_by_name.get(norm, 0)
            others = other_by_name.get(norm, [])
            basic = is_basic_land(card.typeLine, card.cardName)
            effective_owned = card.quantity if basic else min(card.quantity, max(card.assignedQuantity or 0, min(collection_qty, card.quantity)))
            missing = max(0, card.quantity - effective_owned)
            if not card.isSideboard:
                total += card.quantity
                owned += effective_owned
            quote = quotes.get(card.cardScryfallId) or quotes.get(norm)
            unit = quote.unitPrice.trend if quote else 0.0
            if quote:
                visible_quotes[card.cardScryfallId or norm] = quote
            total_value += unit * card.quantity
            owned_value += unit * effective_owned
            missing_value += unit * missing
            result.append(DeckViewCard(
                id=card.id, deckId=card.deckId, cardScryfallId=card.cardScryfallId,
                cardName=card.cardName, quantity=card.quantity,
                assignedQuantity=card.assignedQuantity, isSideboard=card.isSideboard,
                isCommander=card.isCommander, manaCost=card.manaCost, typeLine=card.typeLine,
                imageUri=images.get(card.cardScryfallId) or safe_image_uri(card.imageUri),
                setCode=card.setCode, ownedInCollection=card.quantity if basic else collection_qty,
                availableToAssign=max(0, collection_qty - sum(o.quantity for o in others) - card.assignedQuantity),
                assignedInOtherDecks=others, missingCount=missing,
            ))
        commander = next((c for c in cards if c.isCommander), None)
        commander_name = commander.cardName if commander else deck.commander
        identity = extract_colors_from_mana_cost(commander.manaCost) if commander else []
        if commander_name:
            rows = await db.query_raw(
                "SELECT details_es->'color_identity' AS identity FROM card_catalog WHERE normalized_name=$1",
                normalize_card_name(commander_name),
            )
            if rows and isinstance(rows[0].get("identity"), list):
                identity = [color for color in "WUBRG" if color in rows[0]["identity"]]
        return DeckViewResponse(
            commanderColorIdentity=identity,
            **{key: getattr(deck, key) for key in (
                "id", "userId", "name", "format", "description", "commander",
                "commanderScryfallId", "createdAt", "updatedAt"
            )},
            commanderImageUri=safe_image_uri(deck.commanderImageUri),
            totalCards=total, uniqueCards=len(result), ownedCards=owned,
            missingCards=max(0, total-owned), completionPercentage=round(owned / total * 100, 1) if total else 0,
            cards=result,
            priceSummary=PriceSummary(
                provider=provider, currency=currency, currencySymbol=symbol,
                totalCards=total, totalNetValue=round(total_value, 2),
                totalOwnedValue=round(owned_value, 2), totalMissingValue=round(missing_value, 2),
                quotes=visible_quotes, lastUpdated=datetime.now(timezone.utc),
            ),
        )
