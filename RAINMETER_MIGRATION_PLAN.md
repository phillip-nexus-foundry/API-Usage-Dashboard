# Rainmeter Migration Plan: API-Usage-Dashboard

## 1) Goal and Scope
Migrate Claude Code and Codex CLI usage-window metrics from browser/CDP scraping to Rainmeter-equivalent logic ported into the dashboard codebase.

In scope:
- Claude Code usage windows + spend/extra values (currently scraped from `claude.ai/settings/usage`)
- Codex CLI usage windows (currently scraped from `chatgpt.com/codex/settings/usage`)
- Keep existing `/api/resources` display contract for these providers

Out of scope:
- MiniMax and Anthropic billing balance scraping removal (must remain browser/CDP-based per requirement)
- Moonshot and ElevenLabs behavior changes

## 2) Current Architecture Analysis (API-Usage-Dashboard)
Project path: `/mnt/c/Users/AI-Agents/.openclaw/projects/API-Useage-Dashboard`

### Runtime wiring
- Entry point: `run.py` -> app factory `dashboard/app.py`.
- `dashboard/app.py` initializes legacy `BalancePoller` (`balance/poller.py`) and schedules periodic polling.
- `BalancePoller.poll_all()` writes snapshots into `resource_snapshots` (SQLite file `data/dashboard.db`).
- `/api/resources` (modular route: `dashboard/presentation/routes/resources.py`) reads:
  - usage telemetry from SQLAlchemy DB (`records`) for computed windows
  - latest resource snapshots from `BalancePoller.get_latest_snapshots()` for scraped windows/extra/spend values

### Current CDP-dependent paths
- `balance/poller.py::poll_anthropic()`
  - Calls `_poll_provider_page()` for Anthropic billing balance (CDP)
  - Calls `_poll_claude_usage_page()` for Claude usage windows/spend/extra (CDP)
- `balance/poller.py::poll_codex_cli()`
  - OpenAI billing API attempt
  - CDP scrape via `_cdp_scrape_codex_sync()` for 5h/weekly remaining
  - Config fallback
- `balance/poller.py::poll_minimax()` remains CDP/browser based

### Current frontend/resource contract to preserve
`/api/resources` expects for Claude/Codex:
- Anthropic snapshot payload under `raw_payload.claude_usage`:
  - `plan_usage_pct`, `weekly_pct`, `plan_usage_reset`, `weekly_reset`
  - `spend_used`, `spend_limit`, `spend_reset_text`, `extra_usage_balance`
- Codex snapshot payload under `raw_payload.codex_usage`:
  - `five_hour_remaining_pct`, `weekly_remaining_pct`, `weekly_reset`

## 3) Rainmeter Architecture Analysis (Source Implementation)
Project path: `/mnt/c/Users/AI-Agents/.openclaw/workspace/projects/rainmeter-public`

### QuotaHud (Claude) source of truth
- Script: `@Resources/Fetch-QuotaData.ps1`
- Reads OAuth tokens from `~/.claude/.credentials.json`
- Refreshes token at `<60s` expiry via `https://console.anthropic.com/v1/oauth/token`
- Calls usage endpoint: `https://api.anthropic.com/api/oauth/usage`
- Calls profile endpoint: `https://api.anthropic.com/api/oauth/profile`
- Exposes tiers:
  - `FIVE_HOUR` (5h utilization)
  - `SEVEN_DAY` (7d utilization)
  - `SEVEN_DAY_SONNET` (7d sonnet bucket)  (NOT NEEDED DO NOT IMPLEMENT THIS)
  - `EXTRA` (extra usage utilization)
- Exposes reset and time-progress helpers:
  - `*_RESET` (human-readable reset countdown)
  - `*_TIMEPCT` (elapsed time progress through bucket window)

### CodexQuota source of truth
- Script: `@Resources/Fetch-CodexQuota.ps1`
- Reads latest `.jsonl` session under `~/.codex/sessions`
- Parses `event_msg` payloads with `payload.type == token_count`
- Extracts `payload.rate_limits`:
  - global stream (`limit_id == codex`)
  - model stream (`limit_id != codex`)
- From each stream, uses:
  - `primary` bucket => 5h
  - `secondary` bucket => 7d
- Exposes `used_percent`, `resets_at`, `window_minutes` plus reset/time helper output

## 4) Target Design
### Design principles
- Port Rainmeter logic into Python modules (do not shell out to PowerShell at runtime).
- Keep snapshot payload shape backward-compatible for UI and existing fallback tooling.
- Restrict browser/CDP to balance-only scraping where required (MiniMax + Anthropic billing balance).

