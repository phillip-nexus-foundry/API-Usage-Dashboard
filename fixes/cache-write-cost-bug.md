# Cache Write Cost Calculation Bug - Analysis and Fix Required

**Date:** February 27, 2026  
**Reporter:** Phillip (sonicstriker11)  
**Status:** ✅ RESOLVED — Delta logic validated against ground-truth (Feb 27, 2026: $0.04 discrepancy on $14+ spend). Override removed.  
**File Location:** `fixes/cache-write-cost-bug.md`

---

## Problem Summary

The API-Usage-Dashboard is showing incorrect remaining balance for Anthropic API usage:
- **Dashboard shows:** $2.36 remaining of $20.00 (spent: $17.64)
- **Anthropic site shows:** $10.79 remaining of $20.00 (spent: $9.21)
- **Discrepancy:** ~$8.43 overcharge

---

## Root Cause Analysis

### Issue: Incorrect Cache Write Cost Calculation

The `cacheWrite` field in OpenClaw's telemetry data represents **cumulative cache size**, not tokens written in that specific call. However, the dashboard is charging the full cache write price on every single API call.

### Evidence from Database

Sample Opus calls showing the problem:

| Timestamp | Input | Output | CacheRead | CacheWrite | Cost | CacheWrite Cost |
|-----------|-------|--------|-----------|------------|------|-----------------|
| 1771993061948 | 3 | 164 | 0 | 36,097 | $0.689 | $0.686 |
| 1771993115947 | 3 | 2,348 | 0 | 50,372 | $1.121 | $1.071 |
| 1771994441785 | 3 | 1,209 | 0 | 144,394 | $2.798 | $2.775 |

**Key Observations:**
1. CacheWrite values are cumulative (36K -> 50K -> 144K tokens)
2. Each call is being charged for the full cumulative cache size
3. Anthropic only charges for cache writes when content is first cached
4. The dashboard is repeatedly charging for the same cached content

### Impact Calculation

From `analyze_discrepancy.py`:
- Total cache_write costs charged: **$10.05**
- This explains most of the $8.43 discrepancy
- The remaining difference may be from other miscalculations or timing issues

---

## Current Code State

### File: `parsers/telemetry_schema.py`

The `MODEL_COSTS` dictionary defines pricing (including non-zero Anthropic `cache_write`).

The old `compute_dollar_cost()` applied raw `tokens_cache_write` directly, which overcharges when telemetry is cumulative.

---

## Proposed Solution (Implemented)

### Selected Approach: High-Watermark Cache Write Billing
Calculate billable cache_write against a running maximum per `(session_id, model)`:
- First observed value -> bill full observed value
- If current value exceeds previous max -> bill only the increase (`current - max_seen`)
- If value is below or equal to max_seen -> bill $0

This avoids repeated charges after counter drops/resets and better matches cumulative-counter telemetry patterns.

### Why This Approach
1. Matches observed telemetry behavior (monotonic cumulative counters).
2. Avoids false charges on first observed records, which may already include historical cached content.
3. Still charges positive growth after baseline.
4. Avoids aggressive undercharging from setting cache_write to $0 globally.

---

## Implementation Details

### 1) Ingest Parser Update (`parsers/openclaw_reader.py`)
- Added in-memory cache-write state keyed by `(session_id, model)` during file parse.
- For Anthropic models:
  - First value: billable = full observed value
  - New high: billable = increment above previous high-watermark
  - Same/decrease: billable = 0
- Raw telemetry `tokens_cache_write` is still stored unchanged.
- Cost components are now computed with explicit per-component pricing math.

### 2) Shared Cost Logic (`parsers/telemetry_schema.py`)
- Added `compute_cost_breakdown(...)` returning:
  - `cost_input`, `cost_output`, `cost_cache_read`, `cost_cache_write`, `cost_web_surcharge`, `cost_total`
