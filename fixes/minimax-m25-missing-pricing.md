# MiniMax M2.5 Missing Pricing Fix

**Date:** February 27, 2026  
**Status:** ✅ RESOLVED  
**Impact:** Dashboard underreported MiniMax spend by ~$0.19 ($0.07 computed vs $0.26 actual)

---

## Problem

The API-Usage-Dashboard showed MiniMax remaining balance as **$24.93** while the actual MiniMax online balance was **$24.74** — a ~$0.19 discrepancy across only 39 calls.

## Root Cause

`MiniMax-M2.5` had **no entry** in `config.yaml`'s `model_costs` section. The dashboard's cost calculator couldn't look up per-token pricing, so nearly all 39 MiniMax records were stored with `cost_total = $0.00`.

The ledger-based balance (`$25.00 deposit - $0.07 computed`) produced $24.93 instead of the correct $24.74.

## Fix Applied

### 1. Added MiniMax M2.5 pricing to `config.yaml`

```yaml
model_costs:
  MiniMax-M2.5:
    input: 0.3
    output: 1.2
    cache_read: 0.03
    cache_write: 0.375
```

**Source:** [MiniMax Official Pricing](https://platform.minimax.io/docs/guides/pricing-paygo)

| Rate | Per Million Tokens |
|------|-------------------|
| Input | $0.30 |
| Output | $1.20 |
| Cache Read | $0.03 |
| Cache Write | $0.375 |

### 2. Recalculated all 39 existing DB records

Ran `fix_minimax_costs.py` to update all `MiniMax-M2.5` rows in `dashboard.db` with correct per-token costs.

**Before:** Total computed cost = $0.07  
**After:** Total computed cost = $0.260779  
**Result:** Remaining = $25.00 - $0.26 = **$24.74** ✅ (exact match with online balance)

## Key Lesson

When adding a new model/provider to OpenClaw, **always add its pricing to the dashboard's `config.yaml` `model_costs` section** at the same time. The model name must match exactly (case-sensitive) — e.g., `MiniMax-M2.5`, not `minimax-m2.5`.

## Files Changed

- `config.yaml` — Added `MiniMax-M2.5` to `model_costs`
- `dashboard.db` — Recalculated 39 records via `fix_minimax_costs.py`
- `fix_minimax_costs.py` — One-time recalculation script (kept for reference)
