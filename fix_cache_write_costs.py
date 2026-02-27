"""
Fix Anthropic cache_write overcharge in dashboard database.
Recalculates all Anthropic records using per-session/model cacheWrite deltas.
"""
import sqlite3
import json
from parsers.telemetry_schema import (
    compute_cost_breakdown,
    is_anthropic_model,
    anthropic_billable_cache_write_delta,
)

DB_PATH = "dashboard.db"

def fix_costs():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all Anthropic records that need recalculation
    cursor.execute("""
        SELECT 
            call_id,
            session_id,
            timestamp,
            model,
            tokens_input,
            tokens_output,
            tokens_cache_read,
            tokens_cache_write,
            tool_names,
            cost_total,
            cost_cache_write
        FROM records 
        WHERE provider = 'anthropic'
        ORDER BY session_id ASC, model ASC, timestamp ASC, call_id ASC
    """)
    
    rows = cursor.fetchall()
    total_fixed = 0
    total_savings = 0
    
    print(f"Found {len(rows)} Anthropic records to check...")
    
    cache_write_state = {}

    for row in rows:
        (
            call_id,
            session_id,
            _timestamp,
            model,
            inp,
            out,
            cread,
            cwrite,
            tool_names_json,
            old_total,
            old_cwrite_cost,
        ) = row
        
        # Parse tool_names
        try:
            tool_names = json.loads(tool_names_json) if tool_names_json else []
        except Exception:
            tool_names = []
        
        # Recalculate billable cache write tokens using cumulative-to-delta logic.
        billable_cache_write = cwrite
        if is_anthropic_model(model):
            key = (session_id, model)
            prev_highwater = cache_write_state.get(key)
            billable_cache_write = anthropic_billable_cache_write_delta(prev_highwater, cwrite)
            if prev_highwater is None:
                cache_write_state[key] = cwrite
            else:
                cache_write_state[key] = max(prev_highwater, cwrite)

        breakdown = compute_cost_breakdown(
            model=model,
            tokens_input=inp,
            tokens_output=out,
            tokens_cache_read=cread,
            tokens_cache_write_billable=billable_cache_write,
            tool_names=tool_names,
        )

        new_cost = breakdown["cost_total"]
        new_cost_input = breakdown["cost_input"]
        new_cost_output = breakdown["cost_output"]
        new_cost_cache_read = breakdown["cost_cache_read"]
        new_cost_cache_write = breakdown["cost_cache_write"]

        # Only update if there's a meaningful difference
        if abs(old_total - new_cost) > 0.0001 or abs(old_cwrite_cost - new_cost_cache_write) > 0.0001:
            cursor.execute("""
                UPDATE records 
                SET cost_input = ?,
                    cost_output = ?,
                    cost_cache_read = ?,
                    cost_cache_write = ?,
                    cost_total = ?
                WHERE call_id = ?
            """, (
                new_cost_input,
                new_cost_output,
                new_cost_cache_read,
                new_cost_cache_write,
                new_cost,
                call_id
            ))
            total_fixed += 1
            total_savings += (old_total - new_cost)

    conn.commit()

    # Show new totals
    cursor.execute("SELECT SUM(cost_total) FROM records WHERE provider = 'anthropic'")
    new_anthropic_total = cursor.fetchone()[0] or 0

    cursor.execute("SELECT SUM(cost_total) FROM records")
    new_grand_total = cursor.fetchone()[0] or 0

    conn.close()

    print(f"\n=== FIX COMPLETE ===")
    print(f"Records updated: {total_fixed}")
    print(f"Total cost reduction: ${total_savings:.2f}")
    print(f"\nNew Anthropic total: ${new_anthropic_total:.2f}")
    print(f"New grand total: ${new_grand_total:.2f}")
    print(f"\nExpected remaining balance: ${50 - new_anthropic_total:.2f}")
    print(f"Ground truth remaining:    $30.24")


if __name__ == "__main__":
    fix_costs()
