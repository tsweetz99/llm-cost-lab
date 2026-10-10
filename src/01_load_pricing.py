from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, SEED_PATH


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS daily_usage;
        DROP TABLE IF EXISTS unit_capacity;
        DROP TABLE IF EXISTS commitment_terms;
        DROP TABLE IF EXISTS committed_pricing;
        DROP TABLE IF EXISTS on_demand_pricing;
        DROP TABLE IF EXISTS models;
        DROP TABLE IF EXISTS providers;
        PRAGMA foreign_keys = ON;

        CREATE TABLE providers (
            provider_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE models (
            model_id INTEGER PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES providers(provider_id),
            model_name TEXT NOT NULL,
            tier TEXT,
            UNIQUE (provider_id, model_name)
        );

        CREATE TABLE on_demand_pricing (
            pricing_id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL REFERENCES models(model_id),
            input_per_mtok REAL NOT NULL,
            output_per_mtok REAL NOT NULL,
            effective_date TEXT NOT NULL,
            expires_date TEXT,
            source_url TEXT
        );

        CREATE TABLE commitment_terms (
            term_id INTEGER PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES providers(provider_id),
            unit_name TEXT NOT NULL,
            term TEXT NOT NULL,
            hourly_rate REAL NOT NULL,
            note TEXT,
            effective_date TEXT NOT NULL,
            source_url TEXT,
            UNIQUE (provider_id, unit_name, term)
        );

        -- burndown_tpm is in input-equivalent tokens/min; output_burn is how many
        -- input tokens one output token consumes.
        CREATE TABLE unit_capacity (
            capacity_id INTEGER PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES providers(provider_id),
            unit_name TEXT NOT NULL,
            model_id INTEGER NOT NULL REFERENCES models(model_id),
            burndown_tpm REAL NOT NULL,
            output_burn REAL NOT NULL,
            note TEXT,
            source_url TEXT,
            UNIQUE (provider_id, unit_name, model_id)
        );

        CREATE TABLE daily_usage (
            usage_id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL REFERENCES models(model_id),
            usage_date TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            UNIQUE (model_id, usage_date)
        );
        """
    )


def load_seed() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        create_schema(conn)
        with open(SEED_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

        print(f"Database: {DB_PATH}")
        print("Providers:", conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0])
        print("Models:", conn.execute("SELECT COUNT(*) FROM models").fetchone()[0])
        print("On-demand rows:", conn.execute("SELECT COUNT(*) FROM on_demand_pricing").fetchone()[0])
        print("Commitment terms:", conn.execute("SELECT COUNT(*) FROM commitment_terms").fetchone()[0])
        print("Unit capacity rows:", conn.execute("SELECT COUNT(*) FROM unit_capacity").fetchone()[0])
        print("Daily usage rows:", conn.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0])


if __name__ == "__main__":
    load_seed()
