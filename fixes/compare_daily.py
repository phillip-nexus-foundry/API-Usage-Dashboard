"""Compare DB opus token totals vs Claude ground truth by day."""
import sqlite3

c = sqlite3.connect("dashboard.db")

# Today
r = c.execute("""
    SELECT COUNT(*), SUM(tokens_input), SUM(tokens_output), 
           SUM(tokens_cache_read), SUM(tokens_cache_write),
           SUM(tokens_input + tokens_cache_read + tokens_cache_write)
    FROM records 
    WHERE model='claude-opus-4-6' 
    AND DATE(timestamp/1000, 'unixepoch')='2026-02-27'
""").fetchone()
print(f"TODAY opus DB:    calls={r[0]} in_side={r[5]} out={r[2]} (in={r[1]} cr={r[3]} cw={r[4]})")
print(f"TODAY Claude:     in_side=4,868,674 out=31,678")
print(f"  Diff in_side: {r[5] - 4868674}")
print(f"  Diff output:  {r[2] - 31678}")

# Before today
r2 = c.execute("""
    SELECT COUNT(*), SUM(tokens_input), SUM(tokens_output),
           SUM(tokens_cache_read), SUM(tokens_cache_write),
           SUM(tokens_input + tokens_cache_read + tokens_cache_write)
    FROM records
    WHERE model='claude-opus-4-6'
    AND DATE(timestamp/1000, 'unixepoch') < '2026-02-27'
""").fetchone()
print(f"\nBEFORE opus DB:   calls={r2[0]} in_side={r2[5]} out={r2[2]} (in={r2[1]} cr={r2[3]} cw={r2[4]})")
print(f"BEFORE Claude:    in_side=1,361,303 out=15,689")
print(f"  Diff in_side: {r2[5] - 1361303}")
print(f"  Diff output:  {r2[2] - 15689}")

# Total
total_in_side = r[5] + r2[5]
total_out = r[2] + r2[2]
print(f"\nTOTAL DB:         in_side={total_in_side} out={total_out}")
print(f"TOTAL Claude:     in_side=6,229,977 out=47,367")
print(f"  Diff in_side: {total_in_side - 6229977}")
print(f"  Diff output:  {total_out - 47367}")

c.close()
