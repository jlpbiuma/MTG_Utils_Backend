"""User-scoped persistence for card lists imported into opportunities."""
import json
from datetime import datetime

from prisma import Json
from src.core.db import db


def _to_match_row(card) -> dict:
    return {
        # Negative IDs keep persisted manual rows separate from the bot's SQLite IDs.
        "id": -card.id, "url": card.sourceUrl, "card_name": card.cardName,
        "set_code": card.setCode, "price": None, "currency": card.currency,
        "condition": None, "extra_json": json.dumps(card.extra),
        "subject": card.subject, "source_phone": "", "source_chat": None,
        "raw_message": None, "detected_at": card.importedAt.isoformat(),
    }


async def read_saved_rows(user_id: str) -> list[dict]:
    cards = await db.manualopportunitycard.find_many(
        where={"userId": user_id}, order=[{"importedAt": "desc"}, {"id": "asc"}],
    )
    return [_to_match_row(card) for card in cards]


async def save_import(user_id: str, url: str, subject: str, rows: list[dict], imported_at: datetime) -> list[dict]:
    where = {"userId": user_id, "sourceUrl": url}
    async with db.tx() as tx:
        # Serialise simultaneous imports of the same user's URL, including the
        # first import when there are no existing rows to lock.
        await tx.execute_raw("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", json.dumps([user_id, url]))
        await tx.manualopportunitycard.delete_many(where=where)
        await tx.manualopportunitycard.create_many(data=[{
            "userId": user_id, "sourceUrl": url, "subject": subject,
            "cardName": row["card_name"], "setCode": row["set_code"],
            "currency": row["currency"], "extra": Json(json.loads(row["extra_json"])),
            "importedAt": imported_at,
        } for row in rows])
        saved = await tx.manualopportunitycard.find_many(where=where, order={"id": "asc"})
    return [_to_match_row(card) for card in saved]
