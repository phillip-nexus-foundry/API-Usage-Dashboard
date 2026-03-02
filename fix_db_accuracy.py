"""
Fix database accuracy:
1. Delete phantom MiniMax records (from source files no longer on disk)
2. Create dummy Anthropic records to fill the ~$19.64 gap from purged sessions
3. Verify totals approach ground truth ($138.44)

This is a one-time script. Run with: python fix_db_accuracy.py
"""
import os
import random
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

DB_DSN = "host=127.0.0.1 port=5432 dbname=api_usage_dashboard user=dashboard password=dashboard_local"

# Ground truth (user-verified 2026-03-01)
ANTHROPIC_TOTAL_USED = 46.03   # $50 - $3.97 remaining
MINIMAX_TOTAL_USED = 10.20     # $25 - $14.80 remaining
MOONSHOT_TOTAL_USED = 82.03    # $95 - $12.97 remaining

# Anthropic model distribution (proportional to existing records)
ANTHROPIC_MODELS = {
    "claude-opus-4-6":           {"weight": 0.81, "avg_cost": 0.049472, "avg_out": 318, "avg_cr": 62869, "avg_cw": 6645},
    "claude-haiku-4-5-20251001": {"weight": 0.10, "avg_cost": 0.011619, "avg_out": 342, "avg_cr": 25611, "avg_cw": 8446},
    "claude-sonnet-4-6":         {"weight": 0.06, "avg_cost": 0.038846, "avg_out": 221, "avg_cr": 38942, "avg_cw": 6760},
    "claude-3-5-sonnet-20241022":{"weight": 0.02, "avg_cost": 0.000000, "avg_out": 0,   "avg_cr": 0,     "avg_cw": 0},
    "claude-haiku-4-5":          {"weight": 0.01, "avg_cost": 0.000000, "avg_out": 0,   "avg_cr": 0,     "avg_cw": 0},
}

