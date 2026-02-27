import sqlite3

conn = sqlite3.connect('dashboard.db')
cursor = conn.cursor()

# Get all Opus records with cache_write > 0
print('=== OPUS CALLS WITH CACHE WRITE ===')
cursor.execute("""
    SELECT 
        timestamp,
        tokens_input,
        tokens_output,
        tokens_cache_read,
        tokens_cache_write,
        cost_total,
        cost_cache_write
    FROM records 
    WHERE provider='anthropic' 
    AND model = 'claude-opus-4-6'
    AND tokens_cache_write > 0
    ORDER BY timestamp ASC
""")

rows = cursor.fetchall()
total_cache_write_cost = 0
total_actual_cost = 0

for row in rows:
    ts, inp, out, cread, cwrite, total, cwrite_cost = row
    # The cost_cache_write is being calculated as (cwrite/total_tokens) * cost_total
    # But we're charging $18.75/1M for ALL cache_write tokens
    
    # Recalculate what we SHOULD charge (no cache_write cost since it's cumulative)
    input_cost = (inp * 15.00) / 1_000_000
    output_cost = (out * 75.00) / 1_000_000
    cache_read_cost = (cread * 1.50) / 1_000_000
    # cache_write should be 0 since these are cumulative numbers, not per-call writes
    corrected_cost = input_cost + output_cost + cache_read_cost
    
    print(f"TS: {ts}")
    print(f"  Input: {inp}, Output: {out}, CacheRead: {cread}, CacheWrite: {cwrite}")
    print(f"  Recorded cost: ${total:.6f}, CacheWrite cost: ${cwrite_cost:.6f}")
    print(f"  Corrected cost (no cache_write): ${corrected_cost:.6f}")
    print(f"  OVERCHARGE: ${cwrite_cost:.6f}")
    print()
    
    total_cache_write_cost += cwrite_cost
    total_actual_cost += corrected_cost

print(f"=== SUMMARY ===")
print(f"Total cache_write costs charged: ${total_cache_write_cost:.6f}")
print(f"Total corrected costs: ${total_actual_cost:.6f}")

# Get current totals
cursor.execute("SELECT SUM(cost_total) FROM records WHERE provider='anthropic'")
recorded_total = cursor.fetchone()[0]
print(f"\nCurrent recorded Anthropic total: ${recorded_total:.6f}")
print(f"Actual should be: ${recorded_total - total_cache_write_cost:.6f}")
print(f"\nUser started with: $20.00")
print(f"Dashboard shows remaining: $2.36 (spent: $17.64)")
print(f"Anthropic site shows remaining: $10.79 (spent: $9.21)")
print(f"\nThe cache_write overcharge: ~${total_cache_write_cost:.2f}")
print(f"This explains most of the discrepancy!")

conn.close()
