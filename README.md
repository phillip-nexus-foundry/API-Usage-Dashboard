# API Usage Dashboard

Real-time token usage and cost tracking for all LLMs used via OpenClaw.

## Quick Start

```batch
PowerShell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/start-dashboard.ps1 -OpenBrowser
```

Dashboard runs at **http://127.0.0.1:8050**

## Windows / WSL Migration Setup

Run the dashboard on Windows so it can reach:

- browser CDP at `127.0.0.1:9222`
- `C:\Users\Agents\.claude\.credentials.json`
- `C:\Users\Agents\.codex\sessions`

while still reading OpenClaw session JSONL files from WSL over:

- `\\wsl.localhost\Ubuntu-24.04\home\agents\openclaw-local\core\agents\main\sessions`

Versioned helper scripts live in `scripts/windows/`:

- `start-dashboard.ps1`
- `stop-dashboard.ps1`
- `install-autostart.ps1`

To install background auto-start at logon and create desktop launchers:

```batch
PowerShell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/install-autostart.ps1
```

## How to Run

| Method | Command | Use When |
|--------|---------|----------|
| **Persistent (recommended)** | `PowerShell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/start-dashboard.ps1` | Normal Windows use |
| **One-shot** | `python app.py` | Debugging â€” exits on crash |
| **Via uvicorn** | `python -m uvicorn app:app --host 127.0.0.1 --port 8050` | Development |

### First-time setup
```batch
pip install -r requirements.txt
```

## Architecture

- **Parser:** Reads OpenClaw JSONL session files from `/home/agents/openclaw-local/core/agents/main/sessions/`
- **Database:** SQLite (`dashboard.db`) â€” auto-created, incremental updates via mtime tracking
- **Server:** FastAPI + static files (Chart.js dark-theme dashboard)
- **File watcher:** Auto-rescans when session files change (debounced 1s)
- **Balance checker:** Probes Anthropic/Moonshot APIs for rate limits on startup

## Runtime Requirements For Live Polling

- Claude Code/Codex/console scraping requires CDP access on `http://127.0.0.1:9222`.
- If the app runs in WSL and Brave/Chrome runs in Windows, CDP at `127.0.0.1:9222` is not reachable from WSL by default.
- For live polling (not stale fallback), run the dashboard in the same runtime context that can reach:
  - browser CDP (`127.0.0.1:9222`)
  - provider APIs (Moonshot, ElevenLabs/OpenAI where applicable)
- Required config/env alignment:
  - `config.yaml -> sessions_dir` must point to a real path for the runtime (Windows path when running on Windows).
  - `MOONSHOT_API_KEY`, `XI_API_KEY`/`ELEVENLABS_API_KEY`, and other provider keys must exist in that runtime environment.

## March 2026 Change Log (Codex)

- Added a stronger `/api/refresh` flow in `app.py`:
  - reload config
  - rescan telemetry
  - poll all providers
  - recompute balances
  - return structured refresh metadata (`polled`, `polled_providers`, `balance_providers`, `refresh_version`).
- Changed startup behavior in `app.py`:
  - initial scan now runs in a background task instead of blocking FastAPI startup.
- Fixed verified balance calibration logic in `balance/checker.py`:
  - `verified_usage_cost` is treated as baseline + incremental usage since `verified_usage_date + 1 day`.
  - applies to both provider-level and project-level balances.
- Updated frontend `static/dashboard.js`:
  - no-cache fetch helpers for live cards
  - resource poll now refetches `/api/resources` and `/api/balance`
  - filtered reload now refreshes resources too
  - refresh button handles `/api/refresh` status and errors explicitly.
- Merged Rainmeter migration approach for usage-value sourcing (no card UI changes):
  - Added `balance/usage_windows.py` shared helpers for reset/time percent formatting.
  - Added `balance/providers/claude_oauth_usage.py`:
    - Claude usage windows now support OAuth usage API sourcing (`usage_source_mode: auto|rainmeter_port|cdp`).
  - Added `balance/providers/codex_quota_usage.py`:
    - Codex 5h/7d usage now reads local session JSONL `token_count.rate_limits` (Rainmeter parity).
  - `balance/poller.py` now:
    - supports `usage_source_mode` and `usage_allow_fallback`
    - defaults to non-stale behavior (`usage_allow_fallback: false`)
    - keeps existing payload keys used by Resource Availability cards
    - uses direct DevTools WebSocket evaluation for generic provider page scraping (`_cdp_scrape_provider_sync`) to avoid Playwright subprocess failures for balance pages.
- `app.py` and modular `dashboard/presentation/routes/resources.py` now avoid implicit stale/manual fallback values unless explicitly enabled.
  - Removed derived Anthropic spend math (`spend_limit - extra_usage_balance`) because it can produce incorrect spend values; spend fields must come from real source payloads.
