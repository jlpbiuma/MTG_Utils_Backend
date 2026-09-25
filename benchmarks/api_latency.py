"""HTTP latency harness for hot API endpoints (local or production).

Usage:
  MTG_API_BASE=http://192.168.0.4:8000 \\
  MTG_API_USER_ID=<uuid> \\
  uv run python benchmarks/api_latency.py --runs 7 --out benchmarks/api_latency_baseline_prod.json

Auth uses X-User-Id (same as backend get_current_user_id fallback). Optional Bearer via MTG_API_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path(__file__).parent / "api_latency_results.json"

ENDPOINTS: List[Dict[str, Any]] = [
    {"name": "health", "method": "GET", "path": "/api/health", "auth": False},
    {"name": "decks", "method": "GET", "path": "/api/decks", "auth": True},
    {"name": "collection_query", "method": "GET", "path": "/api/collection/query", "auth": True},
    {"name": "wants_query", "method": "GET", "path": "/api/wants/query", "auth": True},
    {"name": "priorities", "method": "GET", "path": "/api/priorities?page=1&limit=100", "auth": True},
    {"name": "scryfall_search", "method": "GET", "path": "/api/scryfall/search?q=sol+ring&limit=15", "auth": False},
    {
        "name": "pricing_collection",
        "method": "POST",
        "path": "/api/pricing",
        "auth": True,
        "body": {"includeCollection": True, "provider": "cardmarket", "forceRefresh": False},
    },
    {
        "name": "pricing_collection_history",
        "method": "GET",
        "path": "/api/pricing/collection/history?days=30",
        "auth": True,
    },
    {"name": "whatsapp_matches", "method": "GET", "path": "/api/whatsapp/matches", "auth": True},
]


def _request(
    base: str,
    method: str,
    path: str,
    headers: Dict[str, str],
    body: Optional[dict] = None,
) -> Tuple[int, float, int]:
    data = None
    req_headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = Request(f"{base.rstrip('/')}{path}", data=data, headers=req_headers, method=method)
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=120) as resp:
            payload = resp.read()
            status = resp.status
    except HTTPError as exc:
        payload = exc.read() if exc.fp else b""
        status = exc.code
    except URLError as exc:
        raise SystemExit(f"Request failed for {method} {path}: {exc}") from exc
    duration_ms = (time.perf_counter() - started) * 1000.0
    return status, duration_ms, len(payload)


def _stats(samples: List[float]) -> Dict[str, float]:
    ordered = sorted(samples)
    n = len(ordered)
    p95_idx = min(n - 1, max(0, int(round(0.95 * (n - 1)))))
    return {
        "n": n,
        "mean_ms": round(statistics.fmean(ordered), 1),
        "median_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[p95_idx], 1),
        "min_ms": round(ordered[0], 1),
        "max_ms": round(ordered[-1], 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure hot API endpoint latency")
    parser.add_argument("--base", default=os.getenv("MTG_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--user-id", default=os.getenv("MTG_API_USER_ID", "00000000-0000-0000-0000-000000000000"))
    parser.add_argument("--token", default=os.getenv("MTG_API_TOKEN", ""))
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--deck-id", default=os.getenv("MTG_API_DECK_ID", ""), help="Optional deck id for detail probe")
    args = parser.parse_args()

    headers: Dict[str, str] = {"Accept": "application/json", "X-User-Id": args.user_id}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    endpoints = list(ENDPOINTS)
    if args.deck_id:
        endpoints.insert(
            2,
            {"name": "deck_detail", "method": "GET", "path": f"/api/decks/{args.deck_id}", "auth": True},
        )
        endpoints.insert(
            3,
            {
                "name": "deck_view",
                "method": "GET",
                "path": f"/api/decks/{args.deck_id}/view?provider=cardmarket",
                "auth": True,
            },
        )

    results: Dict[str, Any] = {
        "schema": "api_latency_v1",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "user_id": args.user_id,
        "runs": args.runs,
        "endpoints": {},
    }

    print(f"Base={args.base} runs={args.runs} user={args.user_id}")
    for ep in endpoints:
        samples: List[float] = []
        statuses: List[int] = []
        sizes: List[int] = []
        hdrs = headers if ep.get("auth", True) else {"Accept": "application/json"}
        for _ in range(args.runs):
            status, ms, size = _request(args.base, ep["method"], ep["path"], hdrs, ep.get("body"))
            samples.append(ms)
            statuses.append(status)
            sizes.append(size)
            print(f"  {ep['name']:28} {ms:8.1f} ms  status={status}  bytes={size}")
        results["endpoints"][ep["name"]] = {
            **_stats(samples),
            "status_codes": statuses,
            "median_bytes": int(statistics.median(sizes)),
            "path": ep["path"],
            "method": ep["method"],
        }
        summary = results["endpoints"][ep["name"]]
        print(
            f"→ {ep['name']}: median={summary['median_ms']}ms "
            f"p95={summary['p95_ms']}ms bytes≈{summary['median_bytes']}"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
