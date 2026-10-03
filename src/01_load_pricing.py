from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, SEED_PATH


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS providers (
            provider_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS models (
            model_id INTEGER PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES providers(provider_id),
            model_name TEXT NOT NULL,
            tier TEXT,
            UNIQUE (provider_id, model_name)
        );

        CREATE TABLE IF NOT EXISTS on_demand_pricing (
            pricing_id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL REFERENCES models(model_id),
            input_per_mtok REAL NOT NULL,
            output_per_mtok REAL NOT NULL,
            effective_date TEXT NOT NULL,
            expires_date TEXT,
            source_url TEXT
        );

        CREATE TABLE IF NOT EXISTS committed_pricing (
            commit_id INTEGER PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES providers(provider_id),
            model_id INTEGER REFERENCES models(model_id),
            unit_name TEXT,
            hourly_rate REAL NOT NULL,
            term TEXT NOT NULL,
            tpm_capacity REAL,
            throughput_note TEXT,
            effective_date TEXT NOT NULL,
            source_url TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_usage (
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
        conn.executescript(
            """
            DELETE FROM daily_usage;
            DELETE FROM on_demand_pricing;
            DELETE FROM committed_pricing;
            DELETE FROM models;
            DELETE FROM providers;
            """
        )
        with open(SEED_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

        print(f"Database: {DB_PATH}")
        print("Providers:", conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0])
        print("Models:", conn.execute("SELECT COUNT(*) FROM models").fetchone()[0])
        print("On-demand rows:", conn.execute("SELECT COUNT(*) FROM on_demand_pricing").fetchone()[0])
        print("Committed rows:", conn.execute("SELECT COUNT(*) FROM committed_pricing").fetchone()[0])
        print("Daily usage rows:", conn.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0])


if __name__ == "__main__":
    load_seed()
