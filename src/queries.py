import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"No database at {DB_PATH}. Run: python src/01_load_pricing.py")

    conn = sqlite3.connect(DB_PATH)

    print("=== Cheapest model per provider by 3:1 blended cost ===")
    query1 = """
    WITH blended AS (
        SELECT
            p.name AS provider,
            m.model_name,
            m.tier,
            (od.input_per_mtok * 0.75 + od.output_per_mtok * 0.25) AS blended_cost
        FROM on_demand_pricing od
        JOIN models m ON od.model_id = m.model_id
        JOIN providers p ON m.provider_id = p.provider_id
    )
    SELECT
        provider,
        model_name,
        tier,
        ROUND(blended_cost, 4) AS blended_3_1
    FROM blended
    WHERE blended_cost = (
        SELECT MIN(blended_cost)
        FROM blended b2
        WHERE b2.provider = blended.provider
    )
    ORDER BY blended_3_1;
    """
    for row in conn.execute(query1):
        print(row)

    print("\n=== Output-to-Input Price Ratio per Model ===")
    query2 = """
    SELECT
        p.name AS provider,
        m.model_name,
        m.tier,
        ROUND(od.output_per_mtok / od.input_per_mtok, 2) AS output_input_ratio,
        od.input_per_mtok,
        od.output_per_mtok
    FROM on_demand_pricing od
    JOIN models m ON od.model_id = m.model_id
    JOIN providers p ON m.provider_id = p.provider_id
    ORDER BY output_input_ratio DESC, provider, model_name;
    """
    for row in conn.execute(query2):
        print(row)

    conn.close()


if __name__ == "__main__":
    main()
