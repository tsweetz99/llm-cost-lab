"""Refresh the pricing seed from data/price_catalog.json and live provider pages.

Two kinds of live check run before anything is written:
- Commitment terms with `needles`: the page must still contain at least one of the
  published price strings.
- Any row with a `verify` spec (the on-demand rates): the price the catalog states
  must match what the provider's page publishes. See verify.py for how each source
  is read.

--offline rewrites the seed without fetching. --check makes no network calls and
writes nothing: it exits non-zero if the committed seed is not exactly what the
catalog renders to (for example after a hand edit to the seed).

The seed is only rewritten, and its date only restamped, when every live check
passes, so the date means every price in it was checked. A successful live run also
sets the catalog's fetched_on to that date, so a later --offline run reproduces the
same seed instead of reverting its dates. An unreachable source, an oversized page,
and a price that is missing or different are reported as separate failures, and none
of them touches the seed.
"""
from __future__ import annotations

import argparse
import gzip
import json
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from catalog import REFRESH_HINT, load_catalog, render_seed, seed_drift, set_fetched_on
from config import CATALOG_PATH, DATA_DIR, SEED_PATH
from verify import verify_on_demand as verify_catalog

SNAPSHOT_PATH = DATA_DIR / "price_snapshot.json"
# The Vertex AI pricing page is ~3.6 MB of HTML with its prices ~2.2 MB in, so a small cap
# silently cut them off. Truncation is reported instead of being treated as "price missing".
MAX_PAGE_BYTES = 12_000_000


def tls_context() -> ssl.SSLContext:
    # python.org builds of Python on macOS ship without a CA bundle, so verification fails
    # unless certifi's is used. Fall back to the system store when certifi is not installed.
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch(url: str, referer: str | None = None) -> tuple[int, str, bool]:
    """Return (HTTP status, page text, truncated) where truncated means the page exceeded MAX_PAGE_BYTES."""
    headers = {"User-Agent": "llm-cost-lab/0.1"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60, context=tls_context()) as resp:
        raw = resp.read(MAX_PAGE_BYTES + 1)
        status = resp.status
    if raw[:2] == b"\x1f\x8b":  # some static hosts gzip regardless of Accept-Encoding
        raw = gzip.decompress(raw)
    return status, raw[:MAX_PAGE_BYTES].decode("utf-8", errors="replace"), len(raw) > MAX_PAGE_BYTES


def check_pages(catalog: dict, offline: bool) -> list[dict]:
    results = []
    needle_rows = [row for row in catalog["commitment_terms"] if row.get("needles")]
    urls = sorted({row["source_url"] for row in needle_rows})
    for url in urls:
        needles = sorted({n for row in needle_rows if row["source_url"] == url for n in row["needles"]})
        if offline:
            results.append({"url": url, "status": "skipped", "needles": needles, "hit": []})
            continue
        try:
            status, body, truncated = fetch(url)
        except OSError as exc:  # URLError, HTTPError, TLS failures, timeouts and resets are all OSErrors
            results.append(
                {"url": url, "status": f"error: {exc}", "error": str(exc), "needles": needles, "hit": [], "ok": False}
            )
            continue
        hit = [n for n in needles if n in body]
        result = {"url": url, "http": status, "needles": needles, "hit": hit, "ok": bool(hit)}
        if truncated:
            result["truncated"] = True
        results.append(result)
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
    verified = [] if args.offline else verify_catalog(catalog, fetch)
    SNAPSHOT_PATH.write_text(
        json.dumps({"fetched_on": fetched_on, "offline": args.offline, "checks": checks, "verification": verified}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {SNAPSHOT_PATH}")
    for check in checks:
        print(f"  {check['url']} -> {check.get('status') or check.get('http')} hit={check['hit']}")
    for item in verified:
        print(f"  {item['row']} -> {'verified' if item['ok'] else 'NOT verified'} {item['expected']}")

    unreachable = [c for c in checks + verified if c.get("error")]
    inconclusive = [c for c in checks + verified if c.get("ok") is False and c.get("truncated")]
    missing = [c for c in checks if c.get("ok") is False and not c.get("error") and not c.get("truncated")]
    different = [c for c in verified if c.get("ok") is False and not c.get("error") and not c.get("truncated")]
    problems = []
    if different:
        details = "; ".join(
            f"{c['row']}: catalog {c['expected']}, "
            + (f"live source says {c['found']}" if c.get("found") is not None else "not found on the page")
            for c in different
        )
        problems.append(f"Catalog price differs from the live source for {len(different)} row(s): {details}.")
    if inconclusive:
        problems.append(
            f"{len(inconclusive)} page(s) exceeded the {MAX_PAGE_BYTES:,}-byte read limit before a price was found, "
            "so the check is inconclusive, not a missing price: "
            + ", ".join(c["url"] for c in inconclusive)
            + ". Raise MAX_PAGE_BYTES in src/00_refresh_prices.py."
        )
    if unreachable:
        problems.append(
            f"Could not fetch or read {len(unreachable)} source(s): {unreachable[0]['error']}. "
            "That is a network or TLS problem, not a price change. "
            "Fix connectivity, or run with --offline to skip the live checks."
        )
    if missing:
        problems.append(
            f"No published price found on {len(missing)} page(s): "
            + ", ".join(c["url"] for c in missing)
            + ". Check the catalog against those pages."
        )
    if problems:
        raise SystemExit("\n".join(problems) + f"\n{SEED_PATH.name} was NOT rewritten.")

    if not args.offline and set_fetched_on(fetched_on):
        print(f"Set fetched_on to {fetched_on} in {CATALOG_PATH.name}")
    SEED_PATH.write_text(render_seed(catalog, fetched_on), encoding="utf-8")
    print(f"Wrote {SEED_PATH}")


if __name__ == "__main__":
    main()
