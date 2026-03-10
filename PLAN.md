# OpenClaw API Usage Dashboard - Implementation Plan

## Context

You need full granular visibility into token usage and API costs across all LLMs used via OpenClaw (Anthropic Claude models + Moonshot Kimi K2.5). OpenClaw already captures rich per-call telemetry in JSONL session files at `/home/agents/openclaw-local/core/agents/main/sessions/` (~656 files, ~2871 API calls). Rather than adding a heavyweight OTel+Grafana+Docker stack on top, we build a **lightweight Python FastAPI app** that reads this existing data directly and serves a Grafana-inspired dark-theme dashboard in the browser. This gives you a working MVP immediately with zero infrastructure overhead.

**Target:** `/home/agents/openclaw-local/core/projects/API-Useage-Dashboard`

---

## Architecture

```
OpenClaw Session JSONL files (already exist)
        |
        v
parsers/openclaw_reader.py  (reads + indexes .jsonl files)
parsers/telemetry_schema.py  (24-field TelemetryRecord dataclass)
        |
        v
dashboard.db  (SQLite - records + file_index tables, auto-created)
        |
        v
app.py  (FastAPI, 14 REST endpoints, serves static files)
  |-- balance/checker.py  (Anthropic/Moonshot balance + alerts)
  |-- evals/evaluator.py  (8 automated evaluations)
        |
        v
static/index.html + dashboard.js + style.css  (Chart.js dashboard)
```

Runs on `http://127.0.0.1:8050` - single `python` process, no Docker required.

---

## Files to Create (15 files)

### Phase 1: Core Parser

**1. `parsers/__init__.py`** - Empty init file

**2. `parsers/telemetry_schema.py`** - Canonical 24-field dataclass
- `TelemetryRecord` dataclass with: `call_id`, `session_id`, `parent_id`, `timestamp`, `timestamp_iso`, `api`, `provider`, `model`, `stop_reason`, `tokens_input`, `tokens_output`, `tokens_cache_read`, `tokens_cache_write`, `tokens_total`, `cache_hit_ratio`, `cost_input`, `cost_output`, `cost_cache_read`, `cost_cache_write`, `cost_total`, `has_thinking`, `has_tool_calls`, `tool_names`, `content_length`, `is_error`
- `MODEL_COSTS` dict with per-1M-token rates for all configured models
- `compute_dollar_cost()` function that independently calculates cost from token counts

**3. `parsers/openclaw_reader.py`** - JSONL session file parser
- `OpenClawReader` class that scans `/home/agents/openclaw-local/core/agents/main/sessions/`
- Parses each `.jsonl` and `.jsonl.reset.*` file line-by-line
- Extracts assistant messages with `usage` data into `TelemetryRecord` objects
- Stores parsed records in SQLite (`dashboard.db`) with two tables:
  - `records` â€” all 24 TelemetryRecord fields, indexed on `session_id`, `timestamp`, `provider`, `model`
  - `file_index` â€” tracks `(filepath, mtime, record_count)` for incremental updates
- On `scan()`: checks `file_index` mtimes, only re-parses changed/new files, upserts into `records`. Deleted files have their records removed from both tables.
- Provides `scan()`, `get_records(filters)`, `get_session_ids()`, `get_stats()` methods (backed by SQL queries)
- Session JSONL structure: line 0 is `type=session` header, subsequent lines are `type=message` with nested `message.usage` on assistant role entries

### Phase 2: API Layer

**4. `requirements.txt`**
- `fastapi`, `uvicorn[standard]`, `pyyaml`, `httpx`, `python-dateutil`, `aiosqlite`

**5. `config.yaml`** - Dashboard configuration
- Server host/port (127.0.0.1:8050)
- Sessions directory path
- Balance thresholds per provider (warn/critical dollar amounts)
- Initial deposit amounts for Anthropic (no balance API) and Moonshot
- Model cost table, notification settings, eval grade thresholds

