"""Shared paths so scripts work from any working directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUTS_DIR = ROOT / "outputs"
DB_PATH = ROOT / "llm_costs.db"
SEED_PATH = DATA_DIR / "seed_pricing.sql"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
