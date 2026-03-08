# Telemetry Drift Fix: Automated Balance Polling Plan

**Date:** February 28, 2026  
**Status:** 📋 Planning Phase  
**Location:** `fixes/telemetry-drift-polling-plan.md`  
**Origin:** Discord Q&A VC suggestion  

---

## Executive Summary

**The Problem:** The dashboard calculates costs from telemetry logs, but calculation errors (e.g., cache write overcharging) and gaps in session data cause drift from provider ground truth. Currently requires manual `verified_usage_cost` overrides in `config.yaml`.

**The Solution:** Automated browser polling of provider account pages to fetch real balances, then auto-calibrate dashboard calculations against ground truth.

**Key Insight from Discord:** Use automated browser sessions to periodically poll the three provider balance pages and sync dashboard to real numbers.

---

## Q&A — Addressing Your Questions

### 1. "Can this be done?"
**Yes.** While providers don''t offer true "webhooks" for balance updates, we can achieve the same outcome via **automated browser automation** (Playwright/Selenium):

| Provider | Method | Feasibility | Notes |
|----------|--------|-------------|-------|
| **Anthropic** | Browser automation | ✅ High | Console at `console.anthropic.com/settings/billing` — clean DOM, predictable structure |
| **Moonshot** | Official API + Browser fallback | ✅ Already implemented | API endpoint `api.moonshot.cn/v1/users/me/balance` — no scraping needed |
| **MiniMax** | Browser automation | ⚠️ Medium | Chinese provider, may require translation layer or have CAPTCHA |
| **ElevenLabs** | Browser automation | ✅ High | Dashboard at `elevenlabs.io/subscription` — clear balance display |

### 2. "Do I have to leave browser pages or tabs open?"
**No.** The solution uses **headless browser automation**:
- Runs completely in background (no visible windows)
- Uses saved session cookies to stay logged in
- Spins up browser instance → navigates → extracts data → closes → repeats on schedule
- No persistent browser window or tab required

### 3. "Can it be done in the background as long as I am logged into those accounts?"
**Yes.** The approach uses **persistent browser profiles**:
- First run: You manually log in once through the automation tool (it saves cookies/session)
- Subsequent runs: Automation reuses saved session (cookies, localStorage, sessionStorage)
- Session refresh: Automatically handles session expiry by re-logging in if needed

### 4. "Can this automatically refresh to ensure up-to-the-minute balance readings?"
**Yes.** Configurable polling intervals:
- **High-frequency:** Every 5 minutes during active usage periods
- **Normal:** Every 15-30 minutes during business hours
- **Low-frequency:** Every 2-4 hours overnight
- **Smart polling:** Detect when you''re actively using the dashboard → increase poll frequency

---

## Architecture Options

### Option A: Dashboard-Integrated Polling (Recommended)
Extend the existing FastAPI app with a background polling service.

```
┌─────────────────────────────────────────────────────────────┐
│                    API Usage Dashboard                      │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │  FastAPI     │  │  Scheduler   │  │  Browser Pool   │   │
│  │  Endpoints   │◄─┤  (APScheduler)│◄─┤  (Playwright)   │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
│         │                                        │          │
│         ▼                                        ▼          │
│  ┌──────────────┐                       ┌──────────────┐   │
│  │  SQLite DB   │                       │  Provider    │   │
│  │  dashboard.db│                       │  Websites    │   │
│  └──────────────┘                       └──────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Pros:**
- Single process, shared config
- Direct DB writes to `verified_balance` table
- Already have Python ecosystem

**Cons:**
- Browser automation adds dependencies (~150MB)
- Runs on Windows (your workstation) — needs to be online

### Option B: Separate Balance Sync Service
Standalone microservice that only does balance polling.

**Pros:**
- Could run on a Raspberry Pi or VPS (always online)
- Dashboard can be restarted without affecting balance sync

**Cons:**
- More complex deployment
- Session cookies need to be shared/synced
- Overkill for current scale

### Option C: Browser Extension + Webhook Receiver
Browser extension extracts balances, sends to dashboard via HTTP POST.

**Pros:**
- No headless browser complexity
- Extension has access to logged-in sessions automatically

**Cons:**
- Requires building a browser extension
- Browser must be open

---

## Recommended Implementation: Option A (Integrated)

### Component Design

#### 1. Balance Polling Module (`balance/poller.py`)

```python
class BalancePoller:
    """Polls provider websites for live balance data."""
    
    async def poll_anthropic(self) -> BalanceResult:
        """Fetch balance from Anthropic console."""
        page = await self.context.new_page()
        await page.goto("https://console.anthropic.com/settings/billing")
        await page.wait_for_selector("[data-testid='balance-display']", timeout=10000)
        balance_text = await page.inner_text("[data-testid='balance-display']")
        return self._parse_anthropic_balance(balance_text)
