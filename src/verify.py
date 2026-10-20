"""Check catalog prices against the provider pages they came from.

Each catalog row that has a `verify` spec is looked up on its live source and compared with the
price the catalog states. Three source types, because the pages publish prices three different ways:

- azure_row   Azure's pricing page ships each price as a JSON `data-amount` attribute on a span
              whose visible text is a "$-" placeholder. We read the JSON for one region.
- gcp_rows    Google's pricing page has the prices in its HTML tables. We match an input row and
              the output row that follows it.
- aws_bedrock AWS's pricing page holds `{priceOf!...}` templates and fills them in client-side
              from a public price-list JSON. We resolve the same templates against that JSON.

Nothing here writes files; the caller supplies a `fetch(url)` and decides what a failure means.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from decimal import Decimal
from typing import Callable

AWS_REGION = "US East (N. Virginia)"
AZURE_REGION = "us-east"
AWS_DATA_URL = (
    "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/"
    "bedrockfoundationmodels.json"
)
AWS_PAGE_URL = "https://aws.amazon.com/bedrock/pricing/"

Fetch = Callable[..., "tuple[int, str, bool]"]


def money_re(value: float) -> str:
    """Regex for a dollar amount that tolerates trailing zeros ($2.5, $2.50, $10, $10.00)."""
    text = format(Decimal(str(value)).normalize(), "f")
    if "." in text:
        whole, frac = text.split(".")
        return rf"\${re.escape(whole)}\.{frac}0*(?!\d|\.\d)"
    return rf"\${re.escape(text)}(?:\.0+)?(?!\d|\.\d)"


def _azure_price(match: re.Match) -> str:
    regional = json.loads(match.group(1)).get("regional", {})
    value = regional.get(AZURE_REGION)
    if value is None and regional:
        value = next(iter(regional.values()))
    return "$-" if value is None else f"${Decimal(str(value)).normalize():f}"


def page_text(raw: str) -> str:
    """Flatten HTML to single-spaced text with table cells joined by ' | ' and rows by ' || '."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    text = re.sub(
        r"<span\s+class='price-data[^']*'\s+data-amount='(\{.*?\})'[^>]*>\s*<span\s+class='price-value'>[^<]*</span>\s*</span>",
        _azure_price,
        text,
    )
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"(?i)</tr>", " || ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def verify_azure_row(text: str, spec: dict, price_in: float, price_out: float) -> bool:
    pattern = (
        re.escape(spec["anchor"])
        + r" \| Input: "
        + money_re(price_in)
        + r"[^|]*?Output: "
        + money_re(price_out)
    )
    return re.search(pattern, text) is not None


def verify_gcp_rows(text: str, spec: dict, price_in: float, price_out: float) -> bool:
    pattern = (
        re.escape(spec["input_anchor"])
        + r"\s*"
        + money_re(price_in)
        + r"\s*\|.{0,400}?"
        + re.escape(spec["output_anchor"])
        + r"\s*"
        + money_re(price_out)
        + r"\s*\|"
    )
    return re.search(pattern, text) is not None


