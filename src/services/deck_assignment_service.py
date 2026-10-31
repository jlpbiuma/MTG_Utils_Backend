from src.core.db import db
from src.services.image_resolver import safe_image_uri


class DeckAssignmentService:
    @staticmethod
    async def pending(user_id: str):
        rows = await db.query_raw('''
            SELECT n.id, n.deck_id AS "deckId", n.card_name AS "cardName",
                   d.name AS "deckName", n.quantity, n.created_at AS "createdAt",
                   COALESCE(c.image_uri, (
                       SELECT p.image_uri FROM card_printings p
                       WHERE p.catalog_id = c.id AND p.image_uri IS NOT NULL
                       LIMIT 1
                   ), (
                       SELECT dc.image_uri FROM deck_cards dc
                       WHERE dc.deck_id = n.deck_id AND LOWER(dc.card_name) = LOWER(n.card_name) AND dc.image_uri IS NOT NULL
                       LIMIT 1
                   )) AS "imageUri"
            FROM deck_assignment_notices n JOIN decks d ON d.id = n.deck_id
            -- One catalog card per notice, through indexed lookups: an OR join
            -- scanned the whole catalog and could repeat a notice.
            LEFT JOIN LATERAL (
                SELECT m.id, m.image_uri FROM (
                    SELECT cc.id, cc.image_uri, 0 AS rank FROM card_catalog cc WHERE cc.normalized_name = LOWER(n.card_name)
                    UNION ALL
                    SELECT cc.id, cc.image_uri, 1 FROM card_catalog cc WHERE LOWER(cc.name) = LOWER(n.card_name)
                ) m ORDER BY m.rank, m.id LIMIT 1
            ) c ON true
            WHERE n.user_id = $1 AND d.user_id = $1
              AND n.accepted_at IS NULL AND NOT d.is_archived
            ORDER BY n.created_at, n.id
        ''', user_id)
        for r in rows:
            if "imageUri" in r:
                r["imageUri"] = safe_image_uri(r["imageUri"])
        return rows

    @staticmethod
    async def accept(user_id: str, notice_id: str) -> bool:
        # Repeated acceptance succeeds; notices belonging to others are invisible.
        rows = await db.query_raw('''
            UPDATE deck_assignment_notices SET accepted_at = COALESCE(accepted_at, NOW())
            WHERE id = $1 AND user_id = $2 RETURNING id
        ''', notice_id, user_id)
        return bool(rows)
