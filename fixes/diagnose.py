"""Diagnose cost discrepancy between dashboard and Anthropic ground truth."""
import sqlite3

c = sqlite3.connect("dashboard.db")

# 1. Check for duplicate call_ids
dupes = c.execute("SELECT call_id, COUNT(*) FROM records GROUP BY call_id HAVING COUNT(*) > 1").fetchall()
print(f"Duplicate call_ids: {len(dupes)}")
if dupes:
    for d in dupes[:5]:
        print(f"  {d[0]}: {d[1]} copies")

# 2. File index stats
fi = c.execute("SELECT filepath, record_count FROM file_index ORDER BY record_count DESC LIMIT 20").fetchall()
print(f"\nTop files by record count:")
for f in fi:
    short = f[0].split("\\")[-1] if "\\" in f[0] else f[0].split("/")[-1]
    print(f"  {f[1]:5d} records: {short}")

reset_files = [f for f in fi if "reset" in f[0].lower()]
print(f"\nReset files in index: {len(reset_files)}")

# 3. Token totals per model for Anthropic
print("\n=== Anthropic Token Totals ===")
rows = c.execute("""
    SELECT model, 
           SUM(tokens_input) as tin, SUM(tokens_output) as tout,
           SUM(tokens_cache_read) as tcr, SUM(tokens_cache_write) as tcw,
           SUM(cost_total) as cost, COUNT(*) as calls
    FROM records WHERE provider='anthropic'
    GROUP BY model ORDER BY cost DESC
""").fetchall()
grand_cost = 0
grand_in = 0
grand_out = 0
for r in rows:
    total_in_side = r[1] + r[3] + r[4]
    print(f"  {r[0]}: in={r[1]} out={r[2]} cr={r[3]} cw={r[4]} total_in_side={total_in_side} cost=${r[5]:.4f} calls={r[6]}")
    grand_cost += r[5]
    grand_in += total_in_side
    grand_out += r[2]
print(f"\n  GRAND TOTAL: in_side={grand_in} out={grand_out} cost=${grand_cost:.4f}")

# 4. Ground truth comparison
print("\n=== Ground Truth Comparison ===")
print(f"  Dashboard Anthropic cost: ${grand_cost:.4f}")
print(f"  Anthropic ground truth:   $19.76 (balance $30.24 of $50)")
print(f"  Discrepancy:              ${grand_cost - 19.76:.4f} (dashboard OVER-reports)")

# 5. Opus-specific check
print("\n=== Opus Token Check ===")
opus = c.execute("""
    SELECT SUM(tokens_input), SUM(tokens_output), SUM(tokens_cache_read), SUM(tokens_cache_write),
           SUM(cost_total)
    FROM records WHERE model='claude-opus-4-6'
""").fetchone()
opus_in_side = opus[0] + opus[2] + opus[3]
print(f"  Dashboard opus: in_side={opus_in_side} out={opus[1]} cost=${opus[4]:.4f}")
print(f"  Claude reports: in=6,229,977 out=47,367")
print(f"  Token diff (in_side): {opus_in_side - 6229977} (positive = we over-count)")
print(f"  Token diff (output):  {opus[1] - 47367} (positive = we over-count)")

# 6. Check cache_write patterns — look for suspiciously large values
print("\n=== Large Cache Write Records (Opus) ===")
big_cw = c.execute("""
    SELECT call_id, timestamp, tokens_cache_write, cost_cache_write
    FROM records WHERE model='claude-opus-4-6' AND cost_cache_write > 0.1
    ORDER BY cost_cache_write DESC LIMIT 10
""").fetchall()
for r in big_cw:
    print(f"  {r[0][:30]}... cw={r[2]} cost=${r[3]:.4f}")

c.close()