def _clean(cell: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip().rstrip("*").strip()


def aws_tables(page_html: str, data: dict) -> list[dict]:
    """Parse the Anthropic tables out of the AWS page and resolve each price template."""
    prices = data["regions"][AWS_REGION]

    def resolve(cell: str):
        template = re.search(r"\{priceOf!([^}]+)\}", cell)
        if not template:
            return None
        body = template.group(1)
        key = (body.split("!")[1] if "!" in body else body).replace("!opt", "")
        entry = prices.get(key)
        return float(entry["price"]) if entry else None

    page = htmllib.unescape(page_html)
    tables = []
    for match in re.finditer(r"<table>.*?</table>", page, re.S):
        table = match.group(0)
        heads = [_clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
        if not heads or "Anthropic models" not in heads[0]:
            continue
        headings = re.findall(r"<h[23]>(.*?)</h[23]>", page[max(0, match.start() - 4000): match.start()], re.S)
        rows = {}
        for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if cells:
                rows[_clean(cells[0])] = dict(zip(heads[1:], (resolve(c) for c in cells[1:])))
        tables.append({"section": _clean(headings[-1]) if headings else "", "rows": rows})
    return tables


def aws_lookup(tables: list[dict], section: str, model: str, columns: list[str]):
    """Return the resolved values for `columns` of `model` in the table under `section`, or None."""
    for table in tables:
        row = table["rows"].get(model)
        if table["section"] == section and row is not None and all(c in row for c in columns):
            return [row[c] for c in columns]
    return None


def _close(a, b) -> bool:
    return a is not None and abs(a - b) < 1e-9


class _AwsSource:
    """Fetch the AWS pricing page and its price-list file once; a failure is remembered, not retried."""

    def __init__(self, fetch: Fetch):
        self.fetch, self.tables, self.error = fetch, None, None

    def get(self) -> list[dict]:
        if self.error is not None:
            raise self.error
        if self.tables is None:
            try:
                _, page, _ = self.fetch(AWS_PAGE_URL)
                _, data, _ = self.fetch(AWS_DATA_URL, referer=AWS_PAGE_URL)
                self.tables = aws_tables(page, json.loads(data))
            except (OSError, KeyError, ValueError) as exc:
                self.error = exc
                raise
        return self.tables


def _finish(result: dict, check) -> dict:
    """Run `check()` (returns ok, truncated); turn fetch and parse failures into an 'error' entry."""
    try:
        result["ok"], truncated = check()
        if truncated and not result["ok"]:
            result["truncated"] = True
    except OSError as exc:
        result["error"] = str(exc)
    except (KeyError, ValueError) as exc:
        result["error"] = f"could not parse source: {exc!r}"
    return result


def verify_on_demand(catalog: dict, fetch: Fetch, aws: _AwsSource | None = None) -> list[dict]:
    """Verify every on-demand row that has a `verify` spec. One dict per row, never raises."""
    aws = aws or _AwsSource(fetch)
    texts: dict[str, tuple[str, bool]] = {}
    results = []
    for row in catalog["on_demand"]:
        spec = row.get("verify")
        if spec is None:
            continue
        price_in, price_out = row["input_per_mtok"], row["output_per_mtok"]
        result = {"row": f"{row['provider']} {row['model']}", "kind": spec["kind"], "url": spec["url"],
                  "expected": [price_in, price_out], "ok": False}

        def check(spec=spec, price_in=price_in, price_out=price_out, result=result):
            if spec["kind"] == "aws_bedrock":
                found = aws_lookup(aws.get(), spec["section"], spec["model"],
                                   ["Price per 1M input tokens", "Price per 1M output tokens"])
                result["found"] = found
                return found is not None and _close(found[0], price_in) and _close(found[1], price_out), False
            if spec["url"] not in texts:
                _, body, truncated = fetch(spec["url"])
                texts[spec["url"]] = (page_text(body), truncated)
            text, truncated = texts[spec["url"]]
            matcher = verify_azure_row if spec["kind"] == "azure_row" else verify_gcp_rows
            return matcher(text, spec, price_in, price_out), truncated

        results.append(_finish(result, check))
    return results


def verify_reserved(catalog: dict, fetch: Fetch, aws: _AwsSource | None = None) -> list[dict]:
    """Verify AWS Reserved-tier term rows: hourly price per 1K input TPM and per 1K output TPM."""
    aws = aws or _AwsSource(fetch)
    results = []
    for row in catalog["commitment_terms"]:
        spec = row.get("verify")
        if spec is None:
            continue
        expected = [row["hourly_rate"], row["output_hourly_rate"]]
        result = {"row": f"{row['provider']} {row['model']} {row['term']}", "kind": spec["kind"],
                  "url": spec["url"], "expected": expected, "ok": False}

        def check(spec=spec, expected=expected, result=result):
            columns = [f"Price per hour per 1K {side} TPM with {spec['months']}-Month Commitment" for side in ("Input", "Output")]
            found = aws_lookup(aws.get(), spec["section"], spec["model"], columns)
            result["found"] = found
            return found is not None and _close(found[0], expected[0]) and _close(found[1], expected[1]), False

        results.append(_finish(result, check))
    return results


def verify_catalog(catalog: dict, fetch: Fetch) -> list[dict]:
    """Every check the catalog asks for, sharing one AWS fetch."""
    aws = _AwsSource(fetch)
    return verify_on_demand(catalog, fetch, aws) + verify_reserved(catalog, fetch, aws)
