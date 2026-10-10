"""Break-even: committed capacity vs pay-as-you-go tokens.

Assumptions (documented so the chart is auditable):
- Hours per month = 730 (24 * 365 / 12).
- Workload mix = 75% input / 25% output tokens (same blend as queries.py).
- PAYG cost is linear in monthly tokens at the model's blended $/MTok.
- Committed cost is a fixed monthly bill = hourly_rate * 730, independent of
  tokens until you exceed the unit's capacity.
- Unit capacity is published in burndown (input-equivalent) tokens per minute,
  and one output token burns `output_burn` of them. For a mix with output share s:
      real tokens/min = burndown_tpm / ((1 - s) + s * output_burn)
- Monthly real-token capacity of one unit = real tokens/min * 60 * 730.
- Break-even tokens = committed_monthly / blended_rate_per_token.
- Break-even utilization = break-even tokens / monthly real-token capacity.
- Tiers above 200k context, prompt caching and non-text modalities are ignored.

Providers set output_burn close to the model's output/input price ratio, so
break-even utilization is nearly independent of the mix. `burn_matches_price`
flags rows where that stops being true (usually a data-entry error).

This is the FinOps question: at what volume does locking in a PTU or GSU beat
staying on demand -- and how close is that to the unit's ceiling.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, OUTPUTS_DIR

HOURS_PER_MONTH = 730
INPUT_SHARE = 0.75
OUTPUT_SHARE = 0.25
MINUTES_PER_MONTH = 60 * HOURS_PER_MONTH
BURN_PRICE_TOLERANCE = 0.15


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(DB_PATH) as conn:
        payg = pd.read_sql_query(
            """
            SELECT
                p.name AS provider,
                m.model_id,
                m.model_name,
                od.input_per_mtok,
                od.output_per_mtok
            FROM on_demand_pricing od
            JOIN models m ON od.model_id = m.model_id
            JOIN providers p ON m.provider_id = p.provider_id
            """,
            conn,
        )
        terms = pd.read_sql_query(
            """
            SELECT
                p.name AS provider,
                t.provider_id,
                t.unit_name,
                t.term,
                t.hourly_rate
            FROM commitment_terms t
            JOIN providers p ON t.provider_id = p.provider_id
            """,
            conn,
        )
        capacity = pd.read_sql_query(
            """
            SELECT
                c.provider_id,
                c.unit_name,
                c.model_id,
                c.burndown_tpm,
                c.output_burn
            FROM unit_capacity c
            """,
            conn,
        )
    payg["blended_per_mtok"] = (
        payg["input_per_mtok"] * INPUT_SHARE + payg["output_per_mtok"] * OUTPUT_SHARE
    )
    return payg, terms, capacity


def break_even_table(
    payg: pd.DataFrame, terms: pd.DataFrame, capacity: pd.DataFrame
) -> pd.DataFrame:
    units = capacity.merge(terms, on=["provider_id", "unit_name"]).merge(
        payg, on=["model_id", "provider"]
    )
    rows = []
    for _, u in units.iterrows():
        monthly_commit = u["hourly_rate"] * HOURS_PER_MONTH
        blended = u["blended_per_mtok"]
        be_tokens = monthly_commit / blended * 1_000_000

        burn_per_token = INPUT_SHARE + OUTPUT_SHARE * u["output_burn"]
        real_tpm = u["burndown_tpm"] / burn_per_token
        cap_tokens = real_tpm * MINUTES_PER_MONTH

        price_ratio = u["output_per_mtok"] / u["input_per_mtok"]
        burn_ratio = u["output_burn"] / price_ratio
        rows.append(
            {
                "provider": u["provider"],
                "model": u["model_name"],
                "unit": u["unit_name"],
                "term": u["term"],
                "hourly_rate": u["hourly_rate"],
                "monthly_commit_$": round(monthly_commit, 2),
                "payg_blended_$/MTok": round(blended, 4),
                "breakeven_MTok": round(be_tokens / 1_000_000, 3),
                "unit_capacity_MTok": round(cap_tokens / 1_000_000, 3),
                "breakeven_utilization": round(be_tokens / cap_tokens, 3),
                "output_burn": u["output_burn"],
                "price_ratio": round(price_ratio, 2),
                "burn_matches_price": abs(burn_ratio - 1) <= BURN_PRICE_TOLERANCE,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(["provider", "model", "hourly_rate"], ascending=[True, True, False])
        .reset_index(drop=True)
    )


def plot(payg: pd.DataFrame, table: pd.DataFrame) -> Path:
    tokens = np.linspace(0, 120_000_000, 400)
    fig, ax = plt.subplots(figsize=(12, 7))

    for _, row in payg.iterrows():
        cost = tokens * row["blended_per_mtok"] / 1_000_000
        style = "-" if row["model_name"] == "GPT-4o" else "--"
        width = 2.4 if row["model_name"] == "GPT-4o" else 1.2
        alpha = 1.0 if row["model_name"] == "GPT-4o" else 0.45
        ax.plot(
            tokens / 1_000_000,
            cost,
            style,
            linewidth=width,
            alpha=alpha,
            label=f"PAYG {row['provider']} {row['model_name']}",
        )

    term_styles = {"hourly": ":", "1-month": "--", "1-year": "-.", "6-month": "--"}
    gpt4o = table[table["model"] == "GPT-4o"]
    for _, r in gpt4o.iterrows():
        ax.axhline(
            r["monthly_commit_$"],
            linestyle=term_styles.get(r["term"], "--"),
            linewidth=2,
            label=f"PTU {r['term']}  ${r['monthly_commit_$']:,.0f}/mo",
        )

    capacity_mtok = gpt4o["unit_capacity_MTok"].iloc[0]
    ax.axvline(capacity_mtok, color="firebrick", linewidth=1.4, alpha=0.8)
    ax.text(
        capacity_mtok,
        0.86,
        f"1 PTU ceiling\n{capacity_mtok:.0f}M tok/mo ",
        transform=ax.get_xaxis_transform(),
        ha="right",
        fontsize=8,
        color="firebrick",
    )

    for _, r in gpt4o.iterrows():
        ax.axvline(r["breakeven_MTok"], color="0.6", linewidth=0.8, alpha=0.6)
        ax.scatter([r["breakeven_MTok"]], [r["monthly_commit_$"]], zorder=5)
        ax.annotate(
            f"{r['term']}\n{r['breakeven_MTok']:.1f}M tok\n{r['breakeven_utilization']:.0%} util",
            xy=(r["breakeven_MTok"], r["monthly_commit_$"]),
            xytext=(6, 8),
            textcoords="offset points",
            fontsize=8,
        )

    ax.set_title("GPT-4o break-even: Azure PTU commitment vs pay-as-you-go")
    ax.set_xlabel("Monthly tokens (millions)  ·  75% input / 25% output")
    ax.set_ylabel("Monthly cost (USD)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.92)
    fig.tight_layout()

    out = OUTPUTS_DIR / "breakeven_analysis.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"No database at {DB_PATH}. Run: python src/01_load_pricing.py")

    payg, terms, capacity = load_frames()
    table = break_even_table(payg, terms, capacity)
    print("PAYG models:", len(payg))
    print("Commitment terms:", len(terms))
    print("Unit capacity rows:", len(capacity))
    print()
    print(table.to_string(index=False))
    print()
    print("How to read this")
    print("  monthly_commit_$       fixed bill if you buy one unit for that term")
    print("  breakeven_MTok         PAYG tokens where PAYG cost = that fixed bill")
    print("  unit_capacity_MTok     real tokens one unit can serve per month at this mix")
    print("  breakeven_utilization  break-even tokens as a % of that capacity")
    print("  If utilization at break-even is > 100%, one unit cannot pay for itself:")
    print("  you would hit the capacity cap before PAYG became more expensive.")
    print("  burn_matches_price     output_burn is within "
          f"{BURN_PRICE_TOLERANCE:.0%} of the output/input price ratio")

    mismatched = table[~table["burn_matches_price"]]
    if not mismatched.empty:
        print("\nCheck these rows: output_burn disagrees with the price ratio")
        print(mismatched[["provider", "model", "output_burn", "price_ratio"]].drop_duplicates().to_string(index=False))

    out = plot(payg, table)
    csv_out = OUTPUTS_DIR / "breakeven_table.csv"
    table.to_csv(csv_out, index=False)
    print(f"\nWrote {out}")
    print(f"Wrote {csv_out}")


if __name__ == "__main__":
    main()
