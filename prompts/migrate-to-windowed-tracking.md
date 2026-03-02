# Prompt: Migrate API Usage Dashboard to Real-Time Claude Code & Codex Tracking

## Objective
Migrate the API Usage Dashboard to use the time-windowed usage tracking approach from the rainmeter-public repository. Implement up-to-the-minute tracking for both Claude Code (OAuth API) and Codex (local telemetry) subscriptions with 5-hour and 7-day windows There should be NO graphical change to the existing Resource Availability cards for Claude Code and Codex --  we only want to update the values correctly.

## Source Reference
See `C:\Users\AI-Agents\.openclaw\workspace\projects\rainmeter-public\@Resources\` for the working implementations:
- `Fetch-QuotaData.ps1` — Claude Code OAuth API approach
- `Fetch-CodexQuota.ps1` — Codex local telemetry parsing

## Requirements

### 1. Claude Code Usage Tracking (OAuth-Based)

**API Endpoint:** `https://api.anthropic.com/api/oauth/usage`

**Authentication:** OAuth token from Claude Code (stored in `~/.claude/config.json` or environment)

**Windows to Track:**
- 5-hour window: 300 minutes
- 7-day window: 10080 minutes

**Data Points to Capture:**
```json
{
  "five_hour": {
    "utilization_percent": 0-100,
    "resets_at": "ISO-8601 timestamp",
    "time_elapsed_percent": 0-100,
    "remaining_minutes": integer
  },
  "seven_day": {
    "utilization_percent": 0-100,
    "resets_at": "ISO-8601 timestamp",
    "time_elapsed_percent": 0-100,
    "remaining_minutes": integer
  }
}
```

**Core Calculation (from Get-TimePct):**
```python
from datetime import datetime, timezone
import math

def get_time_pct(iso_timestamp: str, window_minutes: int) -> float:
    """Calculate percentage of time elapsed in current window."""
    if not iso_timestamp:
        return -1
    try:
        reset_time = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00')).astimezone()
        remaining = (reset_time - datetime.now(timezone.utc).astimezone()).total_seconds() / 60
        elapsed = window_minutes - max(0, remaining)
        return max(0, min(100, round(elapsed / window_minutes * 100)))
    except Exception:
        return -1
```

### 2. Codex Usage Tracking (Local Telemetry)

**Data Source:** `~/.codex/sessions/*.jsonl` (newline-delimited JSON)

**Files to Parse:** All `*.jsonl` files in the Codex sessions directory

**Windows to Track (per stream):**
- Stream types: `GLOBAL` (limit_id="codex") and `MODEL` (model-specific)
- Each has: `primary` (5-hour, 300 min) and `secondary` (7-day, 10080 min) buckets

**JSONL Entry Format to Extract:**
```json
{
  "type": "token_count",
  "rate_limits": {
    "codex": {
      "primary": {
        "used_percent": 45.2,
        "resets_at": 1740882000,
        "window_minutes": 300
      },
      "secondary": {
        "used_percent": 12.5,
        "resets_at": 1741486800,
        "window_minutes": 10080
      }
    },
    "models": {
      "gpt-5.3-codex": {
        "primary": { ... },
        "secondary": { ... }
      }
    }
  }
}
```

**Core Calculation:**
```python
from datetime import datetime, timezone
import math

def get_time_pct_from_epoch(epoch_seconds: int, window_minutes: int) -> float:
    """Calculate percentage of time elapsed from Unix epoch timestamp."""
    if not epoch_seconds or not window_minutes:
        return -1
    try:
        reset_time = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone()
        remaining = (reset_time - datetime.now(timezone.utc).astimezone()).total_seconds() / 60
        elapsed = window_minutes - max(0, remaining)
        return max(0, min(100, round((elapsed / window_minutes) * 100)))
    except Exception:
        return -1
```

### 3. Dashboard Integration Requirements

**Background Refresh:**
- Auto-refresh every 60 seconds for live data
- Cache results to avoid API rate limits (30s minimum between Claude API calls)
- Read Codex JSONL on every refresh (local file, no rate limit)

**Visual Elements:**
- **Progress bars:** Show `utilization_percent` (how much quota used)
- **Time indicators:** Show `time_elapsed_percent` (how far into window)
- **Countdown timers:** Human-readable time until reset (e.g., "2h 15m", "3d 4h")
- **Color coding:**
  - Green: < 50% utilization
  - Yellow: 50-80% utilization
  - Red: > 80% utilization
  - Critical: > 95% utilization

**Layout:**
```
USE the existing layout we have. For Codex, just show the 5 hr and 1 wk windows.
```

### 4. Implementation Tasks

1. **Create Data Fetchers:**
   - `claude_code_fetcher.py` — OAuth API client with caching
   - `codex_fetcher.py` — JSONL parser with file watching

2. **Create Calculator Module:**
   - `window_calculator.py` — Shared `get_time_pct()` functions
   - Human-readable time formatting (`format_reset_time()`)

3. **Update Dashboard UI:**
   - Add new routes/endpoints for Claude Code and Codex data
   - Implement auto-refresh (JavaScript polling every 60s)
   - Create progress bar components with dual indicators (usage % + time %)

4. **Configuration:**
   - Read OAuth token from Claude Code config automatically
   - Configurable refresh intervals
   - Toggle switches to show/hide each service

5. **Error Handling:**
   - Graceful fallback if Claude API unavailable
   - Handle missing Codex telemetry (user hasn't used Codex yet)
   - Display "last updated" timestamp

## Technical Stack
- Python/Flask (existing dashboard)
- JavaScript for live updates
- No external API dependencies beyond existing Anthropic OAuth

## Success Criteria
- [ ] Dashboard displays real-time Claude Code 5H and 7D windows
- [ ] Dashboard displays real-time Codex GLOBAL 5H/7D windows
- [ ] Time elapsed percentage calculated correctly using `Get-TimePct` logic
- [ ] Auto-refresh working (60s interval)
- [ ] Visual indicators show both usage % and time progress
- [ ] Graceful handling when services unavailable

## Output Location
Implement in: `C:\Users\AI-Agents\.openclaw\projects\API-Useage-Dashboard\`