### New data flow
1. `poll_anthropic()`:
   - Keep `_poll_provider_page()` for Anthropic billing balance (CDP).
   - Replace `_poll_claude_usage_page()` with OAuth API flow ported from `Fetch-QuotaData.ps1`.
   - Map API response into existing `raw_response["claude_usage"]` keys.
2. `poll_codex_cli()`:
   - Replace CDP scrape path with local Codex session parser ported from `Fetch-CodexQuota.ps1`.
   - Map extracted global 5h/7d used% to existing remaining% fields:
     - `five_hour_remaining_pct = 100 - global_5h_used_pct`
     - `weekly_remaining_pct = 100 - global_7d_used_pct`
   - Keep optional model bucket details as extra keys for future UI.
3. `poll_minimax()`:
   - No change (browser/CDP remains).

## 5) Implementation Plan (Phased)
### Phase 0: Baseline and guardrails
1. Capture current payload samples from `resource_snapshots` for `anthropic` and `codex_cli`.
2. Add feature flag `usage_source_mode` with values:
   - `cdp` (current behavior)
   - `rainmeter_port` (new behavior)
   - `auto` (prefer new behavior, fallback to config)
3. Default to `auto` in development only; production rollout starts with `cdp` then flips.

### Phase 1: Extract shared usage-window utilities
Create `balance/usage_windows.py`:
- `format_reset_from_iso()` and `format_reset_from_epoch()`
- `compute_timepct_from_iso(window_minutes)`
- `compute_timepct_from_epoch(window_minutes)`
- `clamp_percent()`

These are direct Python ports of Rainmeter helper logic (`Format-Reset`, `Get-TimePct`, percent clamping).

### Phase 2: Claude OAuth usage client (Rainmeter parity)
Create `balance/providers/claude_oauth_usage.py`:
- `load_claude_credentials(path)`
- `refresh_oauth_if_needed(credentials)`
- `fetch_usage(access_token)`
- `fetch_profile(access_token)`
- `map_to_dashboard_payload(usage, profile)`

Output payload (must include existing keys):
- `plan_usage_pct` <- `five_hour.utilization`
- `plan_usage_reset` <- formatted from `five_hour.resets_at`
- `weekly_pct` <- `seven_day.utilization`
- `weekly_reset` <- formatted from `seven_day.resets_at`
- `spend_used`, `spend_limit`, `spend_reset_text`, `extra_usage_balance` (from config fallback merge rules if API lacks them)
- Additional optional keys for observability:
  - `seven_day_sonnet_pct`, `seven_day_sonnet_reset`
  - `plan_usage_time_pct`, `weekly_time_pct`, `seven_day_sonnet_time_pct`
  - `source = "oauth_usage_api"`

### Phase 3: Codex local quota parser (Rainmeter parity)
Create `balance/providers/codex_quota_usage.py`:
- `resolve_codex_sessions_root()` (default `~/.codex/sessions`, env override)
- `find_latest_session_jsonl()`
- `iter_token_count_events()`
- `extract_latest_rate_limits(global/model)`
- `map_to_dashboard_payload()`

Mapping for backward compatibility:
- `five_hour_remaining_pct = 100 - GLOBAL_5H_USED`
- `weekly_remaining_pct = 100 - GLOBAL_7D_USED`
- `weekly_reset` from global 7d reset formatting

Include extra fields (safe additions):
- `global_5h_used_pct`, `global_7d_used_pct`, `global_5h_reset`, `global_7d_reset`
- `model_name`, `model_5h_used_pct`, `model_7d_used_pct`
- `*_time_pct`
- `source = "local_codex_sessions"`

### Phase 4: Integrate into BalancePoller
Update `balance/poller.py`:
1. `poll_anthropic()`:
   - Keep existing Anthropic balance scraping call.
   - Replace `_poll_claude_usage_page()` invocation with new Claude OAuth client call.
   - Preserve fallback merge behavior from `claude_usage_fallback`.
2. `poll_codex_cli()`:
   - Remove CDP dependency branch for usage windows.
   - Use new Codex local parser as primary.
   - Keep `codex_usage_fallback` merge for missing values.
3. Keep MiniMax and Anthropic balance scraping untouched.
4. Mark `balance_source` values clearly:
   - Anthropic snapshot remains balance-source from billing scrape; add usage source in payload.
   - Codex snapshot `balance_source` can be `local_sessions`.