```

#### 2. Calibration Engine (`balance/calibrator.py`)

Calculates drift between dashboard computed costs and ground truth balances.

#### 3. Scheduler Integration

Uses APScheduler for configurable polling intervals with smart frequency adjustment.

#### 4. Database Schema Addition

```sql
CREATE TABLE balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    balance REAL NOT NULL,
    timestamp INTEGER NOT NULL,
    source TEXT NOT NULL,
    drift_from_computed REAL
);
```

---

## Security Considerations

### 1. Credential Storage
- Never store plaintext passwords
- Use browser''s native credential storage (persistent context handles this)
- API keys continue using environment variables

### 2. Headless Detection Evasion
Some sites block headless browsers. Mitigations:
- Realistic viewport sizes
- Stealth scripts to mask `navigator.webdriver`
- Human-like delays between actions

### 3. Rate Limiting & Ethics
- Respect robots.txt
- Add jitter to polling intervals (±30 seconds)
- Honor HTTP 429 with exponential backoff

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1)
- [ ] Add Playwright dependency
- [ ] Create `balance/poller.py` with basic browser automation
- [ ] Implement Anthropic balance scraping
- [ ] Create `balance_snapshots` database table
- [ ] Add manual "Poll Now" button to dashboard UI

### Phase 2: Automation (Week 2)
- [ ] APScheduler integration
- [ ] Smart polling schedule
- [ ] Drift detection and alerting
- [ ] Auto-calibration logic

### Phase 3: Additional Providers (Week 3)
- [ ] ElevenLabs polling
- [ ] MiniMax polling (if needed)
- [ ] Drift trend visualization
- [ ] Calibration report UI

---

## Technical Requirements

### Dependencies
```txt
playwright>=1.41.0
apscheduler>=3.10.0
```

### First-Time Setup
```batch
# Install Playwright browsers (one-time)
playwright install chromium

