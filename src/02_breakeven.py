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

Peak-to-average (P = peak-minute TPM / average TPM): a commitment must be sized
for the peak. If a fractional unit count is sized to be 100% busy at peak, its
average utilization is 1 / P, so
      commitment cost / PAYG cost = P * breakeven_utilization
      max_peak_to_avg             = 1 / breakeven_utilization
A commitment only wins while the workload's P stays below max_peak_to_avg.
This assumes no spillover to PAYG, whole units are not enforced, and minimum
deployment sizes are ignored.

Providers set output_burn close to the model's output/input price ratio, so
break-even utilization is nearly independent of the mix. `burn_matches_price`
flags rows where that stops being true (usually a data-entry error).

This is the FinOps question: at what volume does locking in a PTU or GSU beat
staying on demand -- and how close is that to the unit's ceiling.
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # fixed backend so saved charts don't depend on the local default
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, OUTPUTS_DIR

HOURS_PER_MONTH = 730
INPUT_SHARE = 0.75
OUTPUT_SHARE = 0.25
MINUTES_PER_MONTH = 60 * HOURS_PER_MONTH
BURN_PRICE_TOLERANCE = 0.15
PEAK_SCENARIOS = [1.0, 1.25, 1.5, 2.0, 3.0, 5.0]
SENSITIVITY_TERMS = ["1-month", "3-month", "1-year"]


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
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
                "max_peak_to_avg": round(cap_tokens / be_tokens, 2),
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


PROVIDER_SHORT = {"Azure OpenAI": "Azure", "GCP Vertex AI": "GCP", "AWS Bedrock": "AWS"}
TERM_ORDER = ["hourly", "1-week", "1-month", "3-month", "6-month", "1-year"]
TERM_COLORS = {
    "hourly": "#7f7f7f",
    "1-week": "#bcbd22",
    "1-month": "#ff7f0e",
    "3-month": "#2ca02c",
    "6-month": "#9467bd",
    "1-year": "#1f77b4",
}
PLOT_COLUMNS = 3


