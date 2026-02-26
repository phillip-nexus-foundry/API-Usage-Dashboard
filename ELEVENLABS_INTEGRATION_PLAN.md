# ElevenLabs Agents Usage Tracking — Integration Plan

**Date:** 2026-02-25
**Status:** Planned
**Dashboard:** API-Usage-Dashboard
**Target:** Track ElevenLabs Conversational AI Agent call costs and usage

---

## 1. Overview

Add ElevenLabs as a third provider in the API Usage Dashboard, focusing on
Conversational AI Agents usage (billed per minute of call duration + LLM
pass-through costs). This extends the existing provider-agnostic architecture
with a new data source, cost model, and balance tracker.

---

## 2. ElevenLabs Billing Model

### Agent Calls (per-minute)

| Tier | Included Minutes | Overage |
|------|-----------------|---------|
| Free | 15 | N/A |
| Starter | 50 | N/A |
| Creator | 250 | ~$0.10/min |
| Pro | 1,100 | ~$0.10/min |
| Scale | 3,600 | ~$0.10/min |
| Business | 13,750 | ~$0.06–0.08/min |

**Special rates:**
- Setup/testing calls → 50% discount (`dev_discount`)
- Silence >10s → 5% of normal rate
- Burst mode (over concurrency limit) → 2x rate

### LLM Pass-Through

Tracked separately per conversation via `metadata.charging`:
- `llm_charge` — total LLM cost
- `llm_usage` — `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`

### TTS Characters (non-agent)

Character-based credits tracked via `x-character-count` response header.
Relevant if standalone TTS calls are also made outside of agents.

---

## 3. Available API Endpoints

### Subscription & Quota
| Endpoint | Purpose |
|----------|---------|
| `GET /v1/user/subscription` | Character quota, limits, reset time, billing period |
| `GET /v1/usage/character-stats` | Time-series character usage (hour/day/week/month) with breakdown by voice, product, or api_key |

### Agent Conversations (primary data source)
| Endpoint | Purpose |
|----------|---------|
| `GET /v1/convai/conversations` | List conversations — filterable by agent, duration, date range, outcome. Returns `call_duration_secs` and `message_count` |
| `GET /v1/convai/conversations/{id}` | Full detail — includes `metadata.charging` with `call_charge`, `llm_charge`, `llm_usage`, `free_minutes_consumed`, `is_burst`, `dev_discount`, `tier` |
| `GET /v1/convai/agents` | List agents (name, id, last call time) |

### Cost Estimation
| Endpoint | Purpose |
|----------|---------|
| `POST /v1/convai/llm-usage/calculate` | Estimate LLM cost given prompt size and knowledge base |

### Webhooks
| Event | Trigger |
|-------|---------|
| `post_call_transcription` | After each call — full conversation data including all charging fields |

---

## 4. Implementation Plan

### Phase 1 — Config & Data Model

**config.yaml additions:**
```yaml
balance:
  elevenlabs:
    api_key_env: ELEVENLABS_API_KEY
    mode: api          # 'api' (live subscription endpoint) or 'ledger'
    warn_threshold: 50 # minutes remaining
    critical_threshold: 10
    plan_tier: pro     # for overage rate calculation

elevenlabs:
  poll_interval_seconds: 300  # how often to poll conversations
  agents: []                  # auto-discovered or manually listed agent IDs
  webhook_enabled: false      # future: receive post_call_transcription events
```

**New DB table — `elevenlabs_conversations`:**
```sql
CREATE TABLE elevenlabs_conversations (
    conversation_id   TEXT PRIMARY KEY,
    agent_id          TEXT,
    agent_name        TEXT,
    timestamp         INTEGER,       -- start_time_unix_secs
    call_duration_secs REAL,
    call_charge       REAL,          -- from metadata.charging
    llm_charge        REAL,
    llm_input_tokens  INTEGER,
    llm_output_tokens INTEGER,
    total_cost        REAL,          -- call_charge + llm_charge
    is_burst          BOOLEAN,
    dev_discount      BOOLEAN,
    free_minutes_used REAL,
    call_successful   BOOLEAN,
    termination_reason TEXT,
    message_count     INTEGER
);
```

### Phase 2 — Data Ingestion

**New module: `parsers/elevenlabs_reader.py`**

1. On startup and at `poll_interval_seconds`, call `GET /v1/convai/conversations`
   with `call_start_after_unix` set to last-known timestamp (incremental).
