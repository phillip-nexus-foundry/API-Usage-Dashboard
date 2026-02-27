"""
Recalculate ALL Anthropic costs with correct Opus 4.6 pricing 
and NO high-watermark (treat cacheWrite as per-call, which it is).
"""
import sqlite3
import json
from parsers.telemetry_schema import compute_cost_breakdown

DB_PATH = "dashboard.db"

def recalc():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT call_id, model, tokens_input, tokens_output, 
               tokens_cache_read, tokens_cache_write, tool_names,
               cost_total
        FROM records WHERE provider = 'anthropic'
    """)
    
    rows = cursor.fetchall()
    total_fixed = 0
    old_total = 0
    new_total = 0
    
    for row in rows:
        call_id, model, inp, out, cread, cwrite, tool_json, old_cost = row
        old_total += old_cost
        
        try:
            tools = json.loads(tool_json) if tool_json else []
        except:
            tools = []
        
        # Use raw cacheWrite as billable (per-call, no high-watermark)
        breakdown = compute_cost_breakdown(
            model=model,
            tokens_input=inp,
            tokens_output=out,
            tokens_cache_read=cread,
            tokens_cache_write_billable=cwrite,
            tool_names=tools,
        )
        
        new_cost = breakdown["cost_total"]
        new_total += new_cost
        
        if abs(old_cost - new_cost) > 0.000001:
            cursor.execute("""
                UPDATE records 
                SET cost_input=?, cost_output=?, cost_cache_read=?, 
                    cost_cache_write=?, cost_total=?
                WHERE call_id=?
            """, (
                breakdown["cost_input"],
                breakdown["cost_output"],
                breakdown["cost_cache_read"],
                breakdown["cost_cache_write"],
                new_cost,
                call_id,
            ))
            total_fixed += 1
    
    conn.commit()
    
    # Verify
    cursor.execute("SELECT SUM(cost_total) FROM records WHERE provider='anthropic'")
    final = cursor.fetchone()[0] or 0
    
    conn.close()
    
    print(f"Records updated: {total_fixed}")
    print(f"Old Anthropic total: ${old_total:.4f}")
    print(f"New Anthropic total: ${final:.4f}")
    print(f"Remaining (of $50):  ${50-final:.4f}")
    print(f"Ground truth:        $30.24")
    print(f"Discrepancy:         ${(50-final) - 30.24:.4f}")

if __name__ == "__main__":
    recalc()
