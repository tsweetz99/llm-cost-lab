"""Break-even: committed capacity vs pay-as-you-go tokens.

Assumptions (documented so the chart is auditable):
- Hours per month = 730 (24 * 365 / 12).
- Workload mix = 75% input / 25% output tokens (same blend as queries.py).
- PAYG cost is linear in monthly tokens at the model's blended $/MTok.
- Committed cost is a fixed monthly bill = hourly_rate * 730, independent of
  tokens until you exceed the unit's TPM capacity.
- Monthly token capacity of one unit = tpm_capacity * 60 * 730.
- Break-even tokens = committed_monthly / blended_rate_per_token.
- Break-even utilization = break-even tokens / monthly capacity.

This is the FinOps question: at what volume does locking in a PTU (or model
unit) beat staying on demand — and how close is that to the unit's ceiling.
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


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
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
        commit = pd.read_sql_query(
            """
            SELECT
                p.name AS provider,
                m.model_name,
                c.model_id,
                c.unit_name,
                c.hourly_rate,
                c.term,
                c.tpm_capacity,
                c.throughput_note
            FROM committed_pricing c
            JOIN providers p ON c.provider_id = p.provider_id
            LEFT JOIN models m ON c.model_id = m.model_id
            """,
            conn,
        )
    payg["blended_per_mtok"] = (
        payg["input_per_mtok"] * INPUT_SHARE + payg["output_per_mtok"] * OUTPUT_SHARE
    )
    return payg, commit


def break_even_table(payg: pd.DataFrame, commit: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, c in commit.iterrows():
        match = payg[payg["model_id"] == c["model_id"]]
        if match.empty:
            continue
        p = match.iloc[0]
        monthly_commit = c["hourly_rate"] * HOURS_PER_MONTH
        blended = p["blended_per_mtok"]
        be_tokens = (monthly_commit / blended) * 1_000_000 if blended else np.nan
        capacity = (
            c["tpm_capacity"] * MINUTES_PER_MONTH if pd.notna(c["tpm_capacity"]) else np.nan
        )
        util = be_tokens / capacity if capacity else np.nan
        rows.append(
            {
                "provider": c["provider"],
                "model": c["model_name"],
                "unit": c["unit_name"],
                "term": c["term"],
                "hourly_rate": c["hourly_rate"],
                "monthly_commit_$": round(monthly_commit, 2),
                "payg_blended_$/MTok": round(blended, 4),
                "breakeven_MTok": round(be_tokens / 1_000_000, 3),
                "unit_capacity_MTok": round(capacity / 1_000_000, 3) if capacity == capacity else None,
                "breakeven_utilization": round(util, 3) if util == util else None,
            }
        )
    return pd.DataFrame(rows)


def plot(payg: pd.DataFrame, commit: pd.DataFrame, table: pd.DataFrame) -> Path:
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
    azure_ptu = commit[(commit["provider"] == "Azure OpenAI") & (commit["model_name"] == "GPT-4o")]
    for _, c in azure_ptu.iterrows():
        monthly = c["hourly_rate"] * HOURS_PER_MONTH
        ax.axhline(
            monthly,
            linestyle=term_styles.get(c["term"], "--"),
            linewidth=2,
            label=f"PTU {c['term']}  ${monthly:,.0f}/mo",
        )

    gpt4o = table[table["model"] == "GPT-4o"]
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
    ax.set_xlabel("Monthly tokens (millions)  \u00b7  75% input / 25% output")
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

    payg, commit = load_frames()
    table = break_even_table(payg, commit)
    print("PAYG models:", len(payg))
    print("Committed rows:", len(commit))
    print()
    print(table.to_string(index=False))
    print()
    print("How to read this")
    print("  monthly_commit_$     fixed bill if you buy one unit for that term")
    print("  breakeven_MTok       PAYG tokens where PAYG cost = that fixed bill")
    print("  breakeven_utilization  those tokens as a % of the unit's TPM ceiling")
    print("  If utilization at break-even is > 100%, one unit cannot pay for itself:")
    print("  you would hit the TPM cap before PAYG became more expensive.")

    out = plot(payg, commit, table)
    csv_out = OUTPUTS_DIR / "breakeven_table.csv"
    table.to_csv(csv_out, index=False)
    print(f"\nWrote {out}")
    print(f"Wrote {csv_out}")


if __name__ == "__main__":
    main()
