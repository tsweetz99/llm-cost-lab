# llm-cost-lab

**A hands-on lab for modeling and governing the cost of LLM and AI infrastructure.**

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![SQL](https://img.shields.io/badge/SQL-analytics-336791)
![Status](https://img.shields.io/badge/status-active-brightgreen)

---

## Why this exists

As organizations move real workloads onto large language models and AI infrastructure, a new cost-management problem is emerging: **AI compute is expensive, its pricing is opaque, and the tools built for traditional cloud spend don't map cleanly onto it.** Commitment models differ from provider to provider, pricing units are unintuitive, and cost can spike without anyone noticing until the invoice arrives.

`llm-cost-lab` is where I work through those problems in code — building the pricing models, break-even analyses, and anomaly-detection logic a FinOps practitioner needs to bring AI spend under control. It's a working lab, not a polished product: the point is to reason about AI cost governance concretely, with real data and real math, rather than in the abstract.

## What it demonstrates

For anyone reviewing this as a work sample, the project shows:

- **Fluency in LLM/AI pricing models** across providers — Azure OpenAI PTUs (hourly vs. 1-month/1-year reservations), GCP's GSUs (1-week to 1-year terms), and pay-as-you-go token pricing across Azure, AWS Bedrock, and GCP — including how each provider's capacity units "burn down" faster for output tokens than input tokens, which changes the break-even math.
- **Commitment planning** — when a committed-capacity model beats on-demand, and the sustained utilization where that flips. For most terms modeled here it is close to saturation.
- **Cost anomaly detection** — identifying unexpected spend patterns in usage data before they become billing surprises.
- **Practical data engineering** — Python and SQL against a SQLite store, structured so pricing data loads cleanly and analyses are reproducible.

In short: the FinOps discipline I've practiced on traditional cloud spend for years, applied to the AI-era cost problems that are just now taking shape.

## The problem, in plain terms

Three things make AI cost hard to govern, and each maps to a piece of this lab:

1. **Opaque units.** A "PTU," a "model unit," and a "GSU" are not intuitive, one output token can consume the capacity of 4–9 input tokens, and you can't manage what you can't translate into dollars. → *Pricing foundation.*
2. **Commitment vs. on-demand.** Providers offer discounts for committing to capacity, but committing to the wrong amount wastes money in the other direction. Knowing the break-even point requires modeling, not guessing. → *Break-even analysis.*
3. **Silent spikes.** AI usage is bursty, and cost anomalies hide easily in normal-looking data. → *Anomaly detection.*

## Modules

| Module | What it does | Status |
|---|---|---|
| **00 · Price refresh** | `data/price_catalog.json` is the pricing source of truth; `src/00_refresh_prices.py` regenerates `data/seed_pricing.sql` from it and checks that each commitment price is still published on the provider's page | ✅ Complete |
| **01 · Pricing foundation** | Loads on-demand prices, commitment terms, and per-model unit capacity into SQLite (`src/01_load_pricing.py`); SQL queries to compare unit economics across providers | ✅ Complete |
| **02 · Break-even analysis** | `src/02_breakeven.py` — models Azure PTU and GCP GSU commitments vs. pay-as-you-go across terms, with burndown-adjusted capacity, and shows how much traffic peakiness (peak-to-average ratio) a commitment can tolerate before it stops paying off | ✅ Complete |
| **03 · Cost anomaly detection** | `src/03_anomaly.py` — prices a 90-day usage series and flags spend spikes against a trailing robust baseline that is weekday/weekend-aware; `src/stress_test_anomaly.py` moves the incident around to test it | ✅ Complete |
| **04 · Hybrid sizing** | `src/04_hybrid_sizing.py` — finds how much to commit when overflow can spill to pay-as-you-go, across illustrative weekly load shapes or your own hourly token CSV | ✅ Complete |

## How the math works

**Break-even (Module 02)**

- Workload mix is 75% input / 25% output tokens. One parameter drives both the pay-as-you-go blend and the capacity math.
- Monthly committed bill for one unit = `hourly_rate × 730`.
- Break-even tokens = that bill ÷ blended $/token.
- **Capacity is published in burndown tokens, not raw tokens.** Providers quote a unit's throughput in input-equivalent tokens per minute, and each output token burns several of them (Azure GPT-4o 4×, Gemini 3.1 Pro 6×, Gemini 2.5 Flash 9×). Real-token capacity for a mix with output share *s* is `burndown_tpm ÷ ((1 − s) + s × output_burn)`, times 60 × 730 for a month.
- Break-even utilization = break-even tokens ÷ real-token capacity. Over 100% means one unit hits its ceiling before it can beat pay-as-you-go.
- Burn multipliers track each model's output/input price ratio, so break-even utilization is nearly independent of the mix. The script flags any row where the two diverge by more than 15% (`burn_matches_price`), which usually means a data-entry error.
- **Peakiness matters because a commitment must be sized for the peak.** With peak-to-average ratio *P* (peak-minute TPM ÷ average TPM), a unit sized to be fully busy at peak averages `1 ÷ P` utilization. So commitment cost ÷ pay-as-you-go cost = `P × break-even utilization`, and the highest *P* a commitment can tolerate is `1 ÷ break-even utilization` (`max_peak_to_avg`).

**Key results** (one unit, 75/25 mix):

| Unit · model | Break-even util., 1-month | Break-even util., 1-year | Max peak-to-avg, 1-year | One-unit capacity (M tokens/mo) |
|---|---|---|---|---|
| Azure PTU · GPT-4o | 95% | 81% | 1.24 | 62.6 |
| Azure PTU · GPT-5 | ≈100% | 85% | 1.18 | 75.7 |
| Azure PTU · GPT-4o-mini | 107% | 91% | 1.10 | 926.1 |
| GCP GSU · Gemini 2.5 Flash | 135% | ≈100% | 1.00 | 2,356.4 |
| GCP GSU · Gemini 3.1 Pro | 103% | 76% | 1.31 | 584.0 |

Utilization cells are the share of the unit's real-token capacity you must sustain before the commitment beats pay-as-you-go; over 100% means it never pays on a single unit. Shorter terms (hourly, 1-week) are worse in every case. Full tables: `outputs/breakeven_table.csv` and `outputs/peak_sensitivity.csv`.

![1-year commitment savings vs peak-to-average ratio](outputs/peak_to_average.png)

A 1-year commitment sized to the peak only beats pay-as-you-go while peak-minute traffic stays within roughly 10–30% of the average. Past that it costs more: at a peak-to-average ratio of 2, every 1-year term modeled costs 52–100% more than staying on demand. Module 04 relaxes the sized-to-peak assumption.

**Hybrid sizing (Module 04)**

Azure (spillover, opt-in) and Vertex AI (default) can serve traffic above a commitment at pay-as-you-go rates, so the question becomes "how much should I commit?" rather than "should I commit?". Normalize by the pay-as-you-go cost of all tokens, let *x(t)* be the hourly load as a multiple of the average, and *u* the break-even utilization:

- Committing *c* × the average load costs `u·c + mean(max(x − c, 0))`.
- The optimum is the load level exceeded a fraction *u* of the time. If *u* ≥ 1, commit nothing.
- Savings are never negative, and are capped at `(1 − u)` times the share of tokens the commitment serves, because the discount only applies to committed tokens.
- Savings depend on the load shape and *u*, not on volume.

1-year hybrid savings vs pay-as-you-go, on illustrative weekly hourly load shapes (*P* = hourly peak-to-average):

| Unit · model | flat (P=1.00) | diurnal 24×7 (P=1.64) | business hours (P=2.57) | nightly batch (P=3.60) |
|---|---|---|---|---|
| Azure PTU · GPT-4o | 19.3% | 9.8% | 5.0% | 9.3% |
| Azure PTU · GPT-5 | 15.0% | 7.3% | 3.9% | 7.2% |
| Azure PTU · GPT-4o-mini | 9.1% | 4.0% | 2.3% | 4.4% |
| GCP GSU · Gemini 2.5 Flash | 0.1% | 0.0% | 0.0% | 0.1% |
| GCP GSU · Gemini 3.1 Pro | 23.9% | 12.6% | 6.2% | 11.5% |

![Sized-to-peak vs hybrid savings by load shape](outputs/hybrid_sizing.png)

Sized to the peak, the same non-flat shapes lose 25–260%; hybrid sizing never loses. But the optimal commitment covers only the base load (0.26–0.62× the average for non-flat shapes), so realistic savings are small: 2–13% for the Azure models and Gemini 3.1 Pro, versus up to 24% for a perfectly flat load. Commitments here are a modest discount on a steady floor, not a lever on total spend. The shapes are illustrative, not measurements: run `python src/04_hybrid_sizing.py --load-csv your_hourly_tokens.csv` (a `tokens` column) to size against your own load. Full results: `outputs/hybrid_sizing.csv`.

**Anomaly detection (Module 03)**

- Daily spend is priced from the on-demand table.
- Baseline is the trailing median of the previous four weeks of same-class days (20 weekdays, or 8 weekend days); scale is the MAD of the same window. The first four weeks are warm-up.
- A day flags if (robust z ≥ 3.5 **and** spend ≥ 1.75× baseline) **or** spend ≥ 2.5× baseline.
- Two incidents are planted on purpose so the output is inspectable: a GPT-4o agent-loop spike and a GPT-4o-mini eval job left running.

![Portfolio spend and the GPT-4o baseline, with flagged days](outputs/anomaly_detection.png)

**Why the baseline is day-class aware.** Weekend volume is about 35% of weekday volume. A first version used one trailing 14-day window that mixed the two, which inflates both the baseline and the MAD, so weekend incidents had to be roughly twice as large to flag, and an agent loop or a forgotten eval job is exactly what runs unnoticed over a weekend. `src/stress_test_anomaly.py` moves the GPT-4o incident to other days and magnitudes and re-scores both baselines across 100 random seeds (detect rate, share of seeds where the planted day flags):

| Planted GPT-4o incident | Day-class baseline | Legacy mixed 14-day window |
|---|---|---|
| Thursday, 6× | 100% | 100% |
| Thursday, 1.9× | 78% | 77% |
| Saturday, 6× | 100% | 91% |
| Saturday, 3× | 100% | 0% |
| Sunday, 3× | 100% | 0% |
| Saturday, 2× | 89% | 0% |

False flags average 0.010 per 90-day × 8-model run with the day-class baseline versus 0.020 with the legacy window. The shipped seed flags exactly the two planted days, but a single seed is not a guarantee: a mild 1.9× weekday spike is caught about three times in four. Full results: `outputs/anomaly_stress_test.csv`.

**Where the data comes from**

Pricing is a dated lab snapshot, not a live feed. `data/price_catalog.json` is the source of truth. `src/00_refresh_prices.py` regenerates `data/seed_pricing.sql` from it (never hand-edit the seed) and fails loudly if a commitment price figure disappears from the provider's page. Throughput and burn figures come from provider documentation and are not machine-checked; each row carries its `source_url`.

## Known limitations

- Text tokens on the ≤200k-context tier only. Prompt caching, batch discounts, long-context pricing, and non-text modalities are not modeled.
- Module 02's peak-to-average analysis assumes no overflow; Module 04 assumes overflow is billed at the standard pay-as-you-go rate. Both ignore whole-unit rounding and provider minimum deployments (Azure's global PTU minimum is 15). Vertex AI spills whole requests rather than tokens, and Azure spillover must be configured.
- The peak-to-average ratio and Module 04's load shapes are inputs, not measurements: the synthetic usage in Module 03 is daily, so it can't show intra-day peaks. Hourly shapes also understate minute-level peaks.
- Azure and GCP commitments are modeled. AWS Bedrock is covered for pay-as-you-go pricing only; Provisioned Throughput is not yet seeded.
- Gemini 3.1 Pro is a preview model, so its capacity figures may change.
- Module 03 runs on synthetic usage data with two planted incidents. It demonstrates the method, and the stress-test rates describe this synthetic noise (12% lognormal), not production detection accuracy.
- Module 03 models seasonality as weekday vs weekend only. Per-weekday patterns, holidays, and month-end effects are not handled, and the detector is one-sided (spikes, not drops).

## Tech stack

- **Python** (`pandas`, `numpy`) — data loading, modeling, and analysis
- **SQL / SQLite** — pricing data store and analytical queries
- **matplotlib** — break-even and cost visualizations

## Quickstart

```bash
# 1. Clone and enter the repo
git clone https://github.com/tsweetz99/llm-cost-lab.git
cd llm-cost-lab

# 2. (Optional) create a virtual environment
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt          # SQLite ships with Python

# 4. (Optional) regenerate the seed from the price catalog
python src/00_refresh_prices.py          # add --offline to skip the live page checks

# 5. Build the pricing database (Module 01; rebuilt from scratch on every run)
python src/01_load_pricing.py

# 6. Compare unit economics
python src/queries.py

# 7. Break-even: PTU/GSU commitments vs PAYG (Module 02)
python src/02_breakeven.py

# 8. Flag spend spikes (Module 03)
python src/03_anomaly.py
python src/stress_test_anomaly.py        # optional; ~40 s. Moves the incident around to test the detector

# 9. Hybrid commitment sizing (Module 04); add --load-csv your_hourly_tokens.csv for real load
python src/04_hybrid_sizing.py
```

Charts and tables land in `outputs/`.

## How this was built

This lab is deliberately built with an **AI-assisted, human-owned** workflow: I use an AI assistant for design discussion and peer-style code review, but I write and run the code myself and own every architecture and modeling decision. That's intentional — governing AI-generated work with a human in the loop is exactly the discipline I advocate for on the cost-governance side, so the project practices what it preaches.

## About

I'm a FinOps and cloud cost-optimization practitioner focused on the emerging discipline of **AI cost governance** — bringing the accountability, commitment planning, and anomaly detection that matured on traditional cloud spend to LLM and AI infrastructure.

🔗 [linkedin.com/in/tsweetz](https://www.linkedin.com/in/tsweetz)

## License

MIT. See [LICENSE](LICENSE).
