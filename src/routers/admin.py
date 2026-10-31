from fastapi import APIRouter, Depends
from src.core.auth import require_admin
from src.core.db import db

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users")
async def list_users():
    """Accounts with their data volume, to pick one for X-User-Id."""
    return await db.query_raw(
        """
        SELECT u.id, u.email, u.name, u.created_at AS "createdAt",
               (SELECT COUNT(*)::int FROM decks d WHERE d.user_id = u.id) AS decks,
               (SELECT COALESCE(SUM(c.quantity), 0)::int FROM user_collections c WHERE c.user_id = u.id) AS "collectionCards",
               (SELECT COUNT(*)::int FROM user_wants w WHERE w.user_id = u.id) AS wants
        FROM users u
        ORDER BY "collectionCards" DESC, u.email
        """
    )
