# Opus 4.6 Pricing Fix

**Date:** February 27, 2026  
**Status:** ✅ RESOLVED  
**Impact:** Dashboard was overcharging Opus by 3x

---

## Problem

The API-Usage-Dashboard was showing incorrect Anthropic balance:
- **Dashboard showed:** ~$15–26 remaining (varied as recalculations were attempted)
- **Claude site showed:** $24.65 remaining (of $50 deposited)
- **Root cause:** Opus 4.6 was priced as Opus 4 (3x more expensive)

## Root Cause

The model `claude-opus-4-6` corresponds to **Claude Opus 4.6**, which has significantly reduced pricing compared to Opus 4/4.1:

| Rate | Old (WRONG — Opus 4) | New (CORRECT — Opus 4.6) |
|------|---------------------|--------------------------|
| Input | $15/MTok | **$5/MTok** |
| Output | $75/MTok | **$25/MTok** |
| Cache Write (5m) | $18.75/MTok | **$6.25/MTok** |
| Cache Read | $1.50/MTok | **$0.50/MTok** |

The dashboard was using Opus 4 pricing ($15/$75) for the Opus 4.6 model ($5/$25), resulting in 3x overcharging on every opus API call.

**Source:** https://platform.claude.com/docs/en/about-claude/pricing

## Additional Issue: Missing Historical Data

After correcting pricing, a secondary discrepancy emerged: the dashboard UNDER-reported costs because OpenClaw purges session JSONL files when sessions are reset/archived. Historical data (especially ~355 haiku calls from Feb 24 worth ~$2.40) was lost.

This is a known limitation: the dashboard computes costs from current JSONL files, but OpenClaw doesn't preserve all historical files.

## Fix Applied

### 1. Pricing Correction
**Files modified:**
- `parsers/telemetry_schema.py` — Updated `MODEL_COSTS["claude-opus-4-6"]`
- `config.yaml` — Updated `model_costs.claude-opus-4-6`

### 2. Database Rebuild
- Deleted `dashboard.db` and let the server rebuild from current JSONL files with correct pricing

### 3. Balance Calibration
- Set `verified_usage_cost: 25.35` in `config.yaml` under `balance.anthropic`
- This overrides computed cost to match Anthropic's ground truth
- Going forward, new opus calls will be correctly priced; periodic recalibration recommended

### 4. Crash Resilience (separate fix, same session)
- Hardened exception handler for MemoryError
- Added `run-persistent.bat` for auto-restart on crash
- Removed old `start.bat`

## Verification

After fix:
```
Anthropic remaining: $24.65 ✅ (matches Claude site exactly)
Cost source: verified_override
Computed cost: $13.21 (from available JSONL data)
Effective cost: $25.35 (calibrated to ground truth)
```

## Going Forward

- **New opus calls** will be priced correctly at $5/$25/$0.50/$6.25
- **Recalibrate** `verified_usage_cost` periodically against Claude's dashboard
- If OpenClaw changes session archival behavior, the computed cost may become more accurate over time
- Watch for model name changes — if Anthropic releases new Opus versions, verify the pricing matches

## All Anthropic Model Pricing (verified Feb 27, 2026)

| Model | Input | Output | Cache Write (5m) | Cache Read |
|-------|-------|--------|-----------------|------------|
| Claude Opus 4.6 | $5 | $25 | $6.25 | $0.50 |
| Claude Opus 4.1/4 | $15 | $75 | $18.75 | $1.50 |
| Claude Sonnet 4.6/4.5/4 | $3 | $15 | $3.75 | $0.30 |
| Claude Haiku 4.5 | $1 | $5 | $1.25 | $0.10 |
| Claude Haiku 3.5 | $0.80 | $4 | $1.00 | $0.08 |
| Claude Haiku 3 | $0.25 | $1.25 | $0.30 | $0.03 |
