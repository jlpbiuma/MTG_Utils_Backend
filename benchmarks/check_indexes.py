"""Check whether performance indexes from indexes_optimization.sql exist.

Usage:
  DATABASE_URL=postgresql://... uv run python benchmarks/check_indexes.py
  # or via docker:
  docker compose exec -T db psql -U postgres -d mtg_utils -c "..."
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prisma import Prisma  # noqa: E402

EXPECTED = [
    "card_catalog_search_name_trgm_idx",
    "card_catalog_search_name_es_trgm_idx",
    "card_printings_prices_updated_at_idx",
    "card_printings_prices_updated_at_nulls_first_idx",
    "card_printings_updated_at_idx",
    "card_catalog_updated_at_idx",
    "user_collections_user_id_card_name_idx",
    "deck_cards_card_scryfall_id_idx",
]


async def main() -> int:
    url = os.getenv("DATABASE_URL") or os.getenv("BENCHMARK_DATABASE_URL")
    client = Prisma()
    await client.connect()
    try:
        ext = await client.query_raw(
            "SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'"
        )
        rows = await client.query_raw(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = ANY($1::text[])
            ORDER BY indexname
            """,
            EXPECTED,
        )
        present = {r["indexname"] for r in rows}
        report = {
            "pg_trgm": bool(ext),
            "present": sorted(present),
            "missing": [name for name in EXPECTED if name not in present],
        }
        print(json.dumps(report, indent=2))
        return 0 if report["pg_trgm"] and not report["missing"] else 1
    finally:
        await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