2. For each new conversation, call `GET /v1/convai/conversations/{id}` to get
   full charging data.
3. Upsert into `elevenlabs_conversations` table.
4. Track `last_polled_timestamp` in a `file_index`-style metadata row.

**Rate limiting considerations:**
- ElevenLabs API rate limits vary by plan; add exponential backoff.
- Batch detail fetches with short delays (e.g., 200ms between calls).
- Cache agent name lookups (`GET /v1/convai/agents` once per poll cycle).

### Phase 3 — Balance & Quota Tracking

**Extend `balance/checker.py`:**

1. Call `GET /v1/user/subscription` to get:
   - `character_count` / `character_limit` → character usage %
   - `next_character_count_reset_unix` → days until reset
2. Calculate remaining agent minutes from:
   - Plan's included minutes − sum of `free_minutes_used` from conversations
   - Or derive from `GET /v1/usage/character-stats` if character-based
3. Status thresholds: `ok` / `warn` / `critical` based on remaining minutes.
4. Windows toast notifications when status changes (reuse existing mechanism).

### Phase 4 — API Endpoints

Add these endpoints to `app.py`:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/elevenlabs/summary` | Total calls, total minutes, total cost, avg call duration, success rate |
| `GET /api/elevenlabs/conversations` | Paginated conversation list with cost data |
| `GET /api/elevenlabs/timeseries?interval=day` | Time-bucketed cost and minutes |
| `GET /api/elevenlabs/agents` | Per-agent breakdown (calls, minutes, cost) |

**Also extend existing endpoints:**
- `GET /api/summary` — include elevenlabs in provider totals
- `GET /api/balance` — include elevenlabs quota status
- `GET /api/cost/daily` — include elevenlabs daily costs
- `GET /api/cost/projection` — include elevenlabs in projections

### Phase 5 — Dashboard UI

**New "ElevenLabs" section in dashboard.js:**

1. **Balance card** — Minutes remaining / total, character quota bar, reset date.
2. **KPI row** — Total Calls, Total Minutes, Total Cost, Avg Duration, Success Rate.
3. **Time series** — Minutes and cost over time (stacked: call_charge + llm_charge).
4. **Per-agent breakdown** — Bar chart of cost/minutes by agent.
5. **Conversation log** — Table with: agent, duration, cost, llm_cost, outcome,
   timestamp. Click to expand transcript summary.

**Integration with existing charts:**
- Provider doughnut → add "ElevenLabs" slice
- Daily cost chart → add elevenlabs stacked bar
- Cost projection → include elevenlabs trend line

### Phase 6 — Webhook Receiver (Optional / Future)

Instead of polling, receive `post_call_transcription` webhooks for real-time
ingestion:

1. Add `POST /api/webhooks/elevenlabs` endpoint.
2. Validate webhook payload, extract conversation + charging data.
3. Upsert into DB immediately.
4. Requires publicly reachable URL (ngrok for dev, reverse proxy for prod).

---

## 5. New Files

```
parsers/elevenlabs_reader.py    — API poller + conversation ingester
tests/test_elevenlabs.py        — Unit tests for reader and cost calculations
```

**Modified files:**
```
app.py                          — New endpoints + elevenlabs in existing aggregations
config.yaml                     — elevenlabs config section
balance/checker.py              — elevenlabs quota checking
static/dashboard.js             — elevenlabs UI section + provider integration
static/style.css                — elevenlabs card styling (if needed)
parsers/telemetry_schema.py     — Optional: unified record type or separate
```

---

## 6. Key Differences from LLM Tracking

| Aspect | Anthropic/Moonshot | ElevenLabs Agents |
|--------|-------------------|-------------------|
| Billing unit | Tokens (input/output) | Minutes + LLM tokens |
| Data source | Local JSONL files | Remote API polling |
| Cost fields | Single cost per call | `call_charge` + `llm_charge` |
| Quota | Dollar balance | Minutes remaining |
| Special rates | None | Burst (2x), dev (0.5x), silence (0.05x) |
| Real-time option | File watcher | Webhook (`post_call_transcription`) |

---

## 7. Dependencies

- `httpx` — already in requirements.txt (for API calls)
- `aiosqlite` — already in requirements.txt (for async DB)
- ElevenLabs API key with read access to conversations

No new Python packages required.