def plot(table: pd.DataFrame) -> Path:
    """One panel per commitment unit and model: PAYG line, a fixed-cost line per term, capacity ceiling.

    Each commitment line stops at the unit's real-token capacity, because one unit cannot serve
    more. A dot marks where the commitment starts to beat pay-as-you-go (labeled with the
    utilization it needs). Terms that never pay within one unit are listed in the panel.
    """
    units = table[["provider", "model", "unit"]].drop_duplicates().reset_index(drop=True)
    nrows = -(-len(units) // PLOT_COLUMNS)
    fig, axes = plt.subplots(nrows, PLOT_COLUMNS, figsize=(5.2 * PLOT_COLUMNS, 4.3 * nrows), squeeze=False)
    seen_terms: set[str] = set()

    for ax, (_, u) in zip(axes.flat, units.iterrows()):
        rows = table[(table["provider"] == u["provider"]) & (table["model"] == u["model"])]
        rows = rows.assign(order=rows["term"].map(TERM_ORDER.index)).sort_values("order")
        capacity = rows["unit_capacity_MTok"].iloc[0]
        blended = rows["payg_blended_$/MTok"].iloc[0]
        x_max = capacity * 1.3
        y_max = blended * x_max
        ax.plot([0, x_max], [0, y_max], color="black", linewidth=2)

        never_pays = []
        for _, r in rows.iterrows():
            term, monthly = r["term"], r["monthly_commit_$"]
            if r["breakeven_utilization"] > 1:
                never_pays.append(term)
            if monthly > y_max:
                continue
            seen_terms.add(term)
            color = TERM_COLORS[term]
            ax.hlines(monthly, 0, capacity, color=color, linewidth=2)
            if r["breakeven_utilization"] <= 1:
                ax.scatter([r["breakeven_MTok"]], [monthly], color=color, edgecolor="black", zorder=5)
                ax.annotate(
                    f"{r['breakeven_utilization']:.0%}",
                    xy=(r["breakeven_MTok"], monthly),
                    xytext=(6, -13),
                    textcoords="offset points",
                    fontsize=8,
                    bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.9},
                    zorder=6,
                )

        ax.axvline(capacity, color="firebrick", linestyle="--", linewidth=1.2)
        ax.set_xlim(0, x_max)
        ax.set_ylim(0, y_max)
        ax.set_title(
            f"{PROVIDER_SHORT.get(u['provider'], u['provider'])} {u['unit']} \u00b7 {u['model']}",
            fontsize=10,
            pad=14,
        )
        ax.text(
            0.02, 0.97, f"1 {u['unit']} ceiling: {capacity:,.0f}M tok/mo",
            transform=ax.transAxes, va="top", fontsize=7.5, color="firebrick",
        )
        if never_pays:
            ax.text(
                0.5, 1.012, "never pays on one unit: " + ", ".join(never_pays),
                transform=ax.transAxes, ha="center", va="bottom", fontsize=7.5, color="0.35",
            )
        ax.set_xlabel("Monthly tokens (millions)", fontsize=8)
        ax.grid(True, alpha=0.3)
    for ax in axes[:, 0]:
        ax.set_ylabel("Monthly cost (USD)", fontsize=8)
    for ax in axes.flat[len(units):]:
        ax.set_visible(False)

    handles = [Line2D([0], [0], color="black", linewidth=2, label="Pay-as-you-go")]
    handles += [
        Line2D([0], [0], color=TERM_COLORS[t], linewidth=2, label=f"{t} commitment")
        for t in TERM_ORDER
        if t in seen_terms
    ]
    handles += [
        Line2D([0], [0], color="firebrick", linestyle="--", linewidth=1.2, label="one-unit capacity"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="0.6", markeredgecolor="black",
               label="break-even (label = utilization needed)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle("Break-even: one committed unit vs pay-as-you-go  \u00b7  75% input / 25% output", fontsize=12)
    fig.tight_layout(rect=(0, 0.08, 1, 0.97))

    out = OUTPUTS_DIR / "breakeven_analysis.png"
    fig.savefig(out, dpi=200, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return out


def peak_sensitivity(table: pd.DataFrame) -> pd.DataFrame:
    """Commitment savings vs PAYG at each peak-to-average scenario.

    savings = 1 - P * breakeven_utilization; negative means the commitment
    costs more than staying on demand.
    """
    out = table[["provider", "model", "unit", "term", "max_peak_to_avg"]].copy()
    for p in PEAK_SCENARIOS:
        out[f"P={p:g}"] = (1 - p * table["breakeven_utilization"]).round(3)
    return out


def plot_peak(table: pd.DataFrame) -> Path:
    fig, ax = plt.subplots(figsize=(10, 6))
    p = np.linspace(1.0, 2.0, 200)
    yearly = table[table["term"] == "1-year"]
    for _, r in yearly.iterrows():
        savings = (1 - p * r["breakeven_utilization"]) * 100
        (line,) = ax.plot(p, savings, linewidth=2, label=f"{r['provider']} {r['model']}")
        if r["max_peak_to_avg"] > 1.0:
            ax.scatter([r["max_peak_to_avg"]], [0], color=line.get_color(), zorder=5)
            ax.annotate(
                f"{r['max_peak_to_avg']:.2f}",
                xy=(r["max_peak_to_avg"], 0),
                xytext=(0, -14),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=line.get_color(),
            )

    ax.axhline(0, color="black", linewidth=1)
    ax.set_ylim(-60, 30)
    ax.set_xlim(1.0, 2.0)
    ax.set_title("1-year commitment savings vs pay-as-you-go, by peak-to-average ratio")
    ax.set_xlabel("Peak-to-average ratio (peak-minute TPM ÷ average TPM)")
    ax.set_ylabel("Savings vs pay-as-you-go (%)  ·  below 0 = commitment costs more")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", fontsize=8, framealpha=0.92)
    fig.tight_layout()

    out = OUTPUTS_DIR / "peak_to_average.png"
    fig.savefig(out, dpi=200, bbox_inches="tight", metadata={"Software": None})
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
    print("  max_peak_to_avg        highest peak-to-average ratio at which the commitment still")
    print("                         beats PAYG (1 / breakeven_utilization; below 1.0 = never)")
    print("  burn_matches_price     output_burn is within "
          f"{BURN_PRICE_TOLERANCE:.0%} of the output/input price ratio")

    mismatched = table[~table["burn_matches_price"]]
    if not mismatched.empty:
        print("\nCheck these rows: output_burn disagrees with the price ratio")
        print(mismatched[["provider", "model", "output_burn", "price_ratio"]].drop_duplicates().to_string(index=False))

    sensitivity = peak_sensitivity(table)
    shown = sensitivity[sensitivity["term"].isin(SENSITIVITY_TERMS)].copy()
    for col in shown.columns[5:]:
        shown[col] = shown[col].map(lambda v: f"{v:+.0%}")
    print("\nCommitment savings vs PAYG by peak-to-average ratio (negative = costs more)")
    print(shown.to_string(index=False))

    out = plot(table)
    peak_out = plot_peak(table)
    csv_out = OUTPUTS_DIR / "breakeven_table.csv"
    sens_out = OUTPUTS_DIR / "peak_sensitivity.csv"
    table.to_csv(csv_out, index=False)
    sensitivity.to_csv(sens_out, index=False)
    print(f"\nWrote {out}")
    print(f"Wrote {peak_out}")
    print(f"Wrote {csv_out}")
    print(f"Wrote {sens_out}")


if __name__ == "__main__":
    main()