**6. `app.py`** - FastAPI application (14 endpoints)

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Serve dashboard HTML |
| `GET /api/summary` | Aggregate KPIs: total calls, cost, tokens, error rate, by-provider, by-model |
| `GET /api/timeseries?interval=hour` | Time-bucketed data for line/bar charts |
| `GET /api/calls?page=1&per_page=50` | Paginated individual call list (all 24 fields) |
| `GET /api/sessions` | Session list with per-session aggregates |
| `GET /api/sessions/{id}` | Single session detail with all calls |
| `GET /api/models` | Per-model breakdown: calls, tokens, cost, error rate |
| `GET /api/tools` | Tool usage frequency breakdown |
| `GET /api/balance` | Provider balances + alert evaluation |
| `GET /api/evals` | Run 8 evaluations, return scores/grades |
| `GET /api/cost/daily` | Daily cost breakdown by provider/model |
| `GET /api/cost/projection` | Monthly projection from 7-day trailing average |
| `GET /api/config` | Model costs and settings (no secrets) |
| `POST /api/refresh` | Force full re-scan of session files |

### Phase 3: Dashboard UI

**7. `static/style.css`** - Dark theme (GitHub-dark palette)
- CSS variables: `--bg-primary: #0d1117`, `--bg-card: #161b22`, `--accent-blue: #58a6ff`
- CSS Grid layout with 12-column system
- Card components, KPI stat styling, table with sticky header, toast notifications
- Responsive: single column below 768px

**8. `static/index.html`** - Dashboard shell
- Loads Chart.js 4.4 + date adapter from CDN
- Grid layout with canvas elements for 12 chart panels + call log table
- Structure:

```
Row 1: 5 KPI cards (Total Calls | Total Cost | Error Rate | Cache Hit% | Sessions)
Row 2: Token Time Series (line) | Cost Over Time (stacked bar)
Row 3: By Provider (doughnut) | By Model (bar) | Stop Reasons (pie)
Row 4: Balance Gauges | Eval Scores (radar chart)
Row 5: Top Tools (horizontal bar) | Monthly Cost Projection (area)
Row 6: Full-width sortable/filterable/paginated call log table
```

**9. `static/dashboard.js`** - Vanilla JS, no build step
- Fetches all `/api/*` endpoints on load (parallel)
- Renders 12 Chart.js panels + HTML table
- 60-second auto-refresh timer
- Filter controls: provider, model, time interval (minute/hour/day)
- Table sorting (click column headers) and pagination
- In-page toast notifications for balance alerts

### Phase 4: Advanced Features

**10. `balance/__init__.py`** - Empty init file

**11. `balance/checker.py`** - Balance checking
- `BalanceChecker` class
- Moonshot: `GET https://api.moonshot.ai/v1/users/me/balance` with Bearer auth
- Anthropic: No public balance API, so computes `initial_deposit - cumulative_cost` from session data
- Compares against configurable warn/critical thresholds
- Windows desktop notifications via PowerShell `New-BurntToastNotification` (fallback: console print)

**12. `evals/__init__.py`** - Empty init file

**13. `evals/evaluator.py`** - 8 automated evaluations (no LLM calls needed)

| Eval | Metric | Scoring |
|------|--------|---------|
| Error Rate | errors/total_calls | A=<2%, B=<5%, C=<10%, D=<20% |
| Cache Efficiency | avg cache_hit_ratio | A=>70%, B=>50%, C=>30% |
| Cost Efficiency | cost per useful output token | Normalized per-model |
| Tool Utilization | % calls using tools | A=>50%, B=>30%, C=>15% |
| Abort Rate | aborted/total_calls | A=<2%, B=<5% |
| Output Density | output_tokens/total_tokens | Higher = more productive |
| Thinking Usage | % calls with reasoning | Model-aware scoring |
| Provider Diversity | fallback provider coverage | Are fallbacks tested? |

Each returns `EvalResult(eval_name, score 0-1, grade A-F, details, count, timestamp)`

### Phase 5: Packaging

