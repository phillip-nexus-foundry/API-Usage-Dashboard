# Claude Code & Codex CLI Usage Fix

## Problem
The API Usage Dashboard was showing inaccurate Claude Code and Codex CLI usage data because:

1. **Browser CDP scraping was failing** - The dashboard relies on scraping `claude.ai/settings/usage` and `chatgpt.com/codex/settings/usage` via Chrome DevTools Protocol (CDP), but Brave browser wasn't running with CDP enabled (connection refused on 127.0.0.1:9222).

2. **Fallback values were outdated** - The fallback values in `config.yaml` were stale:
   - `claude_usage_fallback.spend_used: 24.49` (should be higher)
   - `claude_usage_fallback.extra_usage_balance: 0.51` (should be lower)
   - Codex CLI had no fallback values at all

## Solution Implemented

Since there is **no public API** available for Claude Code (Pro/Max subscription) or Codex CLI usage data, the solution implements:

### 1. Enhanced Fallback System
- Added `codex_usage_fallback` section to `config.yaml`
- Added `last_updated` and `source` fields to track when values were last updated
- Modified `balance/poller.py` to use fallback values when CDP scraping fails

### 2. OpenAI API Integration (Partial)
- Added `_fetch_openai_billing()` method to fetch available OpenAI billing data
- This provides credit grants and subscription info, though not the specific Codex CLI window usage

### 3. New API Endpoints
Added to `app.py`:
- `GET /api/fallback/usage` - Get current fallback values
- `POST /api/fallback/usage/claude` - Update Claude Code fallback values
- `POST /api/fallback/usage/codex` - Update Codex CLI fallback values

### 4. Command-Line Tool
Created `update_usage_fallback.py` script for easy manual updates:

```bash
# Show current fallback values
python update_usage_fallback.py --show

# Update Claude Code usage
python update_usage_fallback.py claude --spend-used 24.75 --extra-balance 0.25 --plan-usage-pct 95

# Update Codex CLI usage
python update_usage_fallback.py codex --weekly-remaining 60 --five-hour-remaining 80
```

### 5. Data Source Tracking
The dashboard now shows a `data_source` field in the resources response indicating:
- `cdp_scrape` - Data from browser scraping (most accurate)
- `openai_api` - Data from OpenAI API (for Codex)
- `fallback` - Data from manually updated fallback values
- `telemetry` - Data from local session tracking

## Files Modified

1. **`config.yaml`** - Added `codex_usage_fallback` section and enhanced `claude_usage_fallback` with tracking fields

2. **`balance/poller.py`**:
   - Modified `poll_anthropic()` to merge fallback values when CDP fails
   - Modified `poll_codex_cli()` to try OpenAI API and fallback values
   - Added `_fetch_openai_billing()` method

3. **`app.py`**:
   - Added `/api/fallback/usage` endpoints
   - Added data source tracking in resources response

4. **`update_usage_fallback.py`** (new) - Command-line tool for updating fallback values

## Usage Instructions

### To Update Claude Code Usage:
1. Go to https://claude.ai/settings/usage
2. Note the values shown:
   - "Plan usage limit" percentage
   - "Weekly limits" percentage
   - "Extra usage balance" amount
   - "Monthly spend limit" used/limit
3. Run:
   ```bash
   python update_usage_fallback.py claude \
     --spend-used 24.75 \
     --extra-balance 0.25 \
     --plan-usage-pct 95
   ```

### To Update Codex CLI Usage:
1. Go to https://chatgpt.com/codex/settings/usage
2. Note the values shown:
   - "5 hour usage limit" remaining percentage
   - "Weekly usage limit" remaining percentage
3. Run:
   ```bash
   python update_usage_fallback.py codex \
     --five-hour-remaining 80 \
     --weekly-remaining 60
   ```

### Via API:
```bash
# Get current fallback values
curl http://localhost:8050/api/fallback/usage

# Update Claude Code
curl -X POST http://localhost:8050/api/fallback/usage/claude \
  -H "Content-Type: application/json" \
  -d '{"spend_used": 24.75, "extra_usage_balance": 0.25}'

# Update Codex CLI
curl -X POST http://localhost:8050/api/fallback/usage/codex \
  -H "Content-Type: application/json" \
  -d '{"five_hour_remaining_pct": 80, "weekly_remaining_pct": 60}'
```

## Future Improvements

1. **Browser Extension** - Create a browser extension that can automatically extract usage data from the Claude/OpenAI websites and push it to the dashboard

2. **Scheduled Updates** - Set up a cron job or scheduled task that reminds the user to update values periodically

3. **Email/Notification** - Alert when fallback values are older than a threshold (e.g., 24 hours)

4. **OCR Screenshot** - Allow users to upload screenshots of the usage pages and use OCR to extract values automatically

## Note on API Availability

As of March 2026, there is **no public API** for:
- Claude Code (Pro/Max) subscription usage - this is separate from the Anthropic API
- Codex CLI usage limits - this is separate from the OpenAI API

Both services require browser-based authentication and have no documented API endpoints for usage data. The fallback system is the most reliable solution until official APIs become available.
