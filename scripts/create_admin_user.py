"""Create or update the local admin account used for cross-account testing.

    ADMIN_IA_EMAIL=... ADMIN_IA_PASSWORD=... \
    DATABASE_URL=postgresql://...@localhost:5432/mtg_utils uv run python -m scripts.create_admin_user

Refuses any database that is not on this machine: the account is for the
local copy of production (make sync-prod-db), never for production itself.
The API only grants admin powers to emails listed in ADMIN_USER_EMAILS.
"""

import asyncio
import os
import sys
from urllib.parse import urlparse

from prisma import Prisma

from src.services.auth_service import hash_password

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


async def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        print(f"Refusing: DATABASE_URL host {host!r} is not local.", file=sys.stderr)
        return 1
    email = os.environ.get("ADMIN_IA_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_IA_PASSWORD", "")
    if not email or len(password) < 12:
        print("Set ADMIN_IA_EMAIL and ADMIN_IA_PASSWORD (12+ characters).", file=sys.stderr)
        return 1

    db = Prisma(datasource={"url": url})
    await db.connect()
    try:
        user = await db.user.upsert(
            where={"email": email},
            data={
                "create": {"email": email, "name": "admin-ia", "passwordHash": hash_password(password)},
                "update": {"name": "admin-ia", "passwordHash": hash_password(password)},
            },
        )
    finally:
        await db.disconnect()
    print(f"Admin user ready: {user.email} ({user.id})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
