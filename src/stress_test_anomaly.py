"""Stress test for the Module 03 anomaly detector.

Regenerates the synthetic usage with one planted GPT-4o incident moved to
different days and magnitudes, across many random seeds, and scores two
detector variants:
  day-class  baseline from earlier weekdays / weekend days separately (default)
  mixed      legacy trailing 14-day window that mixes weekdays and weekends

"Detect rate" is the share of seeds in which the planted day is flagged.
"False flags/run" is the average number of other model-days flagged per
90-day x 8-model run. Detection depends on the synthetic noise level (12%
lognormal) and on weekend volume being 35% of weekday volume; the numbers say
how this detector behaves on this synthetic data, not on production traffic.

Usage: python src/stress_test_anomaly.py [--seeds 100]
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, OUTPUTS_DIR

_spec = importlib.util.spec_from_file_location("anomaly", Path(__file__).parent / "03_anomaly.py")
anomaly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anomaly)

TARGET_MODEL = "GPT-4o"
# (day index, multiplier). Day 36 is a Thursday, 38 a Saturday, 39 a Sunday.
INCIDENTS = [(36, 6.0), (36, 1.9), (38, 6.0), (38, 3.0), (39, 3.0), (38, 2.0)]


def load_reference() -> pd.DataFrame:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return pd.read_sql_query(
            """
            SELECT m.model_id, m.model_name, od.input_per_mtok, od.output_per_mtok
            FROM models m JOIN on_demand_pricing od ON od.model_id = m.model_id
            ORDER BY m.model_id
            """,
            conn,
        )


def usage_frame(reference: pd.DataFrame, seed: int, planted: dict) -> pd.DataFrame:
    rows = anomaly.generate_usage(reference[["model_id", "model_name"]], np.random.default_rng(seed), planted)
    df = pd.DataFrame(rows, columns=["model_id", "usage_date", "input_tokens", "output_tokens"])
    df = df.merge(reference, on="model_id")
    df["usage_date"] = pd.to_datetime(df["usage_date"])
    df["spend_usd"] = (
        df["input_tokens"] / 1e6 * df["input_per_mtok"] + df["output_tokens"] / 1e6 * df["output_per_mtok"]
    )
    return df


def scenario_name(day: int, mult: float) -> str:
    weekday = (anomaly.START + pd.Timedelta(days=day)).day_name()[:3]
    return f"{weekday} {mult:g}x"


def score(reference: pd.DataFrame, seeds: int) -> pd.DataFrame:
    scenarios = [("no incident", {})] + [
        (scenario_name(d, m), {(d, TARGET_MODEL): m}) for d, m in INCIDENTS
    ]
    rows = []
    for name, planted in scenarios:
        target = {(anomaly.START + pd.Timedelta(days=d), m) for d, m in planted}
        for detector, seasonal in (("day-class", True), ("mixed", False)):
            hits = false_flags = 0
            for seed in range(seeds):
                flagged_df = anomaly.detect(usage_frame(reference, seed, planted), seasonal=seasonal)
                flagged = {
                    (r.usage_date, r.model_name) for r in flagged_df[flagged_df["is_anomaly"]].itertuples()
                }
                hits += int(bool(target) and target <= flagged)
                false_flags += len(flagged - target)
            rows.append(
                {
                    "scenario": name,
                    "detector": detector,
                    "detect_rate": round(hits / seeds, 3) if target else None,
                    "false_flags_per_run": round(false_flags / seeds, 3),
                    "seeds": seeds,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=100)
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"No database at {DB_PATH}. Run: python src/01_load_pricing.py")

    result = score(load_reference(), args.seeds)
    show = result.assign(
        detect=lambda d: d["detect_rate"].map(lambda v: "-" if pd.isna(v) else f"{v:.0%}"),
    ).pivot(index="scenario", columns="detector", values=["detect", "false_flags_per_run"])
    order = ["no incident"] + [scenario_name(d, m) for d, m in INCIDENTS]

    print(f"Planted-incident stress test: {args.seeds} seeds, incident on {TARGET_MODEL}\n")
    print(f"{'scenario':14s} {'day-class':>10s} {'mixed':>8s}   (detect rate)")
    for s in order:
        print(f"{s:14s} {show.loc[s, ('detect', 'day-class')]:>10s} {show.loc[s, ('detect', 'mixed')]:>8s}")
    fp = result.groupby("detector")["false_flags_per_run"].mean()
    print(f"\nFalse flags per 90-day x 8-model run (mean over scenarios): "
          f"day-class {fp['day-class']:.3f}, mixed {fp['mixed']:.3f}")

    out = OUTPUTS_DIR / "anomaly_stress_test.csv"
    result.to_csv(out, index=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
