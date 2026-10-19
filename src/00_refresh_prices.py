"""Refresh the pricing seed from data/price_catalog.json and live provider pages.

Fetches each committed source URL and fails if none of the published price
needles are still on the page. Does not scrape dollar amounts out of JS
pricing widgets. --offline rewrites the seed without fetching. --check makes no
network calls and writes nothing: it exits non-zero if the committed seed is not
exactly what the catalog renders to (for example after a hand edit to the seed).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from catalog import REFRESH_HINT, load_catalog, render_seed, seed_drift
from config import DATA_DIR, SEED_PATH

SNAPSHOT_PATH = DATA_DIR / "price_snapshot.json"


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "llm-cost-lab/0.1"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.status, resp.read(1_500_000).decode("utf-8", errors="replace")


def check_pages(catalog: dict, offline: bool) -> list[dict]:
    results = []
    urls = sorted({row["source_url"] for row in catalog["commitment_terms"]})
    for url in urls:
        needles = sorted({n for row in catalog["commitment_terms"] if row["source_url"] == url for n in row["needles"]})
        if offline:
            results.append({"url": url, "status": "skipped", "needles": needles, "hit": []})
            continue
        try:
            status, body = fetch(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            results.append({"url": url, "status": f"error: {exc}", "needles": needles, "hit": [], "ok": False})
            continue
        hit = [n for n in needles if n in body]
        results.append({"url": url, "http": status, "needles": needles, "hit": hit, "ok": bool(hit)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="rewrite the seed without fetching live pages")
    mode.add_argument("--check", action="store_true", help="verify the seed matches the catalog; no network, no writes")
    args = parser.parse_args()
    catalog = load_catalog()

    if args.check:
        drift = seed_drift(catalog)
        if drift:
            print("\n".join(drift[:60]))
            raise SystemExit(
                f"\n{SEED_PATH.name} is out of sync with price_catalog.json. "
                f"If the catalog is right, run: {REFRESH_HINT}. "
                "If you hand-edited the seed, move that change into the catalog first."
            )
        print(f"{SEED_PATH.name} matches price_catalog.json")
        return

    fetched_on = catalog["fetched_on"] if args.offline else date.today().isoformat()
    checks = check_pages(catalog, args.offline)
    SNAPSHOT_PATH.write_text(json.dumps({"fetched_on": fetched_on, "offline": args.offline, "checks": checks}, indent=2), encoding="utf-8")
    SEED_PATH.write_text(render_seed(catalog, fetched_on), encoding="utf-8")
    print(f"Wrote {SEED_PATH}")
    print(f"Wrote {SNAPSHOT_PATH}")
    for check in checks:
        print(f"  {check['url']} -> {check.get('status') or check.get('http')} hit={check['hit']}")
    if any(c.get("ok") is False for c in checks):
        raise SystemExit("Price needle missing on a live page. Seed was still rewritten from the catalog.")


if __name__ == "__main__":
    main()
