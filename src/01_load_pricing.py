from pathlib import Path
import sqlite3

DB_PATH = Path('llm_costs.db')
SEED_PATH = Path(__file__).parent.parent / 'data' / 'seed_pricing.sql'

def create_schema(conn):
    conn.executescript('''
        PRAGMA foreign_keys = ON;  -- Enable foreign key enforcement
        
        CREATE TABLE IF NOT EXISTS providers (
            provider_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS models (
            model_id INTEGER PRIMARY KEY,
            provider_id INTEGER REFERENCES providers(provider_id),
            model_name TEXT NOT NULL,
            tier TEXT
        );
        
        CREATE TABLE IF NOT EXISTS on_demand_pricing (
            pricing_id INTEGER PRIMARY KEY,
            model_id INTEGER REFERENCES models(model_id),
            input_per_mtok REAL NOT NULL,
            output_per_mtok REAL NOT NULL,
            effective_date TEXT NOT NULL,
            expires_date TEXT,
            source_url TEXT
        );
        
        CREATE TABLE IF NOT EXISTS committed_pricing (
            commit_id INTEGER PRIMARY KEY,
            provider_id INTEGER REFERENCES providers(provider_id),
            model_id INTEGER REFERENCES models(model_id),   -- NEW: nullable model-specific
            unit_name TEXT,
            hourly_rate REAL,
            term TEXT,
            throughput_note TEXT,
            effective_date TEXT NOT NULL,
            source_url TEXT
        );
    ''')

def load_seed():
    with sqlite3.connect(DB_PATH) as conn:
        create_schema(conn)
        
        # Clear existing data for clean reload
        conn.executescript('''
            DELETE FROM on_demand_pricing;
            DELETE FROM committed_pricing;
            DELETE FROM models;
            DELETE FROM providers;
        ''')
        
        with open(SEED_PATH, 'r', encoding='utf-8') as f:
            seed_sql = f.read()
        
        conn.executescript(seed_sql)
        conn.commit()
        
        # Self-auditing verification
        print("✅ Database loaded successfully!")
        print("Providers:", conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0])
        print("Models:", conn.execute("SELECT COUNT(*) FROM models").fetchone()[0])
        print("On-demand rows:", conn.execute("SELECT COUNT(*) FROM on_demand_pricing").fetchone()[0])
        print("Committed rows:", conn.execute("SELECT COUNT(*) FROM committed_pricing").fetchone()[0])

if __name__ == "__main__":
    load_seed()