# Initial login — manual auth flow
python -m balance.auth_setup --provider anthropic
```

---

## Decision Points

### Decision 1: Auto-Apply or Alert-Only?
When drift is detected:
- **A)** Auto-update `verified_usage_cost`
- **B)** Alert user and suggest manual approval (recommended for start)
- **C)** Track drift but never auto-correct

### Decision 2: Polling Frequency
- **Conservative:** Every 30 minutes day, 4 hours overnight
- **Aggressive:** Every 5 minutes during active use
- **Adaptive:** Start conservative, boost when dashboard is open (recommended)

### Decision 3: Drift Threshold
- **Strict:** 1% drift triggers alert
- **Moderate:** 5% drift or $2.00 absolute (recommended)
- **Loose:** 10% drift

---

## Success Metrics

- [ ] Dashboard balance matches provider ground truth within 1%
- [ ] No manual `verified_usage_cost` updates needed for 30 days
- [ ] Drift alerts catch discrepancies within 1 hour
- [ ] Polling system runs 7 days without manual intervention

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Provider changes website layout | Medium | High | Selector fallback strategies |
| CAPTCHA on login | Low | High | Manual re-auth flow |
| Browser automation detected | Low | Medium | Stealth plugins |
| Session expiry (30+ days) | High | Medium | Refresh token logic |

---

## Next Steps

1. **Review this plan** — any concerns or changes?
2. **Confirm Phase 1 priority** — Anthropic only, or include ElevenLabs?
3. **Decide on auto-apply vs alert-only**
4. **Create feature branch** and begin implementation

---

## Questions for Phillip

1. Should we start with just Anthropic (no API) or include ElevenLabs too?
2. Do you want auto-correction of drift or manual approval first?
3. Is it okay to add ~200MB of browser binaries to the project?
4. Should we research MiniMax/ElevenLabs APIs before committing to browser automation?

**Ready to proceed on your go-ahead.** 💠

---

## 🚀 Expanded Scope: Unified AI Resource Monitor

**Updated Vision (Feb 28, 2026):** Beyond just dollar balances, we''re building a **complete resource availability dashboard** that shows:

1. **Financial Balances:** Anthropic, MiniMax, Moonshot
2. **Rate Limit Utilization:** Codex (ChatGPT Plus) + Claude Code
3. **Unified View:** At-a-glance status of ALL AI tools and their current capacity

### Target Providers Matrix

| Provider | Balance | Rate Limits | Method | Priority |
|----------|---------|-------------|--------|----------|
| **Anthropic** | ✅ Dollar balance | ✅ RPM/TPM limits | Browser automation | **P0 - Start here** |
| **MiniMax** | ✅ Dollar balance | ✅ RPM/TPM limits | Browser automation | **P0** |
| **Moonshot** | ✅ Dollar balance (API) | ✅ RPM/TPM limits | API + Browser fallback | **P0** (API done) |
| **Codex (ChatGPT Plus)** | ❌ N/A (subscription) | ✅ Hourly/weekly usage | Browser automation | **P1** |
| **Claude Code** | ❌ N/A (subscription) | ✅ Hourly/daily/weekly | Browser automation | **P1** |
| **ElevenLabs** | ✅ Dollar balance | ❌ N/A | Browser automation | **P2** (future) |

### What We''ll Monitor For Each

#### 1. Anthropic (API Console)
- **Balance:** Remaining credits ($)
- **Rate Limits:** 
  - Current tier (Tier 1/2/3/4)
  - RPM (requests per minute)
  - Input TPM / Output TPM
  - Monthly spend cap
- **URL:** `console.anthropic.com/settings/billing`

#### 2. MiniMax (Chinese Provider)
- **Balance:** Remaining credits (¥ converted to $)
- **Rate Limits:** RPM, TPM per model
- **URL:** `https://www.minimaxi.com/platform`
- **Challenge:** May need translation, potential CAPTCHA

#### 3. Moonshot (Kimi)
- **Balance:** Already implemented via API
- **Rate Limits:** Enhance to show per-project limits
- **URL:** API already working + `platform.moonshot.cn` for details

#### 4. Codex (ChatGPT Plus)
- **Balance:** N/A (unlimited subscription)
- **Rate Limits:**
  - Hourly usage window
  - Daily usage (if applicable)
  - Weekly usage trends
  - Tier status (standard vs. extended)
- **URL:** `chat.openai.com` or `platform.openai.com/usage`
- **Note:** Codex CLI uses your ChatGPT Plus subscription — need to check usage dashboard

#### 5. Claude Code (Oath Tier)
- **Balance:** N/A (subscription-based)
- **Rate Limits:**
  - Hourly Claude Code usage
  - Daily limits
  - Weekly quota
  - Oath tier status
- **URL:** `console.anthropic.com` (same login as API)
- **Note:** Claude Code has separate rate limits from API usage

---

## Dashboard UI Updates Needed

### New "Resource Availability" Panel

Add a new top-row panel showing all tools at a glance:

