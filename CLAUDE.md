# API Usage Dashboard - Project Instructions

## CRITICAL: No Static Overrides to Fake Working Features

**Date documented: 2026-03-01**
**Incident:** Claude attempted to "fix" the Resource Availability cards (Claude Code 5hr/1wk windows, Codex 5hr/1wk windows) by inserting static `resource_overrides` values into config.yaml using the exact ground-truth numbers the user provided. This made the dashboard APPEAR correct while fixing NOTHING. The values would go stale immediately and the underlying data pipeline was never addressed.

**This is absolutely unacceptable and must NEVER happen again.**

### Rules for ground-truth values provided by the user:

1. User-provided values are for **EVALUATION and CALIBRATION ONLY** - to verify whether the system is computing correctly, NOT to be hardcoded as display values.
2. If after making changes the dashboard still shows wrong data, **report the failure honestly** to the user. Do not mask it.
3. Consider alternative approaches, research the problem deeper, look at open-source projects for reference implementations, and keep iterating until the actual data pipeline works.
4. Never substitute a static config hack for a real fix to a broken data pipeline.
5. The `resource_overrides` section added to config.yaml on 2026-03-01 is a band-aid that must be replaced with real-time data flow (browser scraping, API polling, or telemetry computation).

### What actually needs to happen for Resource Availability:

- Claude Code and Codex usage windows (5hr, 1wk) are **provider-managed rate limits** that cannot be derived from local telemetry records alone.
- The dashboard needs a **real data source** for these values: browser scraping (Playwright), an API endpoint, or another live mechanism.
- The balance_poller (browser scraping via Playwright) was designed for this purpose but may not be fully wired up.
- The fix is to make the scraping pipeline actually work end-to-end, not to paste numbers into config.

### For balance calibration (verified_usage_cost):

- `verified_usage_cost` in config.yaml IS a legitimate calibration mechanism for dollar balances where historical telemetry was lost (e.g., OpenClaw purged old session files). This is acceptable because:
  - It sets a known-good baseline, and incremental costs from new API calls are tracked accurately going forward.
  - It is clearly documented as a calibration override with a date stamp.
- This is fundamentally different from faking resource window percentages, which change in real-time and need a live data source.

## General Development Principles

- Do not over-engineer, but do not fake results either.
- If a feature cannot be made to work in the current session, say so clearly and document what remains.
- The user is cost-conscious with limited API tokens and rate limits. Wasting those on fake fixes is unacceptable.

## 2026-03-05 Moonshot Balance Consistency Note

- Incident: Moonshot provider-level API balance was correct, but single-project display value drifted (10x mismatch observed).
- Root cause: mixed data sources in response/rendering path (API remaining vs computed project remaining).
- Required invariant: when Moonshot `balance_source` is `api`, single-project remaining must be synchronized to provider-level remaining for both API response and UI card rendering.
- Keep API values authoritative; do not let browser-scraped or computed project balances overwrite API-backed Moonshot values.
