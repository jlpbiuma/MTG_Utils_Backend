from src.core.db import db


class DeckAssignmentService:
    @staticmethod
    async def pending(user_id: str):
        return await db.query_raw('''
            SELECT n.id, n.deck_id AS "deckId", n.card_name AS "cardName",
                   d.name AS "deckName", n.quantity, n.created_at AS "createdAt"
            FROM deck_assignment_notices n JOIN decks d ON d.id = n.deck_id
            WHERE n.user_id = $1 AND d.user_id = $1
              AND n.accepted_at IS NULL AND NOT d.is_archived
            ORDER BY n.created_at, n.id
        ''', user_id)

    @staticmethod
    async def accept(user_id: str, notice_id: str) -> bool:
        # Repeated acceptance succeeds; notices belonging to others are invisible.
        rows = await db.query_raw('''
            UPDATE deck_assignment_notices SET accepted_at = COALESCE(accepted_at, NOW())
            WHERE id = $1 AND user_id = $2 RETURNING id
        ''', notice_id, user_id)
        return bool(rows)
