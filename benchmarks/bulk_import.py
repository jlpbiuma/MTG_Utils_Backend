"""Real PostgreSQL benchmark; prototypes are NOT installed in the application.

Reads public.card_catalog, creates a unique benchmark schema, writes only there,
then drops that schema. Worker dispatch is replaced with an in-memory recorder.
No Scryfall requests, no writes to user collections in public.
"""
import asyncio
import json
import math
import os
import statistics
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prisma import Prisma
from src.services import collection_service, scryfall_service
from src.services.card_utils import normalize_card_name
from src.services.import_service import parse_decklist_text

OUT = Path(__file__).parent
BASE_URL = os.getenv("BENCHMARK_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mtg_utils")


def group_lines(raw):
    groups = {}
    for item in parse_decklist_text(raw):
        if item.quantity < 1:
            raise ValueError("Quantity must be positive")
        # Preserve first-line metadata, matching the current name-level model.
        key = normalize_card_name(item.name)
        if key in groups:
            groups[key].quantity += item.quantity
        else:
            groups[key] = item.model_copy()
    return list(groups.values())


async def grouped_import(raw):
    items = group_lines(raw)
    lines = []
    for item in items:
        suffix = f" ({item.setCode}) {item.collectorNumber or ''}" if item.setCode else ""
        lines.append(f"{item.quantity} {item.name}{suffix}")
    return await collection_service.CollectionService.import_collection_text("benchmark", "\n".join(lines))


async def batched_import(client, raw, dispatch):
    items = group_lines(raw)
    catalog = {}
    for start in range(0, len(items), 500):
        records = await client.cardcatalog.find_many(where={"normalizedName": {"in": [normalize_card_name(i.name) for i in items[start:start + 500]]}})
        catalog.update({r.normalizedName: r for r in records})
    rows, missing = [], []
    for item in items:
        norm = normalize_card_name(item.name)
        card = catalog.get(norm)
        if card is None:
            missing.append(item.name)
        rows.append({"id": str(uuid.uuid4()), "user_id": "benchmark",
                     "card_scryfall_id": card.id if card else f"pending:{item.name}",
                     "card_name": item.name, "quantity": item.quantity,
                     "set_code": item.setCode or (card.setCode if card else None),
                     "collector_number": item.collectorNumber or (card.collectorNumber if card else None),
                     "mana_cost": card.manaCost if card else None,
                     "type_line": card.typeLine if card else None,
                     "image_uri": card.imageUri if card else None})
    # Parameterized bulk writes, atomic quantity increments, all-or-nothing import.
    async with client.tx(timeout=60000) as tx:
        for start in range(0, len(rows), 500):
            await tx.execute_raw('''
                INSERT INTO user_collections
                  (id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number,mana_cost,type_line,image_uri,updated_at)
                SELECT id,user_id,card_scryfall_id,card_name,quantity,set_code,collector_number,mana_cost,type_line,image_uri,NOW()
                FROM jsonb_to_recordset($1::jsonb) AS x(
                  id text,user_id text,card_scryfall_id text,card_name text,quantity integer,
                  set_code text,collector_number text,mana_cost text,type_line text,image_uri text)
                ON CONFLICT (user_id,card_scryfall_id)
                DO UPDATE SET quantity=user_collections.quantity+EXCLUDED.quantity,updated_at=NOW()
            ''', json.dumps(rows[start:start + 500]))
    dispatch(missing)
    return {"importedCount": sum(i.quantity for i in items), "uniqueCards": len(items)}


