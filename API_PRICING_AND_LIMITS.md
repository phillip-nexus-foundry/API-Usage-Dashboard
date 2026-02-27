# API Pricing and Rate Limits Documentation

**Last Updated:** February 27, 2026  
**Dashboard Version:** API-Usage-Dashboard  
**Maintained by:** Development Team

---

## Overview

This document provides comprehensive pricing and rate limit information for all API providers supported by the API-Usage-Dashboard. Understanding these limits is crucial for monitoring usage, avoiding throttling, and optimizing costs.

---

## Provider Comparison Summary

| Provider | Tier System | Entry RPM | Max RPM | Entry TPM | Max TPM | Context Window |
|----------|-------------|-----------|---------|-----------|---------|----------------|
| **Anthropic** | 4 tiers (deposit-based) | 50 (Tier 1) | 4,000 (Tier 4) | 30K | 2M | Up to 200K |
| **Moonshot** | 6 tiers (cumulative recharge) | 3 (Tier 0) | 10,000 (Tier 5) | 500K | 5M | Up to 256K |
| **MiniMax** | **Flat — no tiers** | **500** | **500** | **20M** | **20M** | 196,608 |

---

## 1. Anthropic (Claude Models)

### Tier Structure

Anthropic uses a **deposit-based tier system**. Cumulative lifetime deposits unlock tiers immediately — not monthly spend.

| Tier | Deposit Required | Monthly Spend Limit | RPM | Sonnet ITPM | Sonnet OTPM | Special |
|----------|----------------|-------------------|-----|-------------|-------------|-----------------|
| **Tier 1** | $5 | $100 | 50 | 30,000 | 8,000 | — |
| **Tier 2** | $40 | $500 | 1,000 | 450,000 | 90,000 | — |
| **Tier 3** | $200 | $1,000 | 2,000 | 800,000 | 160,000 | — |
| **Tier 4** | $400 | $5,000 | 4,000 | 2,000,000 | 400,000 | 1M context window |

### How Tier Advancement Works
- **Immediate:** No waiting period or approval — automatic upon deposit threshold
- **Cumulative:** Lifetime deposits count toward tier (not just current balance)
- **Deposits = Credits:** The money goes into your account as prepaid API credits — not a fee
- **Example:** If you have $20 in lifetime purchases and add $25 more, you immediately hit Tier 2 ($45 > $40 threshold)

### Rate Limit Mechanics
- Uses **token bucket algorithm** — continuous replenishment, not fixed windows
- If you hit a limit, you don't wait for a full minute; tokens replenish continuously

### Model Pricing (per 1M tokens)

| Model | Input | Output | Cache Read | Cache Write |
|-------|-------|--------|------------|-------------|
| **claude-opus-4-6** | $15.00 | $75.00 | $1.50 | $18.75 |
| **claude-sonnet-4-6** | $3.00 | $15.00 | $0.30 | $3.75 |
| **claude-haiku-4-5-20251001** | $1.00 | $5.00 | $0.10 | $1.25 |
| **claude-haiku-4-5** | $1.00 | $5.00 | $0.10 | $1.25 |
| **claude-3-5-sonnet-20241022** | $3.00 | $15.00 | $0.30 | $3.75 |
| **claude-3-5-haiku-20241022** | $0.80 | $4.00 | $0.08 | $1.00 |
| **claude-3-haiku-20240307** | $0.25 | $1.25 | $0.03 | $0.30 |

---

## 2. Moonshot (Kimi Models)

### Tier Structure

Moonshot uses a **cumulative recharge tier system**. Each tier unlocks higher rate limits.

| Tier | Cumulative Recharge | Concurrency | RPM | TPM | TPD |
|------|---------------------|-------------|-----|-----|-----|
| **Tier 0** | $1 | 1 | 3 | 500,000 | 1,500,000 |
| **Tier 1** | $10 | 50 | 200 | 2,000,000 | Unlimited |
| **Tier 2** | $20 | 100 | 500 | 3,000,000 | Unlimited |
| **Tier 3** | $100 | 200 | 5,000 | 3,000,000 | Unlimited |
| **Tier 4** | $1,000 | 400 | 5,000 | 4,000,000 | Unlimited |
| **Tier 5** | $3,000 | 1,000 | 10,000 | 5,000,000 | Unlimited |

**Key Metrics:**
- **RPM:** Requests Per Minute
- **TPM:** Tokens Per Minute
- **TPD:** Tokens Per Day

### Project-Level Limits
Moonshot also supports project-level rate limiting. Each API key can be associated with a project that has its own limits within the account tier.

### Model Pricing (per 1M tokens)

| Model | Input | Output | Cache Read | Cache Write | Notes |
|-------|-------|--------|------------|-------------|-------|
| **kimi-k2.5** | $0.60 | $3.00 | $0.10 | $0.60 | Flagship model, strong reasoning |
| **moonshot-v1-8k** | $0.20 | $2.00 | $0.10 | $0.20 | Budget option, shorter context |

### Web Tool Surcharges
Moonshot applies a **$0.01 per call surcharge** for web operations:
- `web_search`
- `web_fetch`
- `browser`

---

## 3. MiniMax (M2.5 Models)

### Tier Structure

**MiniMax uses a flat rate limit model — no tiers, no deposit thresholds.**

All accounts receive the same generous rate limits immediately upon activation:

| Metric | Value |
|--------|-------|
| **RPM** | 500 (flat for all users) |
| **TPM** | 20,000,000 (20M tokens per minute) |
| **Context Window** | 196,608 tokens |
| **Concurrency** | Not explicitly limited |

### Model Pricing (per 1M tokens)

| Model | Input | Output | Cache Read | Notes |
|-------|-------|--------|------------|-------|
| **MiniMax-M2.5** | $0.30 | $1.10 | $0.15 | Latest flagship (Feb 2026) |
| **MiniMax-M2.1** | $0.27 | $0.95 | — | Previous generation |
| **MiniMax-M2** | $0.255 | $1.00 | — | Base M2 series |
| **MiniMax-01** | $0.20 | $1.10 | — | Most affordable |

### Rate Limits by API Type

MiniMax provides separate rate limits for different API endpoints:

| API | Models | RPM | TPM |
|-----|--------|-----|-----|
| **Text API** | MiniMax-M2.5, M2.5-highspeed, M2.1, M2.1-highspeed, M2 | 500 | 20,000,000 |
| **T2A (Text to Audio)** | speech-2.8-turbo/hd, speech-2.6-turbo/hd, speech-02-turbo/hd | 60 | 20,000 |
| **Voice Cloning** | — | 60 | — |
| **Voice Design** | — | 20 | — |
| **Video Generation** | MiniMax-Hailuo-2.3, MiniMax-Hailuo-02 | 5 | — |
| **Image Generation** | image-01 | 10 | 60 |
| **Music Generation** | Music-2.5, Music-2.0 | 120 | — |

### Audio Subscription Plans (Optional)

For high-volume audio usage, MiniMax offers subscription tiers:

| Plan | Monthly | Credits/Month | RPM | Voice Slots |
|------|---------|---------------|-----|-------------|
| **Starter** | $5 | 100,000 | 10 | 10 |
| **Standard** | $30 | 300,000 | 50 | 100 |
| **Pro** | $99 | 1,100,000 | 200 | 250 |
| **Scale** | $249 | 3,300,000 | 500 | 500 |
| **Business** | $999 | 20,000,000 | 800 | 800 |

*Note: These subscriptions are for audio APIs only and do not affect text model rate limits.*

---

## Rate Limit Behavior

### HTTP 429 (Rate Limit Exceeded)

When you exceed rate limits, providers return HTTP 429 status codes with varying retry guidance:

| Provider | Retry-After Header | Error Message |
|----------|-------------------|---------------|
| **Anthropic** | Yes (seconds) | "Rate limit exceeded" |
| **Moonshot** | Yes (seconds) | "rate limit exceeded" |
| **MiniMax** | Check documentation | Varies by endpoint |

### Best Practices

1. **Exponential Backoff:** When receiving 429 errors, implement exponential backoff with jitter
2. **Token Bucket Awareness:** Remember that limits replenish continuously, not at minute boundaries
3. **Cache Aggressively:** Use prompt caching where available to reduce token usage and API calls
4. **Monitor Dashboard:** Use the API-Usage-Dashboard to track your usage patterns and approach to limits
5. **Preemptive Scaling:** If consistently hitting limits, upgrade tiers before critical operations

---

## Dashboard Configuration

### config.yaml Structure

Rate limits are configured in `config.yaml` under the `rate_limits` section:

```yaml
rate_limits:
  claude-opus:
    rpm: 50
    tpm: 38000
    input_tpm: 30000
    output_tpm: 8000
    models:
    - claude-opus-4-6
    auto_detected: true
  
  moonshot:
    rpm: 500
    tpm: 3000000
    models:
    - kimi-k2.5
    - moonshot-v1-8k
    note: Tier2 global limits; per-project limits via API key
  
  minimax:
    rpm: 500
    tpm: 20000000
    models:
    - minimax-m2.5
    note: Flat limits — no tier system
```

### Model Costs Configuration

Model costs are defined in both `config.yaml` and `parsers/telemetry_schema.py`:

```yaml
model_costs:
  minimax-m2.5:
    input: 0.30
    output: 1.10
    cache_read: 0.15
    cache_write: 0.0  # Not currently offered
```

---

## Changelog

| Date | Change |
|------|--------|
| 2026-02-27 | Added MiniMax M2.5 pricing and rate limits documentation |
| 2026-02-27 | Documented Anthropic tier structure (Tier 1-4) |
| 2026-02-27 | Documented Moonshot tier structure (Tier 0-5) |
| 2026-02-25 | Updated Moonshot pricing for kimi-k2.5 |
| 2026-02-23 | Initial documentation for Anthropic and Moonshot |

---

## References

- **Anthropic Pricing:** https://www.anthropic.com/pricing
- **Anthropic Rate Limits:** https://docs.anthropic.com/en/api/rate-limits
- **Moonshot Platform:** https://platform.moonshot.ai/
- **Moonshot Pricing:** https://platform.moonshot.ai/docs/guides/pricing
- **Moonshot Rate Limits:** https://platform.moonshot.ai/docs/guides/rate-limits
- **MiniMax Platform:** https://platform.minimax.io/
- **MiniMax Pricing:** https://platform.minimax.io/docs/guides/pricing
- **MiniMax Rate Limits:** https://platform.minimax.io/docs/guides/rate-limits
- **Price Per Token (Aggregator):** https://pricepertoken.com/

---

*This documentation is automatically referenced by the API-Usage-Dashboard for display and alerting purposes.*
