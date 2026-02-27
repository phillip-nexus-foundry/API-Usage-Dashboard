# API Usage Dashboard

Real-time token usage and cost tracking for all LLMs used via OpenClaw.

## Quick Start

```batch
cd C:\Users\AI-Agents\.openclaw\projects\API-Useage-Dashboard
run-persistent.bat
```

Dashboard runs at **http://127.0.0.1:8050**

## How to Run

| Method | Command | Use When |
|--------|---------|----------|
| **Persistent (recommended)** | `run-persistent.bat` | Normal use — auto-restarts on crash |
| **One-shot** | `python app.py` | Debugging — exits on crash |
| **Via uvicorn** | `python -m uvicorn app:app --host 127.0.0.1 --port 8050` | Development |

### First-time setup
```batch
pip install -r requirements.txt
```

## Architecture

- **Parser:** Reads OpenClaw JSONL session files from `C:/Users/AI-Agents/.openclaw/agents/main/sessions/`
- **Database:** SQLite (`dashboard.db`) — auto-created, incremental updates via mtime tracking
- **Server:** FastAPI + static files (Chart.js dark-theme dashboard)
- **File watcher:** Auto-rescans when session files change (debounced 1s)
- **Balance checker:** Probes Anthropic/Moonshot APIs for rate limits on startup

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | Main server — all API endpoints |
| `config.yaml` | Pricing, rate limits, server config |
| `parsers/openclaw_reader.py` | JSONL parser + SQLite writer |
| `parsers/telemetry_schema.py` | TelemetryRecord dataclass + cost math |
| `static/` | Frontend (HTML/JS/CSS) |
| `balance/` | API balance + rate limit probing |
| `evals/` | Automated evaluation checks |

## Notes

- Port `8050` is the default (configurable in `config.yaml`)
- The persistent runner restarts after 5 seconds on crash
- Historical crash cause: MemoryError on large queries — now handled gracefully with gc.collect() fallback