### Phase 5: Resource route compatibility updates
Update `dashboard/presentation/routes/resources.py` (and legacy `app.py` endpoint if still used):
- Continue reading same existing keys first.
- Optionally read new reset/time fields for better reset labels.
- Do not change response JSON schema expected by `static/dashboard.js`.

### Phase 6: Rollout and cleanup
1. Deploy with `usage_source_mode=cdp` (no behavior change).
2. Enable `usage_source_mode=auto` and log source chosen per provider.
3. After stability window, set `usage_source_mode=rainmeter_port`.
4. Remove obsolete Codex/Claude CDP usage-scrape helpers:
   - `_poll_claude_usage_page`, `_cdp_scrape_claude_sync`, `_cdp_scrape_codex_sync`, `_extract_codex_usage`, `_extract_claude_usage` (only after verification).

## 6) Concrete File Changes
### New files
- `balance/usage_windows.py`
- `balance/providers/__init__.py`
- `balance/providers/claude_oauth_usage.py`
- `balance/providers/codex_quota_usage.py`
- `tests/balance/test_usage_windows.py`
- `tests/balance/test_claude_oauth_usage_mapping.py`
- `tests/balance/test_codex_quota_usage_mapping.py`

### Modified files
- `balance/poller.py`
- `dashboard/presentation/routes/resources.py`
- `dashboard/presentation/routes/system.py` (optional debug surface for usage source)
- `dashboard/app.py` (if config wiring for new flags/paths is needed)
- `config.yaml` (new keys below)
- `README.md` (operational docs)

### Optional parity file
- `app.py` (legacy monolith) if it is still run in any environment.

## 7) Config Additions
Add to `config.yaml`:
- `usage_source_mode: auto|cdp|rainmeter_port`
- `claude_oauth_credentials_path` (default `~/.claude/.credentials.json`)
- `codex_sessions_path` (default `~/.codex/sessions`)
- `usage_poll_timeout_seconds` (default `30`)

Keep existing fallback blocks unchanged:
- `claude_usage_fallback`
- `codex_usage_fallback`

## 8) Dependencies
Python package dependencies:
- No mandatory new third-party dependencies if implemented with existing stack (`httpx`, stdlib JSON/pathlib/datetime).
- Keep `playwright` dependency because MiniMax and Anthropic billing balance scraping still require browser/CDP.

Runtime prerequisites:
- Read/write access to Claude credentials file for token refresh.
- Read access to Codex session JSONL directory.

## 9) Testing and Validation
### Unit tests
- Utility parity tests for reset/time-percent/clamping logic.
- Mapping tests:
  - Claude API response -> `claude_usage` payload keys.
  - Codex rate limits -> `codex_usage` remaining percentages.
- Fallback tests when source data unavailable.

### Integration tests (manual/automated smoke)
1. Trigger `/api/resources/poll`.
2. Verify latest snapshot payloads:
   - Anthropic contains `raw_payload.claude_usage.plan_usage_pct` and `weekly_pct` from OAuth API path.
   - Codex contains `raw_payload.codex_usage.five_hour_remaining_pct` and `weekly_remaining_pct` from local sessions path.
3. Verify `/api/resources` unchanged card rendering for Claude/Codex windows.
4. Verify MiniMax balance polling still works via browser scraping.

### Acceptance criteria
- No CDP dependency for Claude/Codex usage windows.
- Anthropic and MiniMax balances still scraped successfully.
- Frontend cards render unchanged without JS modifications.
- Poll stability improves (no browser-tab growth from Claude/Codex usage polling).

## 10) Risks and Mitigations
- OAuth schema/header drift (Anthropic beta version changes):
  - Mitigation: isolate header/version constant and log explicit API errors.
- Credential file write failures on refresh:
  - Mitigation: atomic write (`tmp` + replace), fallback to existing token if valid.
- Codex session format drift:
  - Mitigation: tolerant parser + fallback + source-status telemetry.
- Dual app paths (modular `dashboard/*` and legacy `app.py`) diverging:
  - Mitigation: implement in modular path first, patch legacy only if still used, document canonical startup (`run.py`).

## 11) Rollback Plan
- Immediate rollback: set `usage_source_mode=cdp`.
- Keep old CDP helper code until 1-2 weeks of stable operation under `rainmeter_port`.
- Preserve fallback config values as final safety net.

## 12) Execution Order (Recommended)
1. Add shared utilities + tests.
2. Add Claude OAuth provider + tests.
3. Add Codex local parser + tests.
4. Integrate into `BalancePoller` behind flag.
5. Validate `/api/resources` parity.
6. Roll out with `auto`, observe logs, then force `rainmeter_port`.
7. Remove obsolete CDP usage-scrape code.
