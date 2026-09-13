"""Download one official Oracle bulk file and measure a local name index.

No application DB writes. Temporary download removed on exit. This evaluates
base-name resolution only, not printings, localized text, images or live prices.
"""
import argparse
import gzip
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.card_utils import normalize_card_name


def main(url):
    if not url.startswith("https://data.scryfall.io/oracle-cards/"):
        raise ValueError("Expected the official Oracle Cards bulk URL")
    names = json.loads((Path(__file__).parent / "card_names.json").read_text())
    with tempfile.TemporaryDirectory(prefix="mtg-bulk-benchmark-") as temp:
        path = Path(temp) / "oracle.jsonl.gz"
        started = time.perf_counter()
        with httpx.stream("GET", url, headers={"User-Agent": "MTGUtilsWorker/1.0 (bulk diagnostic)", "Accept-Encoding": "identity"}, timeout=60, follow_redirects=True) as response:
            response.raise_for_status()
            with path.open("wb") as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
        download_seconds = time.perf_counter() - started
        started = time.perf_counter()
        index, records, collisions, ambiguous = {}, 0, 0, set()
        with gzip.open(path, "rt") as stream:
            for line in stream:
                card = json.loads(line)
                records += 1
                key = normalize_card_name(card["name"])
                if key in index:
                    collisions += 1
                    ambiguous.add(key)
                index[key] = {"id": card["id"], "name": card["name"]}
        index_seconds = time.perf_counter() - started
        lookup_seconds = []
        for _ in range(3):
            started = time.perf_counter()
            found = [index.get(normalize_card_name(name)) for name in names]
            lookup_seconds.append(time.perf_counter() - started)
        report = {"source_url": url, "compressed_bytes": path.stat().st_size,
                  "download_seconds": round(download_seconds,4), "index_seconds": round(index_seconds,4),
                  "source_records": records, "indexed_keys": len(index), "normalized_collisions": collisions,
                  "requested_names": len(names), "resolved_names": sum(c is not None for c in found),
                  "ambiguous_names": [n for n in names if normalize_card_name(n) in ambiguous],
                  "missing_names": [n for n,c in zip(names,found) if c is None],
                  "lookup_seconds": lookup_seconds, "lookup_median_seconds": statistics.median(lookup_seconds),
                  "limitations": "Dictionary lookup in memory, not PostgreSQL import; one representative printing per Oracle ID; no images, language enrichment or rulings."}
        (Path(__file__).parent / "bulk_catalog_results.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    main(parser.parse_args().url)