```
┌─────────────────────────────────────────────────────────────────┐
│  AI RESOURCE AVAILABILITY                    [Refresh All]      │
├──────────────┬──────────────┬──────────────┬──────────────┬─────┤
│  ANTHROPIC   │  MOONSHOT    │  MINIMAX     │  CODEX       │ CLAUDE│
│  $10.79      │  ¥145.20     │  $22.15      │  ████░░░░░   │ ██████│
│  Tier 2      │  Project A   │  Tier 1      │  45% hourly  │ 78%   │
│  RPM: 50/1000│  RPM: 200/500│  RPM: 80/500 │  window      │ daily │
│              │              │              │              │       │
│  [Details]   │  [Details]   │  [Details]   │  [Details]   │ [Details]│
└──────────────┴──────────────┴──────────────┴──────────────┴─────┘
```

### Visualization Types

| Resource Type | Visualization |
|---------------|---------------|
| Dollar balance | Gauge chart (0 → warn → critical) |
| RPM usage | Bar chart (current/max with color zones) |
| Time-window usage | Progress bar with % fill |
| Rate limit tier | Badge + tooltip with upgrade info |

---

## Updated Implementation Roadmap

### Phase 1: Foundation + Anthropic (Week 1)
- [ ] Add Playwright dependency
- [ ] Create `balance/poller.py` with headless browser automation
- [ ] Implement **Anthropic balance + rate limit** scraping
- [ ] Create `resource_snapshots` table (expanded schema below)
- [ ] Add "Resource Availability" panel to dashboard UI
- [ ] Manual "Poll Now" button

### Phase 2: MiniMax + Moonshot Enhancement (Week 2)
- [ ] **MiniMax:** Balance + rate limit scraping (handle Chinese interface)
- [ ] **Moonshot:** Enhance existing API integration with rate limit details
- [ ] Add drift detection between computed and actual balances
- [ ] Smart polling scheduler (APScheduler)
- [ ] Rate limit alert notifications ("Codex hourly window at 80%")

### Phase 3: Codex + Claude Code (Week 3)
- [ ] **Codex:** Scrape ChatGPT Plus usage dashboard for hourly/daily/weekly stats
- [ ] **Claude Code:** Scrape Claude Code-specific rate limits from console
- [ ] Unified rate limit visualization (all tools in one view)
- [ ] Predictive alerts ("At current pace, you''ll hit Codex hourly limit in 15 min")

### Phase 4: Intelligence + Polish (Week 4)
- [ ] Auto-calibration for financial balances
- [ ] Usage pattern analysis ("You typically hit MiniMax limits on Tuesdays")
- [ ] Recommendation engine ("Switch to Moonshot — 60% RPM capacity available")
- [ ] Historical trend charts for rate limit utilization

---

## Updated Database Schema

```sql
-- Renamed and expanded from balance_snapshots
CREATE TABLE resource_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,              -- ''anthropic'', ''minimax'', ''moonshot'', ''codex'', ''claude_code''
    snapshot_type TEXT NOT NULL,         -- ''balance'', ''rate_limit'', ''full_status''
    timestamp INTEGER NOT NULL,          -- epoch ms
    
    -- Financial fields (when applicable)
    balance_amount REAL,                 -- Dollar amount (or converted to USD)
    balance_currency TEXT,               -- ''USD'', ''CNY'', etc.
    balance_source TEXT,                 -- ''api'', ''browser_poll'', ''manual''
    
    -- Rate limit fields
    tier TEXT,                           -- Tier name/number
    rpm_limit INTEGER,                   -- Max RPM
    rpm_used INTEGER,                    -- Current/used RPM
    rpm_remaining INTEGER,               -- Calculated
    
    input_tpm_limit INTEGER,
    input_tpm_used INTEGER,
    
    output_tpm_limit INTEGER,
    output_tpm_used INTEGER,
    
    -- Time-window limits (for subscription services like Codex/Claude Code)
    hourly_limit INTEGER,
    hourly_used INTEGER,
    hourly_reset_at INTEGER,             -- Epoch ms when window resets
    
    daily_limit INTEGER,
    daily_used INTEGER,
    daily_reset_at INTEGER,
    
    weekly_limit INTEGER,
    weekly_used INTEGER,
    weekly_reset_at INTEGER,
    
    -- Drift tracking (for financial providers)
    computed_cost REAL,                  -- What dashboard calculated
    drift_amount REAL,                   -- Difference from ground truth
    drift_percentage REAL,
    
    -- Raw data for debugging
    raw_response TEXT                    -- JSON blob of scraped data
);

-- Indexes for efficient queries
CREATE INDEX idx_resource_snapshots_provider_time 
    ON resource_snapshots(provider, timestamp);
CREATE INDEX idx_resource_snapshots_type_time 
    ON resource_snapshots(snapshot_type, timestamp);
```