**14. `start.bat`** - One-click launcher
- Auto-installs dependencies if missing (`pip install -r requirements.txt`)
- Launches uvicorn on 127.0.0.1:8050
- Opens browser automatically

**15. `docker-compose.yml`** - Optional Docker wrapper
- Default: just the FastAPI app container with read-only mount to `.openclaw/`
- `--profile full`: adds Prometheus + Grafana for users who want them later

---

## Key Data Source Details

**Session JSONL structure** (verified from actual files):
```jsonc
// Line 0: session header
{"type":"session","id":"064a7beb-...","timestamp":"2026-02-22T...","cwd":"..."}

// Subsequent lines: messages
{
  "type":"message",
  "id":"msg_...",
  "parentId":"msg_...",
  "timestamp":"...",
  "message": {
    "role": "assistant",
    "content": [
      {"type":"thinking","thinking":"...","thinkingSignature":"..."},
      {"type":"text","text":"..."},
      {"type":"toolCall","id":"tc_...","name":"Read","arguments":{...}}
    ],
    "api": "anthropic-messages",
    "provider": "anthropic",
    "model": "claude-opus-4-6",
    "usage": {
      "input": 264, "output": 320,
      "cacheRead": 16896, "cacheWrite": 0,
      "totalTokens": 17480,
      "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}
    },
    "stopReason": "stop",
    "timestamp": 1771138478699
  }
}
```

**Cost unit note:** OpenClaw's `cost` values in the config use per-billion-token units. The `cost` in session JSONL is already computed. For Moonshot models, costs show as 0 (free tier). `compute_dollar_cost()` independently recalculates from token counts as a cross-check.

---

## Verification Plan

1. **Parser test:** Run `python -c "from parsers.openclaw_reader import OpenClawReader; r = OpenClawReader(); r.scan(); print(f'{len(r.get_records())} records from {len(r.get_session_ids())} sessions')"` -- should show ~2871 records from ~656 sessions
2. **API test:** Start server, hit `http://127.0.0.1:8050/api/summary` -- should return JSON with total_calls, by_provider, by_model
3. **Dashboard test:** Open `http://127.0.0.1:8050` in browser -- should show all 12 chart panels populated with real data
4. **Balance test:** Hit `/api/balance` -- should return balance info (or graceful "not configured" if API keys not set)
5. **Evals test:** Hit `/api/evals` -- should return 8 eval results with scores and grades
6. **Live update test:** Send a message through OpenClaw TUI, then hit refresh on dashboard -- new call should appear in call log

---

## Design Decisions

### 1. Error Handling Strategy

**Decision: Log-and-skip at the line level.**

- Each JSONL line is parsed inside a `try/except`. Malformed lines are logged (with file path + line number) to a `List[ParseError]` on the reader and to `stderr`, then skipped.
- If an entire file fails to open (permissions, encoding), log the file-level error, skip that file, continue scanning.
- The `/api/summary` response includes a `parse_errors: int` count so the dashboard can surface a small warning badge if errors > 0.
- **Never stop the scan** â€” partial data is far more useful than no data.

### 2. Incremental Update Strategy

**Decision: File-level mtime tracking in SQLite, graceful deletion handling.**

- `OpenClawReader` uses the `file_index` table in `dashboard.db` to track `(filepath, mtime, record_count)`.
- On `scan()`:
  1. List all `.jsonl` and `.jsonl.reset.*` files in the sessions directory.
  2. For each file: if `mtime` matches the `file_index` entry, skip re-parsing. If `mtime` changed or file is new, re-parse, delete old records for that file, and insert new ones.
  3. For `file_index` entries whose files no longer exist on disk: **delete their records from both tables** (handles session file deletion cleanly).
- **Scan depth:** Flat sessions directory only (no recursive subdirectories â€” OpenClaw stores sessions flat).
- The auto-refresh (60s timer in JS) calls `GET /api/summary` etc., which queries the DB. `POST /api/refresh` forces a full re-scan by clearing all `file_index` entries.

### 3. Anthropic Balance Calculation

**Decision: Config-driven ledger of deposits.**

