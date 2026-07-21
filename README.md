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

- **Fluency in LLM/AI pricing models** across providers — Azure OpenAI PTUs (hourly vs. reservation ladders), pay-as-you-go token pricing, AWS Bedrock's model-unit pricing, and GCP's GSU economics — and how their break-even math differs.
- **Commitment planning** — when a reserved/committed capacity model beats on-demand, and the utilization threshold where that flips.
- **Cost anomaly detection** — identifying unexpected spend patterns in usage data before they become billing surprises.
- **Practical data engineering** — Python and SQL against a SQLite store, structured so pricing data loads cleanly and analyses are reproducible.

In short: the FinOps discipline I've practiced on traditional cloud spend for years, applied to the AI-era cost problems that are just now taking shape.

## The problem, in plain terms

Three things make AI cost hard to govern, and each maps to a piece of this lab:

1. **Opaque units.** A "PTU," a "model unit," and a "GSU" are not intuitive, and you can't manage what you can't translate into dollars. → *Pricing foundation.*
2. **Commitment vs. on-demand.** Providers offer discounts for committing to capacity, but committing to the wrong amount wastes money in the other direction. Knowing the break-even point requires modeling, not guessing. → *Break-even analysis.*
3. **Silent spikes.** AI usage is bursty, and cost anomalies hide easily in normal-looking data. → *Anomaly detection.*

## Modules

| Module | What it does | Status |
|---|---|---|
| **01 · Pricing foundation** | Seeds and loads multi-provider LLM/AI pricing into SQLite (`data/seed_pricing.sql`, `src/01_load_pricing.py`); SQL queries to compare unit economics across providers | ✅ Complete |
| **02 · Break-even analysis** | `src/02_breakeven.py` — models Azure PTU (committed) vs. pay-as-you-go across commitment terms and visualizes the utilization point where committing pays off | 🚧 In progress |
| **03 · Cost anomaly detection** | `src/03_anomaly.py` — flags abnormal spend/usage patterns in AI cost data | 🗺️ Planned |

## Tech stack

- **Python** (`pandas`) — data loading, modeling, and analysis
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
pip install pandas matplotlib          # SQLite ships with Python

# 4. Build the pricing database (Module 01)
python src/01_load_pricing.py

# 5. Run an analysis (example — Module 02)
python src/02_breakeven.py
```

## How this was built

This lab is deliberately built with an **AI-assisted, human-owned** workflow: I use an AI assistant for design discussion and peer-style code review, but I write and run the code myself and own every architecture and modeling decision. That's intentional — governing AI-generated work with a human in the loop is exactly the discipline I advocate for on the cost-governance side, so the project practices what it preaches.

## About

I'm a FinOps and cloud cost-optimization practitioner focused on the emerging discipline of **AI cost governance** — bringing the accountability, commitment planning, and anomaly detection that matured on traditional cloud spend to LLM and AI infrastructure.

🔗 [linkedin.com/in/tsweetz](https://www.linkedin.com/in/tsweetz)