- Added `is_anthropic_model(...)` helper.
- Added `anthropic_billable_cache_write_delta(...)` helper (high-watermark semantics).
- `compute_dollar_cost(...)` now delegates to `compute_cost_breakdown(...)`.

### 3) Historical Data Fix Script (`fix_cache_write_costs.py`)
- Rewritten to backfill Anthropic records ordered by:
  - `session_id`, `model`, `timestamp`, `call_id`
- Applies the same delta logic as ingestion.
- Uses high-watermark logic consistent with ingest behavior.
- Updates all cost columns (`cost_input`, `cost_output`, `cost_cache_read`, `cost_cache_write`, `cost_total`).

### 4) Balance Reconciliation Override (`balance/checker.py` + `config.yaml`)
- Added optional provider-level override support for ledger providers:
  - `verified_usage_cost` (exact provider-verified spend value)
  - `usage_cost_adjustment` (offset to computed spend)
- Anthropic now uses:
  - `balance.anthropic.verified_usage_cost: 9.21`
- Balance checker returns both:
  - `cumulative_cost` (effective value used for remaining balance)
  - `raw_cumulative_cost` (computed from DB)
  - `cost_source` (`verified_override`, `computed_plus_adjustment`, or `computed`)

### 5) Runtime Config Reload Fix (`app.py`)
- `/api/balance` now reloads `config.yaml` on each call.
- This ensures edited reconciliation values apply immediately without stale in-memory config.

---

## Files Modified

1. **`parsers/telemetry_schema.py`**
   - Added cost breakdown helper and Anthropic model helper
   - Kept pricing table intact (no blanket `cache_write=0` workaround)

2. **`parsers/openclaw_reader.py`**
   - Added delta-based billable cache-write logic during parsing
   - Switched from proportional component allocation to explicit component pricing

3. **`fix_cache_write_costs.py`**
   - Updated to recalculate historical Anthropic data with delta logic

4. **`balance/checker.py`**
   - Added exact-cost reconciliation fields (`verified_usage_cost`, `usage_cost_adjustment`)
   - Added `cost_source` and `raw_cumulative_cost` outputs

5. **`app.py`**
   - Added config reload before balance computation

6. **`config.yaml`**
   - Set Anthropic `verified_usage_cost: 9.21`

---

## Test Cases

1. **First observed call in stream:** Charges full observed cache_write
2. **Consecutive calls with increasing cache_write:** Charges only the delta
3. **Call with decreasing cache_write:** Charges $0 (cache reset/invalidation)
4. **Call with same cache_write:** Charges $0 (no new writes)
5. **Cross-session calls:** Tracking resets per session
6. **Cross-model calls in same session:** Tracking isolated per model

---

## Additional Notes

1. This implementation assumes OpenClaw ordering within each parsed stream is chronological.
2. The backfill script enforces deterministic order using `session_id, model, timestamp, call_id`.
3. If OpenClaw changes telemetry semantics in future versions, this logic should be revisited.
4. Validation snapshot after third pass backfill (before reconciliation override):
   - Anthropic total: **$9.79**
   - Expected remaining from $20 preload: **$10.21**
   - Closer to Anthropic site ($10.79 remaining) than prior passes, but still not exact.
5. Final operational state (with verified override active):
   - Anthropic cumulative cost used for balance: **$9.21**
   - Anthropic remaining shown by dashboard: **$10.79**
   - `cost_source`: **`verified_override`**

### User-Provided Opus Aggregates (for future reconciliation)
These totals reflect all opus usage BEFORE 2/27/26:
- Total tokens in: **1,361,303** 
- Total tokens out: **15,689**
- Source: Claude dashboard aggregate totals shared by user on February 27, 2026.

Total today now (2/27/26):
Total tokens in
4,868,674
Total tokens out
31,678

---

## Expected Outcome After Fix

- Dashboard remaining balance matches Anthropic site exactly when `verified_usage_cost` is provided
- Current configured value: **$10.79 remaining of $20.00**
- Cost calculation should be consistent and explainable
- New ingested data and historical backfilled data should follow the same rules
