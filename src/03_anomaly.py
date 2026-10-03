"""Cost anomaly detection on daily LLM spend.

Generates a reproducible 90-day usage series (weekday seasonality + mild
trend + two planted incidents), prices it from the on-demand table, then
flags days that break a trailing baseline.

Detector
--------
For each model, on each day t:
  baseline = median of the previous 14 same-weekday-excluded rolling window
             of daily spend (simple trailing 14-day median)
  mad      = median absolute deviation of that window
  robust_z = 0.6745 * (spend_t - baseline) / mad

A day is flagged if (robust_z >= 3.5 AND spend >= 1.75x baseline)
  OR spend >= 2.5x baseline.
Weekends are allowed to be quiet; the detector is one-sided (spikes only).
The AND on the z-score gate stops a tight MAD from flagging ordinary noise.

Planted incidents (so the lab is inspectable):
  day 36  GPT-4o     6x weekday volume  — runaway agent loop
  day 61  GPT-4o-mini 4x weekday volume — eval job left on overnight
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

RNG = np.random.default_rng(20260710)
WINDOW = 14
Z_THRESH = 3.5
RATIO_FLOOR = 1.75
RATIO_THRESH = 2.5
START = pd.Timestamp("2026-04-01")
DAYS = 90


def seed_usage(conn: sqlite3.Connection) -> None:
    models = pd.read_sql_query(
        "SELECT model_id, model_name FROM models",
        conn,
    )
    weekday_input = {
        "GPT-4o": 2_400_000,
        "GPT-5": 800_000,
        "GPT-4o-mini": 6_000_000,
        "Claude Sonnet 5": 1_200_000,
        "Claude Sonnet 4.6": 900_000,
        "Claude Haiku 4.5": 3_000_000,
        "Gemini 3.1 Pro": 1_000_000,
        "Gemini 2.5 Flash": 4_500_000,
    }
    planted = {
        (36, "GPT-4o"): 6.0,
        (61, "GPT-4o-mini"): 4.0,
    }

    rows = []
    for _, m in models.iterrows():
        base = weekday_input.get(m["model_name"], 1_000_000)
        for i in range(DAYS):
            day = START + pd.Timedelta(days=i)
            dow = day.dayofweek
            seasonal = 0.35 if dow >= 5 else 1.0
            noise = RNG.lognormal(mean=0.0, sigma=0.12)
            trend = 1.0 + 0.0015 * i
            spike = planted.get((i, m["model_name"]), 1.0)
            inp = int(base * seasonal * noise * trend * spike)
            out = int(inp * 0.25 * RNG.uniform(0.85, 1.15))
            rows.append((int(m["model_id"]), day.strftime("%Y-%m-%d"), inp, out))

    conn.execute("DELETE FROM daily_usage")
    conn.executemany(
        """
        INSERT INTO daily_usage (model_id, usage_date, input_tokens, output_tokens)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def load_spend(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
            u.usage_date,
            p.name AS provider,
            m.model_name,
            u.input_tokens,
            u.output_tokens,
            od.input_per_mtok,
            od.output_per_mtok,
            (u.input_tokens  / 1e6) * od.input_per_mtok
          + (u.output_tokens / 1e6) * od.output_per_mtok AS spend_usd
        FROM daily_usage u
        JOIN models m ON u.model_id = m.model_id
        JOIN providers p ON m.provider_id = p.provider_id
        JOIN on_demand_pricing od ON od.model_id = m.model_id
        ORDER BY u.usage_date, p.name, m.model_name
        """,
        conn,
    )
    df["usage_date"] = pd.to_datetime(df["usage_date"])
    return df


def detect(df: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for model, g in df.groupby("model_name", sort=False):
        g = g.sort_values("usage_date").copy()
        med = g["spend_usd"].rolling(WINDOW, min_periods=WINDOW).median().shift(1)
        mad = (
            g["spend_usd"]
            .rolling(WINDOW, min_periods=WINDOW)
            .apply(lambda s: np.median(np.abs(s - np.median(s))), raw=True)
            .shift(1)
        )
        g["baseline"] = med
        g["mad"] = mad.replace(0, np.nan)
        g["robust_z"] = 0.6745 * (g["spend_usd"] - g["baseline"]) / g["mad"]
        g["ratio"] = g["spend_usd"] / g["baseline"]
        g["is_anomaly"] = ((g["robust_z"] >= Z_THRESH) & (g["ratio"] >= RATIO_FLOOR)) | (
            g["ratio"] >= RATIO_THRESH
        )
        g["is_anomaly"] = g["is_anomaly"].fillna(False)
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def plot(df: pd.DataFrame) -> Path:
    daily = df.groupby("usage_date", as_index=False)["spend_usd"].sum()
    flags = (
        df[df["is_anomaly"]]
        .groupby("usage_date", as_index=False)
        .agg(spend_usd=("spend_usd", "sum"), n=("model_name", "count"))
    )

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    axes[0].plot(daily["usage_date"], daily["spend_usd"], color="#1B2A4A", linewidth=1.6)
    if not flags.empty:
        axes[0].scatter(
            flags["usage_date"],
            daily.set_index("usage_date").loc[flags["usage_date"], "spend_usd"],
            color="#C0392B",
            zorder=5,
            label="flagged day",
        )
    axes[0].set_title("Portfolio daily LLM spend")
    axes[0].set_ylabel("USD")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc="upper left")

    gpt = df[df["model_name"] == "GPT-4o"].sort_values("usage_date")
    axes[1].plot(gpt["usage_date"], gpt["spend_usd"], color="#1B2A4A", linewidth=1.6, label="GPT-4o")
    axes[1].plot(gpt["usage_date"], gpt["baseline"], color="#C9A84C", linewidth=1.2, label="14-day median")
    anom = gpt[gpt["is_anomaly"]]
    axes[1].scatter(anom["usage_date"], anom["spend_usd"], color="#C0392B", zorder=5)
    axes[1].set_title("GPT-4o \u2014 trailing baseline vs actual")
    axes[1].set_ylabel("USD")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc="upper left")

    fig.tight_layout()
    out = OUTPUTS_DIR / "anomaly_detection.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"No database at {DB_PATH}. Run: python src/01_load_pricing.py")

    with sqlite3.connect(DB_PATH) as conn:
        seed_usage(conn)
        spend = load_spend(conn)

    flagged = detect(spend)
    hits = flagged[flagged["is_anomaly"]].copy()
    hits["usage_date"] = hits["usage_date"].dt.strftime("%Y-%m-%d")

    print(f"Days priced: {flagged['usage_date'].nunique()}")
    print(f"Model-days flagged: {len(hits)}")
    print()
    cols = [
        "usage_date",
        "provider",
        "model_name",
        "spend_usd",
        "baseline",
        "robust_z",
        "ratio",
    ]
    if hits.empty:
        print("No anomalies flagged.")
    else:
        printable = hits[cols].round({"spend_usd": 2, "baseline": 2, "robust_z": 2, "ratio": 2})
        print(printable.to_string(index=False))

    chart = plot(flagged)
    csv_out = OUTPUTS_DIR / "anomalies.csv"
    hits.to_csv(csv_out, index=False)
    flagged.to_csv(OUTPUTS_DIR / "daily_spend.csv", index=False)
    print(f"\nWrote {chart}")
    print(f"Wrote {csv_out}")
    print(f"Wrote {OUTPUTS_DIR / 'daily_spend.csv'}")


if __name__ == "__main__":
    main()