---

## Technical Considerations for Rate Limit Scraping

### Codex (ChatGPT Plus)
- **Challenge:** OpenAI doesn''t have a simple "Codex usage" page — it''s part of general ChatGPT Plus usage
- **Approach:** Scrape `platform.openai.com/usage` and filter for "codex" entries
- **Alternative:** Parse local Codex CLI logs if available
- **Note:** ChatGPT Plus is "unlimited" but Codex has implicit rate limits — need to discover these

### Claude Code (Oath Tier)
- **Challenge:** Claude Code rate limits are displayed in the CLI and console, but may not have a dedicated API
- **Approach:** 
  - Scrape `console.anthropic.com` for Claude Code section
  - Parse Claude Code CLI output (if accessible)
- **Data to capture:**
  - Current Oath tier status
  - Hourly usage bar
  - Daily usage bar
  - Weekly usage bar

### Authentication Strategy
All browser-automated providers need the same auth approach:

```python
# One-time setup flow for each provider
async def setup_auth(provider: str):
    """Interactive auth setup — user logs in once, we save session."""
    context = await launch_persistent_context(
        user_data_dir=f"./browser_profiles/{provider}",
        headless=False  # Show browser for manual login
    )
    page = await context.new_page()
    await page.goto(get_login_url(provider))
    await page.wait_for_selector("[data-testid='dashboard']", timeout=300000)  # 5 min for manual auth
    print(f"✅ {provider} authenticated — session saved")
    await context.close()
```

---

## Updated Decision Points

### Decision 1: Scope for Phase 1
**Original:** Start with Anthropic only  
**Options:**
- **A)** Anthropic only (balance + rate limits)
- **B)** Anthropic + Moonshot enhancement
- **C)** Anthropic + MiniMax (both browser-based)
- **D)** All three: Anthropic, MiniMax, Moonshot

**Recommendation:** **D** — All three. Moonshot API is already done, just need to add rate limit details.

### Decision 2: Codex/Claude Code Priority
- **A)** Include in Phase 1 (parallel track)
- **B)** Phase 2 (after financial balances working)
- **C)** Phase 3 (final polish)

**Recommendation:** **B** — Get financial balances rock-solid first, then add subscription rate limits.

### Decision 3: Alert Strategy
When rate limits approach threshold:
- **A)** Dashboard-only visual indicator
- **B)** Toast notifications + dashboard
- **C)** Discord DM + toast + dashboard (aggressive)

**Recommendation:** **B** for now, upgrade to **C** if you find value.

### Decision 4: Auto-Calibration Threshold
- **A)** Alert at 5% drift, auto-correct at 10%
- **B)** Alert at 2% drift, manual approve only
- **C)** Auto-correct any drift (aggressive)

**Recommendation:** **A** — Balanced approach.

---

## Success Metrics (Updated)

- [ ] Dashboard shows real-time balance for Anthropic, MiniMax, Moonshot within 1%
- [ ] Dashboard shows current rate limit status for all 5 providers
- [ ] Rate limit alerts fire before hitting 90% of any limit
- [ ] No manual balance updates needed for 30 days
- [ ] Can view 7-day trend of rate limit utilization per provider
- [ ] System suggests optimal provider based on current availability

---

## Next Steps (Updated)

1. **Approve expanded scope** — 5 providers (3 financial + 2 subscription-based)
2. **Confirm Phase 1 providers** — Anthropic, MiniMax, Moonshot?
3. **Decide on auto-calibration approach** (Decision 4)
4. **Create feature branch** — `feature/unified-resource-monitor`
5. **Begin Phase 1 implementation**

**Ready to build your AI resource command center.** 💠