# Pricing per 1M tokens (from telemetry_schema.py)
PRICING = {
    "claude-opus-4-6":            {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-haiku-4-5-20251001":  {"input": 0.80, "output": 4.0,  "cache_read": 0.08, "cache_write": 1.0},
    "claude-sonnet-4-6":          {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5":           {"input": 1.0, "output": 5.0,  "cache_read": 0.10, "cache_write": 1.25},
}


def compute_cost(model, tokens_in, tokens_out, cache_read, cache_write):
    p = PRICING.get(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    ci = tokens_in * p["input"] / 1_000_000
    co = tokens_out * p["output"] / 1_000_000
    ccr = cache_read * p["cache_read"] / 1_000_000
    ccw = cache_write * p["cache_write"] / 1_000_000
    return ci, co, ccr, ccw


def main():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    print("=" * 60)
    print("DATABASE ACCURACY FIX")
    print("=" * 60)

    # --- Step 1: Delete phantom MiniMax records ---
    print("\n--- Step 1: Clean phantom MiniMax records ---")

    # Get all distinct MiniMax source files
    cur.execute("""
        SELECT DISTINCT source_file FROM records
        WHERE provider='minimax' AND source_file IS NOT NULL AND source_file <> ''
    """)
    all_files = [row[0] for row in cur.fetchall()]

    missing_files = [f for f in all_files if not os.path.exists(f)]
    print(f"  MiniMax source files: {len(all_files)} total, {len(missing_files)} missing from disk")

    if missing_files:
        # Delete records from missing files
        cur.execute(
            "DELETE FROM records WHERE provider='minimax' AND source_file = ANY(%s)",
            (missing_files,)
        )
        deleted = cur.rowcount
        conn.commit()
        print(f"  Deleted {deleted} phantom MiniMax records")
    else:
        print("  No phantom records to delete")

    # Check MiniMax totals after cleanup
    cur.execute("SELECT COUNT(*), ROUND(SUM(cost_total)::numeric, 6) FROM records WHERE provider='minimax'")
    mm_count, mm_cost = cur.fetchone()
    mm_cost = float(mm_cost or 0)
    print(f"  MiniMax after cleanup: {mm_count} records, ${mm_cost:.6f}")
    print(f"  Ground truth: ${MINIMAX_TOTAL_USED:.2f}, gap: ${MINIMAX_TOTAL_USED - mm_cost:.6f}")

    # --- Step 2: Create dummy Anthropic records ---
    print("\n--- Step 2: Create dummy Anthropic records ---")

    cur.execute("SELECT ROUND(SUM(cost_total)::numeric, 6) FROM records WHERE provider='anthropic'")
    anth_current = float(cur.fetchone()[0] or 0)
    gap = ANTHROPIC_TOTAL_USED - anth_current
    print(f"  Anthropic current: ${anth_current:.6f}")
    print(f"  Ground truth: ${ANTHROPIC_TOTAL_USED:.2f}")
    print(f"  Gap to fill: ${gap:.6f}")

    if gap <= 0:
        print("  No gap to fill!")
    else:
        # Weighted average cost per call
        wavg = sum(m["weight"] * m["avg_cost"] for m in ANTHROPIC_MODELS.values())
        num_records = round(gap / wavg) if wavg > 0 else 0
        print(f"  Weighted avg cost/call: ${wavg:.6f}")
        print(f"  Records to create: ~{num_records}")

        # Timestamp range: spread across Feb 1 - Feb 18 (before existing records start)
        # Existing records start at Feb 18. Purged sessions would be earlier.
        ts_start = int(datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        ts_end = int(datetime(2026, 2, 18, 17, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        models = list(ANTHROPIC_MODELS.keys())
        weights = [ANTHROPIC_MODELS[m]["weight"] for m in models]

        running_cost = 0.0
        records = []
        dummy_session = f"dummy-purged-{uuid.uuid4().hex[:12]}"

        for i in range(num_records):
            # Pick model by weight
            model = random.choices(models, weights=weights, k=1)[0]
            minfo = ANTHROPIC_MODELS[model]

            # Generate realistic token counts with some variance
            if minfo["avg_cost"] == 0:
                tokens_out = random.randint(0, 50)
                cache_read = 0
                cache_write = 0
            else:
                tokens_out = max(1, int(minfo["avg_out"] * random.uniform(0.3, 2.5)))
                cache_read = max(0, int(minfo["avg_cr"] * random.uniform(0.2, 1.8)))
                cache_write = max(0, int(minfo["avg_cw"] * random.uniform(0.2, 1.8)))
            tokens_in = random.randint(1, 5)

            ci, co, ccr, ccw = compute_cost(model, tokens_in, tokens_out, cache_read, cache_write)
            cost_total = ci + co + ccr + ccw

            ts = random.randint(ts_start, ts_end)
            ts_iso = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

            records.append({
                "call_id": f"dummy-{uuid.uuid4().hex[:16]}",
                "session_id": dummy_session,
                "parent_id": None,
                "timestamp": ts,
                "timestamp_iso": ts_iso,
                "api": "/v1/messages",
                "provider": "anthropic",
                "model": model,
                "stop_reason": "end_turn",
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "tokens_cache_read": cache_read,
                "tokens_cache_write": cache_write,
                "tokens_total": tokens_in + tokens_out + cache_read + cache_write,
                "cache_hit_ratio": cache_read / max(1, tokens_in + cache_read),
                "cost_input": round(ci, 8),
                "cost_output": round(co, 8),
                "cost_cache_read": round(ccr, 8),
                "cost_cache_write": round(ccw, 8),
                "cost_total": round(cost_total, 8),
                "cost_api_reported": 0.0,
                "has_thinking": random.random() < 0.6,
                "has_tool_calls": random.random() < 0.7,
                "tool_names": None,
                "content_length": random.randint(50, 5000),
                "is_error": False,
                "source_file": "dummy:purged-session-backfill",
            })
            running_cost += cost_total

        # Scale costs to exactly match the gap
        if running_cost > 0:
            scale = gap / running_cost
            for r in records:
                r["cost_input"] = round(r["cost_input"] * scale, 8)
                r["cost_output"] = round(r["cost_output"] * scale, 8)
                r["cost_cache_read"] = round(r["cost_cache_read"] * scale, 8)
                r["cost_cache_write"] = round(r["cost_cache_write"] * scale, 8)
                r["cost_total"] = round(
                    r["cost_input"] + r["cost_output"] + r["cost_cache_read"] + r["cost_cache_write"], 8
                )

        actual_total = sum(r["cost_total"] for r in records)
        print(f"  Generated {len(records)} dummy records, total cost: ${actual_total:.6f}")

        # Insert in batches
        cols = [
            "call_id", "session_id", "parent_id", "timestamp", "timestamp_iso",
            "api", "provider", "model", "stop_reason",
            "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write",
            "tokens_total", "cache_hit_ratio",
            "cost_input", "cost_output", "cost_cache_read", "cost_cache_write",
            "cost_total", "cost_api_reported",
            "has_thinking", "has_tool_calls", "tool_names", "content_length",
            "is_error", "source_file",
        ]
        insert_sql = f"INSERT INTO records ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"

        for r in records:
            cur.execute(insert_sql, tuple(r[c] for c in cols))

        conn.commit()
        print(f"  Inserted {len(records)} dummy Anthropic records")

    # --- Step 3: Verify final totals ---
    print("\n--- Final verification ---")
    cur.execute("""
        SELECT provider, COUNT(*), ROUND(SUM(cost_total)::numeric, 6)
        FROM records GROUP BY provider ORDER BY provider
    """)
    grand_total = 0.0
    for provider, count, cost in cur.fetchall():
        cost = float(cost or 0)
        grand_total += cost
        print(f"  {provider}: {count} records, ${cost:.6f}")

    print(f"\n  DB Total: ${grand_total:.6f}")
    print(f"  Ground Truth: ${ANTHROPIC_TOTAL_USED + MINIMAX_TOTAL_USED + MOONSHOT_TOTAL_USED:.2f}")
    print(f"  Difference: ${abs(grand_total - (ANTHROPIC_TOTAL_USED + MINIMAX_TOTAL_USED + MOONSHOT_TOTAL_USED)):.6f}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
