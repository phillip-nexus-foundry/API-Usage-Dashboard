"""
Extract sample data showing the cache write cost problem.
Run this to see the actual records that need fixing.
"""
import sqlite3
import json

DB_PATH = "dashboard.db"

def show_samples():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("=" * 80)
    print("SAMPLE OPUS RECORDS SHOWING CACHE WRITE ISSUE")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            datetime(timestamp/1000, 'unixepoch', 'localtime') as local_time,
            session_id,
            model,
            tokens_input,
            tokens_output,
            tokens_cache_read,
            tokens_cache_write,
            cost_total,
            cost_cache_write
        FROM records 
        WHERE provider = 'anthropic'
        AND model = 'claude-opus-4-6'
        AND tokens_cache_write > 0
        ORDER BY timestamp ASC
        LIMIT 20
    """)
    
    prev_cache_write = 0
    print(f"{'Time':<20} {'Session':<12} {'Input':<8} {'Output':<8} {'CacheRead':<10} {'CacheWrite':<12} {'Delta':<10} {'Cost':<10}")
    print("-" * 100)
    
    for row in cursor.fetchall():
        time_str, session, model, inp, out, cread, cwrite, cost, cwrite_cost = row
        session_short = session[:8] if session else "N/A"
        delta = cwrite - prev_cache_write if prev_cache_write > 0 else cwrite
        print(f"{time_str:<20} {session_short:<12} {inp:<8} {out:<8} {cread:<10} {cwrite:<12} {delta:<10} ${cost:<9.4f}")
        prev_cache_write = cwrite
    
    print("\n" + "=" * 80)
    print("ANALYSIS:")
    print("=" * 80)
    print("""
The 'Delta' column shows the change in cache_write from the previous call.

PROBLEM:
- If Delta is the same as CacheWrite, we're charging for the full cumulative cache
- Anthropic only charges for NEW cache writes (the delta), not cumulative size
- The 'Cost' column includes charges for the entire CacheWrite amount

EXAMPLE FIX LOGIC:
- First call with 36,097 cache_write: charge $0.68 (full amount)
- Second call with 50,372 cache_write: charge only delta (14,275) = $0.27
- NOT the full $0.94 that would be charged for 50,372 tokens

This pattern repeats across all calls, causing the ~$8 overcharge.
""")
    
    conn.close()

if __name__ == "__main__":
    show_samples()