- Restored ElevenLabs key sourcing support:
  - `XI_API_KEY` / `ELEVENLABS_API_KEY` env vars
  - `config.yaml -> elevenlabs_api_key`
  - `config.yaml -> balance.elevenlabs.api_key`
- Moonshot API handling now tries both known balance endpoints for compatibility:
  - `https://api.moonshot.ai/v1/users/me/balance`
  - `https://api.moonshot.cn/v1/users/me/balance`
- Claude usage CDP path moved off Playwright subprocesses to direct DevTools WebSocket evaluation to avoid `WinError 5` failures.
  - Claude parser now captures spend/reset and extra-usage balance from the usage page block (`$X spent`, monthly limit, and nearby extra balance values).
- Moonshot parser changes:
  - Missing balance fields are now treated as API failure (not implicit `0.0`).
  - Preferred field order favors available/remaining balances over raw balance fields.
  - Balance parser now coerces formatted currency strings (for example `US$ 1.17`) so live API values are not dropped and replaced by fallback `0` fields.
- Moonshot ledger editing policy:
  - Manual Moonshot ledger mutations are now blocked server-side (`/api/balance/topup`, `/api/balance/topup/delete`).
  - Provider checks are normalized (`strip().lower()`), so Moonshot edits are blocked regardless of input casing/spacing.
  - Moonshot add/delete controls are hidden in the dashboard UI.
  - Moonshot is intended to be tracked from live API balance only.
  - Accidental test entries (`$1.23`, note `blocked`) were removed from `config.yaml` Moonshot project ledger.
  - Added `allow_stale_window_scrape` guard in `app.py` (default disabled) so stale snapshots do not overwrite live usage windows.
  - `balance/checker.py` now supports scraped-balance fallback (non-zero only) when API calls fail, preserving correct status computation.

## Final Bug Fixes (2026-03-05)

### Added

- Moonshot API compatibility in modular provider (`dashboard/application/providers/moonshot.py`):
  - tries both endpoints (`api.moonshot.ai` and `api.moonshot.cn`)
  - supports env/config key resolution (`MOONSHOT_API_KEY`, `balance.moonshot.api_key`, `moonshot_api_key`)
  - robust numeric parsing for balance fields (including formatted strings)
- Moonshot API-key fallback in legacy checker (`balance/checker.py`) to match modular behavior.
- Final Moonshot API consistency pass in `app.py`:
  - when provider source is `api`, single-project Moonshot values are synced to provider-level remaining before `/api/balance` returns.

### Changed

- Balance merge logic in `app.py` now preserves API balances:
  - API-backed providers are no longer overwritten by browser snapshot balances.
- Moonshot card rendering in `static/dashboard.js`:
  - when Moonshot is API-backed, the Balance Tracker card and project selector display API remaining consistently.
- Moonshot project ledger data in `config.yaml` corrected:
  - restored missing second `$20` top-up
  - both `$20` top-ups dated `2026-02-14`
  - free voucher `$5` dated `2026-02-14`
  - `$50` top-up dated `2026-02-23`
- Refresh metadata version updated to `2026-03-05-codex-moonfix`.

### Removed

- Removed the temporary mismatch between provider-level and single-project Moonshot remaining that caused a 10x display discrepancy (for example `0.53` vs `5.3`).
- Removed temporary internal debug marker used during diagnosis (`_sync_hit`) after verification.

### Clarification on Polling Failures

- Polling is isolated per provider in `BalancePoller.poll_all`: one provider scrape failure is caught and converted to an error snapshot; it does not abort polling for later providers.
- Claude/Codex usage collection was moved away from Playwright subprocess dependency toward direct CDP/WebSocket paths to reduce `WinError 5`/subprocess-related failures.

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | Main server â€” all API endpoints |
| `config.yaml` | Pricing, rate limits, server config |
| `parsers/openclaw_reader.py` | JSONL parser + SQLite writer |
| `parsers/telemetry_schema.py` | TelemetryRecord dataclass + cost math |
| `static/` | Frontend (HTML/JS/CSS) |
| `balance/` | API balance + rate limit probing |
| `balance/providers/` | Rainmeter-port usage sources (Claude OAuth + Codex local sessions) |
| `balance/usage_windows.py` | Shared reset/percentage helpers for usage windows |
| `evals/` | Automated evaluation checks |

## Notes

- Port `8050` is the default (configurable in `config.yaml`)
- The persistent runner restarts after 5 seconds on crash
- Historical crash cause: MemoryError on large queries â€” now handled gracefully with gc.collect() fallback