async def main():
    if "?" in BASE_URL:
        raise ValueError("Use a database URL without query parameters")
    admin = Prisma(datasource={"url": BASE_URL + "?schema=public"})
    schema = "benchmark_import_" + uuid.uuid4().hex
    client = Prisma(datasource={"url": BASE_URL + "?schema=" + schema})
    report = {"schema": schema, "trials": [], "parser_skips": [], "cleaned_up": False,
              "notes": "Local real PostgreSQL/Prisma, public catalog read only, worker dispatch mocked; no remote requests"}
    await admin.connect()
    created = False
    try:
        source = await admin.query_raw('SELECT id,name,normalized_name,mana_cost,type_line,image_uri,set_code,collector_number FROM public.card_catalog ORDER BY md5(name) LIMIT 6000')
        safe = []
        for row in source:
            parsed = parse_decklist_text("1 " + row["name"])
            if len(parsed) == 1 and parsed[0].name == row["name"]:
                safe.append(row)
            else:
                report["parser_skips"].append(row["name"])
        if len(safe) < 5000:
            raise RuntimeError("Need at least 5000 parseable real local card names")
        source = safe[:5000]
        (OUT / "card_names.json").write_text(json.dumps([r["name"] for r in source], indent=2))
        await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
        created = True
        for table in ("card_catalog", "user_collections"):
            await admin.execute_raw(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
        await client.connect()
        # Raw query search_path must target the same isolated schema as Prisma.
        path = await client.query_raw('SELECT current_schema() AS name')
        if path[0]["name"] != schema:
            raise RuntimeError(f"Unsafe search_path: {path}")
        cases = [(1000,1000,True),(1000,1000,False),(5000,5000,True),(5000,5000,False),(5000,1000,True)]
        for count, unique, warm in cases:
            rows = source[:unique]
            raw = "\n".join("1 " + rows[i % unique]["name"] for i in range(count))
            expected = Counter(rows[i % unique]["name"] for i in range(count))
            await client.execute_raw('TRUNCATE card_catalog')
            if warm:
                await client.execute_raw('''INSERT INTO card_catalog (id,name,normalized_name,mana_cost,type_line,image_uri,set_code,collector_number,updated_at)
                    SELECT id,name,normalized_name,mana_cost,type_line,image_uri,set_code,collector_number,NOW()
                    FROM jsonb_to_recordset($1::jsonb) AS x(id text,name text,normalized_name text,mana_cost text,type_line text,image_uri text,set_code text,collector_number text)''', json.dumps(rows))
            case = f"{count}_lines_{unique}_unique_{'warm' if warm else 'cold'}"
            for repetition in range(3):
                variants = ["current", "grouped", "batched"]
                variants = variants[repetition:] + variants[:repetition]
                for variant in variants:
                    await client.execute_raw('TRUNCATE user_collections')
                    sent = []
                    def dispatch(names):
                        sent.extend(names)
                    start = time.perf_counter()
                    with (patch.object(collection_service, "db", client),
                          patch.object(scryfall_service, "db", client),
                          patch.object(collection_service, "trigger_async_priority_enrichment", dispatch)):
                        if variant == "current":
                            result = await collection_service.CollectionService.import_collection_text("benchmark", raw)
                        elif variant == "grouped":
                            result = await grouped_import(raw)
                        else:
                            result = await batched_import(client, raw, dispatch)
                    elapsed = time.perf_counter() - start
                    stored = await client.collectioncard.find_many()
                    actual = {r.cardName: r.quantity for r in stored}
                    assert actual == dict(expected), (case, variant, "quantity mismatch")
                    assert result["importedCount"] == count
                    trial = {"case": case, "variant": variant, "repetition": repetition,
                             "seconds": round(elapsed, 4), "stored_rows": len(stored),
                             "total_quantity": sum(actual.values()), "returned_unique_cards": result["uniqueCards"],
                             "dispatched_names": len(set(sent)), "quantity_verified": True}
                    report["trials"].append(trial)
                    (OUT / "results.json").write_text(json.dumps(report, indent=2))
                    print(json.dumps(trial), flush=True)
        # Failure injection: valid first line and invalid zero quantity second.
        report["invalid_input"] = []
        for variant in ("current", "batched"):
            await client.execute_raw('TRUNCATE user_collections')
            raw = f'1 {source[0]["name"]}\n0 {source[1]["name"]}'
            error = None
            try:
                with (patch.object(collection_service, "db", client), patch.object(scryfall_service, "db", client),
                      patch.object(collection_service, "trigger_async_priority_enrichment", lambda _: None)):
                    if variant == "current":
                        await collection_service.CollectionService.import_collection_text("benchmark", raw)
                    else:
                        await batched_import(client, raw, lambda _: None)
            except Exception as exc:
                error = type(exc).__name__
            stored = await client.collectioncard.find_many()
            report["invalid_input"].append({"variant": variant, "error": error, "rows_left": len(stored), "quantity": sum(r.quantity for r in stored)})
        report["medians"] = []
        for case in dict.fromkeys(t["case"] for t in report["trials"]):
            for variant in ("current", "grouped", "batched"):
                values = [t["seconds"] for t in report["trials"] if t["case"] == case and t["variant"] == variant]
                report["medians"].append({"case": case, "variant": variant, "seconds": round(statistics.median(values),4)})
    finally:
        if client.is_connected():
            await client.disconnect()
        if created:
            await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
            report["cleaned_up"] = True
        await admin.disconnect()
        (OUT / "results.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
