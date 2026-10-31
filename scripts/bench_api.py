"""Measure the read API against a local copy of production and compare runs.

Usage (from backend/, with the API running on a local database):
    uv run python scripts/bench_api.py run --user <id> --out before.json
    uv run python scripts/bench_api.py run --user <id> --out after.json
    uv run python scripts/bench_api.py compare before.json after.json

Each endpoint is called ``--repeat`` times after one warm-up call. The digest
is a SHA-256 of the canonical JSON response with volatile keys removed, so two
runs with the same digest returned exactly the same data. Never point this at
production: it only reads, but it is meant for a disposable local copy.
"""
import argparse
import hashlib
import json
import statistics
import sys
import time

import httpx

VOLATILE_KEYS = {"generatedAt", "updatedAt", "fetchedAt", "timestamp", "now", "asOf", "lastUpdated"}


def endpoints(deck_id: str, set_code: str) -> list[tuple[str, str]]:
    return [
        ("decks", "/api/decks"),
        ("deck view", f"/api/decks/{deck_id}/view"),
        ("deck detail", f"/api/decks/{deck_id}"),
        ("deck overlap", "/api/decks/overlap"),
        ("assignment notices", "/api/decks/assignment-notices"),
        ("collection stats", "/api/collection/stats"),
        ("collection query flat p1", "/api/collection/query?grouped=false&page=1&limit=60"),
        ("collection query flat p2", "/api/collection/query?grouped=false&page=2&limit=60"),
        ("collection query grouped", "/api/collection/query?grouped=true&page=1&limit=60"),
        ("collection query price sort", "/api/collection/query?grouped=false&sort=price_trend&direction=desc&page=1&limit=60"),
        ("collection search", "/api/collection/query?query=dragon&grouped=false&page=1&limit=60"),
        ("collection dormant", "/api/collection/dormant"),
        ("wants query", "/api/wants/query?page=1&limit=60"),
        ("wants stats", "/api/wants/stats"),
        ("priorities", "/api/priorities"),
        ("catalog sets", "/api/catalog/sets"),
        ("set cards", f"/api/catalog/sets/{set_code}/cards"),
        ("set value history", f"/api/catalog/sets/{set_code}/value-history"),
        ("expansion performance", f"/api/expansions/{set_code}/performance"),
        ("deck value history", f"/api/decks/{deck_id}/value-history"),
        ("price movers", "/api/pricing/movers"),
        ("collection value history", "/api/pricing/collection/history"),
        ("simulated collections", "/api/simulated-collections"),
        ("acquisition expenses", "/api/acquisitions/expenses"),
        ("acquisition batches", "/api/acquisition-batches"),
    ]


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in sorted(value.items()) if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def digest(payload) -> str:
    return hashlib.sha256(json.dumps(_strip(payload), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def run(args) -> None:
    headers = {"X-User-Id": args.user}
    results = {}
    with httpx.Client(base_url=args.base, headers=headers, timeout=args.timeout) as client:
        for name, path in endpoints(args.deck, args.set):
            if args.only and args.only not in name:
                continue
            timings, status, size, sha = [], None, 0, None
            for attempt in range(args.repeat + 1):
                start = time.perf_counter()
                try:
                    res = client.get(path)
                except httpx.HTTPError as error:
                    status, sha = f"error: {type(error).__name__}", None
                    break
                elapsed = (time.perf_counter() - start) * 1000
                status, size = res.status_code, len(res.content)
                if attempt == 0:
                    sha = digest(res.json()) if res.headers.get("content-type", "").startswith("application/json") else None
                    continue
                timings.append(elapsed)
            results[name] = {
                "path": path, "status": status, "bytes": size, "digest": sha,
                "median_ms": round(statistics.median(timings), 1) if timings else None,
                "max_ms": round(max(timings), 1) if timings else None,
            }
            r = results[name]
            print(f"{name:32} {str(r['status']):>6} {r['median_ms'] or '-':>9} ms {r['bytes']:>10} B  {r['digest']}", flush=True)
    with open(args.out, "w") as handle:
        json.dump(results, handle, indent=2)


def compare(args) -> None:
    before, after = (json.load(open(path)) for path in (args.before, args.after))
    print(f"{'endpoint':32} {'antes ms':>10} {'después ms':>11} {'factor':>7}  resultado")
    for name, old in before.items():
        new = after.get(name)
        if not new:
            continue
        factor = (old["median_ms"] / new["median_ms"]) if old["median_ms"] and new["median_ms"] else None
        same = "igual" if old["digest"] == new["digest"] else "DISTINTO"
        print(f"{name:32} {old['median_ms'] or '-':>10} {new['median_ms'] or '-':>11} "
              f"{(f'{factor:.1f}x' if factor else '-'):>7}  {same}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:8000")
    r.add_argument("--user", required=True)
    r.add_argument("--deck", required=True)
    r.add_argument("--set", required=True)
    r.add_argument("--repeat", type=int, default=3)
    r.add_argument("--timeout", type=float, default=120)
    r.add_argument("--only")
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    args = parser.parse_args()
    run(args) if args.command == "run" else compare(args)


if __name__ == "__main__":
    sys.exit(main())