- `config.yaml` includes a ledger list under `balance.anthropic`:
  ```yaml
  balance:
    anthropic:
      ledger:
        - date: "2026-02-01"
          amount: 100.00
          note: "Initial API credits"
        # Add future top-ups as new entries:
        # - date: "2026-03-15"
        #   amount: 50.00
        #   note: "Top-up"
      warn_threshold: 20.00
      critical_threshold: 5.00
  ```
- **Calculation:** `remaining = sum(ledger[*].amount) - cumulative_cost_from_sessions`
- Handles multiple top-ups naturally â€” just add another ledger entry with date and amount.
- If no ledger is configured, the balance endpoint returns `{"anthropic": {"status": "not_configured", "message": "Add ledger entries to config.yaml"}}`.

### 4. Moonshot API Auth

**Decision: Environment variable reference in config, structured error responses.**

- `config.yaml` stores only the **env var name** (e.g., `MOONSHOT_API_KEY`), never the raw token:
  ```yaml
  balance:
    moonshot:
      api_key_env: "MOONSHOT_API_KEY"
      warn_threshold: 10.00
      critical_threshold: 2.00
  ```
- `BalanceChecker` reads `os.environ.get(env_var_name)` at call time.
- **Failure modes â€” all return structured JSON, never crash:**
  - Env var not set: `{"moonshot": {"status": "not_configured", "message": "Set MOONSHOT_API_KEY env var"}}`
  - HTTP 401/403 (bad/expired token): `{"moonshot": {"status": "auth_error", "message": "Token invalid or expired"}}`
  - Network timeout (5s): `{"moonshot": {"status": "unreachable", "message": "API timeout"}}`
- The dashboard JS renders these states as a grey/yellow gauge with the status message, rather than hiding the panel.

### 5. Database / Persistence

**Decision: SQLite from the start.**

- `dashboard.db` (SQLite) with two tables:
  - `records` â€” all 24 `TelemetryRecord` fields, indexed on `session_id`, `timestamp`, `provider`, `model`
  - `file_index` â€” tracks `(filepath, mtime, record_count)` for incremental updates
- `OpenClawReader.scan()` checks `file_index` mtimes, only re-parses changed/new files, upserts into `records`. Deleted source files have their records removed.
- All `/api/*` endpoints query SQLite directly using `aiosqlite` for async access from FastAPI.
- **Benefits:** Survives restarts without re-scan, enables efficient SQL aggregations for summary/timeseries endpoints, scales cleanly as session count grows.
- DB file location: `dashboard.db` in project root (gitignored).

### 6. Time Zone Handling

**Decision: API returns epoch ms, browser displays local time with UTC toggle.**

- OpenClaw timestamps are Unix epoch milliseconds (e.g., `1771138478699`). These are inherently UTC.
- `dashboard.js` converts to the browser's local timezone using `new Date(ts).toLocaleString()` by default.
- A small toggle in the dashboard header switches between "Local" and "UTC" display. Toggle state is persisted in `localStorage`.
- API responses always return raw epoch milliseconds â€” the JS layer handles all formatting. This keeps the API timezone-agnostic.

### 7. Chart.js Responsive Behavior

**Decision: Stack to single column on mobile, all charts visible, touch-friendly table.**

- **Breakpoints:**
  - `>1200px`: Full 12-column grid as designed (2-3 charts per row)
  - `768px-1200px`: 2 charts per row
  - `<768px`: Single column, all charts stacked vertically (no charts hidden â€” all data stays accessible)
- **Chart sizing:** Each chart canvas uses `maintainAspectRatio: false` with CSS `min-height: 250px` to stay readable on narrow screens.
- **Table on mobile:**
  - Horizontal scroll with `-webkit-overflow-scrolling: touch` for momentum scrolling
  - First column (timestamp) is sticky-positioned so it remains visible while scrolling horizontally
  - Pagination controls are full-width buttons (easy to tap)
- **KPI cards:** Wrap to 2-per-row on tablet, 1-per-row on phone using `flex-wrap`.

