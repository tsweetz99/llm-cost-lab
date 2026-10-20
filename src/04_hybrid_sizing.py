"""Hybrid sizing: commit to the base load, overflow the rest to pay-as-you-go.

Module 02's peak-to-average analysis sizes a commitment to the peak. Both Azure
(spillover, opt-in) and Vertex AI (default) can serve traffic above the
commitment at pay-as-you-go rates, so the better question is how much to commit.

Model (everything normalized by the pay-as-you-go cost of all tokens):
- x(t)  = load at hour t as a multiple of the average load (mean x = 1).
- u     = break-even utilization of one unit (module 02).
- Commit c times the average load, overflow the rest to PAYG:
      cost(c) = u * c + mean(max(x - c, 0))
- Minimizing gives the optimal commitment: the load level exceeded a fraction u
  of the time. If u >= 1 the best commitment is zero.
- Hybrid savings = 1 - cost(c*). It is never negative (c = 0 is always allowed).
- Sized-to-peak savings = 1 - u * max(x); the module 02 result, shown for contrast.

Because everything is linear in volume, savings do not depend on how many tokens
you run, only on the load shape and u. Whole-unit rounding, provider minimum
deployments, and request-level (not token-level) spillover are ignored.

Load shapes are illustrative weekly hourly profiles, not measurements. Pass
--load-csv with a column `tokens` of your own hourly volume to use real data.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # fixed backend so saved charts don't depend on the local default
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, OUTPUTS_DIR

_spec = importlib.util.spec_from_file_location("breakeven", Path(__file__).parent / "02_breakeven.py")
breakeven = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(breakeven)


def weekly_shapes() -> dict[str, np.ndarray]:
    h = np.arange(168)
    hour = h % 24
    weekend = (h // 24) >= 5
    shapes = {
        "flat": np.ones(168),
        "business_hours": np.where(weekend, 0.1, np.where((hour >= 8) & (hour < 18), 1.0, 0.15)),
        "diurnal_24x7": (1 + 0.5 * np.sin(2 * np.pi * (hour - 9) / 24)) * np.where(weekend, 0.7, 1.0),
        "nightly_batch": np.where((hour >= 1) & (hour < 5), 3.0, 0.4),
    }
    return {name: x / x.mean() for name, x in shapes.items()}


def load_custom(path: Path) -> dict[str, np.ndarray]:
    tokens = pd.read_csv(path)["tokens"].to_numpy(dtype=float)
    if len(tokens) < 24 or (tokens < 0).any() or tokens.mean() <= 0:
        raise SystemExit("--load-csv needs a 'tokens' column of at least 24 non-negative hourly values")
    return {"custom": tokens / tokens.mean()}


def optimal_commit(x: np.ndarray, u: float) -> tuple[float, float]:
    candidates = np.concatenate(([0.0], np.unique(x)))
    totals = np.array([u * c + np.maximum(x - c, 0).mean() for c in candidates])
    i = totals.argmin()
    return float(candidates[i]), float(totals[i])


def hybrid_table(table: pd.DataFrame, shapes: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for _, r in table[table["term"].isin(breakeven.SENSITIVITY_TERMS)].iterrows():
        u = r["breakeven_utilization"]
        for name, x in shapes.items():
            c, total = optimal_commit(x, u)
            covered = np.minimum(x, c).mean()
            rows.append(
                {
                    "provider": r["provider"],
                    "model": r["model"],
                    "unit": r["unit"],
                    "term": r["term"],
                    "shape": name,
                    "shape_peak_to_avg": round(x.max(), 2),
                    "breakeven_utilization": u,
                    "optimal_commit_x_avg": round(c, 3),
                    "share_of_tokens_committed": round(covered, 3),
                    "committed_avg_utilization": round(covered / c, 3) if c > 0 else None,
                    "savings_hybrid": round(1 - total, 4),
                    "savings_sized_to_peak": round(1 - u * x.max(), 4),
                }
            )
    return pd.DataFrame(rows)


def plot(result: pd.DataFrame, suffix: str) -> Path:
    yearly = breakeven.longest_term(result)
    shapes = list(dict.fromkeys(yearly["shape"]))
    models = list(dict.fromkeys(yearly["model"]))
    width = 0.8 / len(models)
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
    for ax, col, title in (
        (axes[0], "savings_sized_to_peak", "Sized to peak (no overflow)"),
        (axes[1], "savings_hybrid", "Hybrid: commit base load, overflow to PAYG"),
    ):
        for j, model in enumerate(models):
            vals = np.array(
                [yearly[(yearly["model"] == model) & (yearly["shape"] == s)][col].iloc[0] * 100 for s in shapes]
            )
            xs = np.arange(len(shapes)) + (j - (len(models) - 1) / 2) * width
            ax.bar(xs, np.clip(vals, -100, None), width, label=model)
            for xv, v in zip(xs, vals):
                if v < -100:
                    ax.text(xv, -98, f"{v:.0f}%", ha="center", va="bottom", fontsize=6, rotation=90, color="white")
        ax.axhline(0, color="black", linewidth=1)
        ax.set_xticks(np.arange(len(shapes)))
        ax.set_xticklabels([s.replace("_", "\n") for s in shapes])
        ax.set_title(title, fontsize=10)
        ax.set_ylim(-100, 30)
        ax.grid(True, axis="y", alpha=0.3)
    axes[0].set_ylabel("Longest-term commitment savings vs pay-as-you-go (%)")
    axes[1].legend(loc="lower right", fontsize=8, framealpha=0.92)
    fig.suptitle("Hybrid sizing: how much to commit depends on the load shape")
    fig.tight_layout()
    out = OUTPUTS_DIR / f"hybrid_sizing{suffix}.png"
    fig.savefig(out, dpi=200, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--load-csv", type=Path, help="CSV with an hourly 'tokens' column (your real load)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"No database at {DB_PATH}. Run: python src/01_load_pricing.py")

    shapes = load_custom(args.load_csv) if args.load_csv else weekly_shapes()
    suffix = "_custom" if args.load_csv else ""

    payg, terms, capacity = breakeven.load_frames()
    table = breakeven.break_even_table(payg, terms, capacity)
    result = hybrid_table(table, shapes)

    print("Load shapes (hourly, mean = 1):")
    for name, x in shapes.items():
        print(f"  {name:15s} peak-to-avg {x.max():.2f}")

    yearly = breakeven.longest_term(result)
    print("\nLongest term (1-year, or 3-month for Bedrock): savings vs PAYG, hybrid (sized-to-peak in brackets)")
    cells = yearly.assign(
        cell=lambda d: d.apply(lambda r: f"{r['savings_hybrid']:+.1%} ({r['savings_sized_to_peak']:+.0%})", axis=1)
    )
    print(cells.pivot(index="model", columns="shape", values="cell")[list(shapes)].to_string())

    print("\nLongest term: optimal commitment as a multiple of average load")
    print(yearly.pivot(index="model", columns="shape", values="optimal_commit_x_avg")[list(shapes)].to_string())

    csv_out = OUTPUTS_DIR / f"hybrid_sizing{suffix}.csv"
    result.to_csv(csv_out, index=False)
    chart = plot(result, suffix)
    print(f"\nWrote {chart}")
    print(f"Wrote {csv_out}")


if __name__ == "__main__":
    main()
