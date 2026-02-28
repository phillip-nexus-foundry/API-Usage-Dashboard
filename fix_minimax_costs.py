"""Recalculate all MiniMax-M2.5 costs in dashboard.db using correct pricing."""
import sqlite3

# Official MiniMax M2.5 pricing (per million tokens)
PRICING = {
    "input": 0.30,
    "output": 1.20,
    "cache_read": 0.03,
    "cache_write": 0.375,
}

conn = sqlite3.connect('dashboard.db')
c = conn.cursor()

# Get all MiniMax records
c.execute("""SELECT rowid, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write, cost_total
             FROM records WHERE model='MiniMax-M2.5'""")
rows = c.fetchall()

print(f"Found {len(rows)} MiniMax-M2.5 records to fix")
old_total = 0.0
new_total = 0.0

for row in rows:
    rowid, inp, out, cr, cw = row[0], row[1], row[2], row[3], row[4]
    old_cost = row[5]
    old_total += old_cost

    cost_input = (inp / 1_000_000) * PRICING["input"]
    cost_output = (out / 1_000_000) * PRICING["output"]
    cost_cache_read = (cr / 1_000_000) * PRICING["cache_read"]
    cost_cache_write = (cw / 1_000_000) * PRICING["cache_write"]
    cost_total = cost_input + cost_output + cost_cache_read + cost_cache_write

    new_total += cost_total

    c.execute("""UPDATE records SET cost_input=?, cost_output=?, cost_cache_read=?, cost_cache_write=?, cost_total=?
                 WHERE rowid=?""",
              (round(cost_input, 6), round(cost_output, 6), round(cost_cache_read, 6), round(cost_cache_write, 6), round(cost_total, 6), rowid))

conn.commit()
conn.close()

print(f"Old total cost: ${old_total:.6f}")
print(f"New total cost: ${new_total:.6f}")
print(f"Difference:     ${new_total - old_total:.6f}")
print(f"Expected remaining: ${25.0 - new_total:.2f} (deposit was $25)")
print(f"Actual remaining per MiniMax site: $24.74")
print(f"Dashboard will now show: ${25.0 - new_total:.2f}")
