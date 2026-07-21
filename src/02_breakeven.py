import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# Connect
conn = sqlite3.connect('llm_costs.db')

# On-demand (all models)
df_payg = pd.read_sql_query('''
    SELECT 
        p.name AS provider,
        m.model_name,
        od.input_per_mtok,
        od.output_per_mtok
    FROM on_demand_pricing od
    JOIN models m ON od.model_id = m.model_id
    JOIN providers p ON m.provider_id = p.provider_id;
''', conn)

# Committed pricing (now with multiple terms)
df_commit = pd.read_sql_query('''
    SELECT 
        p.name AS provider,
        m.model_name,
        c.unit_name,
        c.hourly_rate,
        c.term,
        c.throughput_note
    FROM committed_pricing c
    LEFT JOIN models m ON c.model_id = m.model_id
    JOIN providers p ON c.provider_id = p.provider_id;
''', conn)

conn.close()

print("PAYG Models Loaded:", len(df_payg))
print("Committed Rows Loaded:", len(df_commit))

# === Breakeven Chart ===
plt.figure(figsize=(12, 8))

monthly_tokens = np.linspace(0, 100_000_000, 300)

colors = ['blue', 'orange', 'green', 'red', 'purple']
for i, row in df_payg.iterrows():
    blended_rate = (row['input_per_mtok'] * 0.75 + row['output_per_mtok'] * 0.25)
    cost = monthly_tokens * blended_rate / 1_000_000
    plt.plot(monthly_tokens / 1_000_000, cost, 
             label=f"{row['provider']} - {row['model_name']}", 
             linewidth=2.2)

# Example committed lines (you can expand this later)
plt.axhline(y=800, color='darkred', linestyle='--', linewidth=2, 
            label='Example PTU Committed (~$800/mo)')
plt.axhline(y=2000, color='darkred', linestyle='-.', linewidth=2, 
            label='Higher Commitment Tier')

plt.title('LLM Cost: Pay-As-You-Go vs Committed Pricing (Updated Schema)')
plt.xlabel('Monthly Tokens (Millions)')
plt.ylabel('Monthly Cost ($)')
plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
plt.grid(True, alpha=0.3)
plt.tight_layout()

plt.savefig('outputs/breakeven_analysis.png', dpi=200, bbox_inches='tight')
print("\n✅ Updated breakeven chart saved!")

plt.show()