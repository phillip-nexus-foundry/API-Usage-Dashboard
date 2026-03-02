"""
Resource availability routes.
Claude Code tier, usage windows (5hr/1wk), extra usage, spend limits.
Filters out balance-only providers (minimax, moonshot).
"""
import os
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter
from sqlalchemy import func, text

from dashboard.data.models import Record

router = APIRouter(tags=["resources"])
logger = logging.getLogger(__name__)

_config = None
_balance_poller = None
_db = None

_BALANCE_ONLY_PROVIDERS = {"minimax", "moonshot"}


def init(config, balance_poller=None, db=None):
    global _config, _balance_poller, _db
    _config = config
    _balance_poller = balance_poller
    _db = db


def _get_claude_code_tier_display() -> str:
    tier = os.environ.get('CLAUDE_CODE_TIER', '').lower().strip()
    if not tier:
        tier = (_config or {}).get('claude_code_tier', 'pro').lower().strip()
    tier_map = {
        'pro': 'Claude Code Pro ($20/mo)',
        'max_100': 'Claude Code Max ($100/mo)',
        'max_200': 'Claude Code Max ($200/mo)',
    }
    return tier_map.get(tier, 'Claude Code Pro ($20/mo)')


def _window_usage(provider_aliases, since_ms):
    """Query usage stats within a time window."""
    if not _db:
        return {"calls": 0, "cost": 0.0, "first_ts": None, "last_ts": None}
    with _db.session() as session:
        q = session.query(
            func.count(Record.id),
            func.coalesce(func.sum(Record.cost_total), 0.0),
            func.min(Record.timestamp),
            func.max(Record.timestamp),
        ).filter(
            Record.provider.in_(provider_aliases),
            Record.timestamp >= since_ms,
        )
        row = q.one()
        return {
            "calls": int(row[0] or 0),
            "cost": float(row[1] or 0.0),
            "first_ts": int(row[2]) if row[2] is not None else None,
            "last_ts": int(row[3]) if row[3] is not None else None,
        }


def _to_provider_units(unit, usage_cost, usage_calls, provider_key):
    if unit == "usd":
        return round(usage_cost, 2)
    if provider_key == "codex_cli":
        estimated = max(int(round(usage_calls * 4)), int(round(usage_cost * 100)))
        return float(estimated)
    if provider_key == "elevenlabs":
        return float(int(round(usage_cost * 100)))
    return 0.0


def _pct(used, limit):
    if not limit or limit <= 0:
        return 0.0
    return round(min(100.0, (used / limit) * 100.0), 1)


@router.get("/resources")
async def resources():
    """Resource availability cards with usage windows."""
    provider_defs = {
        "anthropic": {
            "display_name": _get_claude_code_tier_display(),
            "usage_provider_aliases": ["anthropic"],
            "window_limits": {"one_week": 20.00},
            "unit": "usd",
        },
        "elevenlabs": {
            "display_name": "ElevenLabs",
            "usage_provider_aliases": ["elevenlabs"],
            "window_limits": {"one_month": 100000},
            "unit": "credits",
        },
        "codex_cli": {
            "display_name": "Codex CLI",
            "usage_provider_aliases": ["openclaw", "codex_cli"],
            "window_limits": {"five_hour": 400, "one_week": 1050},
            "unit": "credits",
            "pricing_notes": {
                "minimum_purchase": "1,000 credits per purchase",
                "messages_per_purchase": "250-1,300 CLI or Extension messages",
                "cloud_tasks_per_purchase": "40-250 cloud tasks",
            },
        },
    }

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    five_hour_ms = 5 * 60 * 60 * 1000
    one_week_ms = 7 * 24 * 60 * 60 * 1000
    five_hour_start = now_ms - five_hour_ms
    one_week_start = now_ms - one_week_ms

    snapshots = {}
    if _balance_poller:
        snapshots = _balance_poller.get_latest_snapshots(list(provider_defs.keys()))

    def _window_reset_meta(window_usage, window_ms):
        first_ts = window_usage.get("first_ts")
        if not first_ts:
            return {"reset_at": None, "remaining_seconds": None}
        reset_at = int(first_ts) + int(window_ms)
        remaining_seconds = max(0, (reset_at - now_ms) // 1000)
        return {"reset_at": reset_at, "remaining_seconds": int(remaining_seconds)}

    response_providers = {}
    for provider_key, provider_def in provider_defs.items():
        usage_5h = _window_usage(provider_def["usage_provider_aliases"], five_hour_start)
        usage_1w = _window_usage(provider_def["usage_provider_aliases"], one_week_start)
        used_5h = _to_provider_units(provider_def["unit"], usage_5h["cost"], usage_5h["calls"], provider_key)
        used_1w = _to_provider_units(provider_def["unit"], usage_1w["cost"], usage_1w["calls"], provider_key)

        limit_5h = float(provider_def.get("window_limits", {}).get("five_hour", 0))
        limit_1w = float(provider_def.get("window_limits", {}).get("one_week", 0))
        snapshot = snapshots.get(provider_key, {})
        ts = snapshot.get("timestamp")
        age_seconds = max(0, (now_ms - int(ts)) // 1000) if ts else None

        is_window_based = provider_key in {"anthropic", "codex_cli"}
        extra_value = round(used_1w, 2)
        display_name = provider_def["display_name"]
        tier = snapshot.get("tier")
        total_credits = snapshot.get("total_credits")
        spend_used = None
        spend_limit_val = None
        pct_5h = None  # Will be set by provider block if scraped data available
        pct_1w = None
        _scraped_5h = False
        _scraped_1w = False
        spend_reset_text = None

        if provider_key == "elevenlabs":
            bal = snapshot.get("balance_amount")
            if bal is not None:
                extra_value = round(float(bal), 2)
            if tier:
                display_name = f"ElevenLabs ({tier})"
        elif provider_key == "anthropic":
            extra_limit = float((_config or {}).get("claude_extra_usage_limit", 25.0))
            extra_value = round(max(extra_limit - used_1w, 0.0), 2)
            total_credits = extra_limit
            usage_payload = (snapshot.get("raw_payload") or {}).get("claude_usage") if snapshot else {}
            fallback = (_config or {}).get("claude_usage_fallback", {}) or {}
            spend_used = usage_payload.get("spend_used") if isinstance(usage_payload, dict) else None
            spend_limit_val = usage_payload.get("spend_limit") if isinstance(usage_payload, dict) else None
            spend_reset_text = usage_payload.get("spend_reset_text") if isinstance(usage_payload, dict) else None
            extra_balance = usage_payload.get("extra_usage_balance") if isinstance(usage_payload, dict) else None
            if spend_used is None:
                spend_used = fallback.get("spend_used")
            if spend_limit_val is None:
                spend_limit_val = fallback.get("spend_limit", extra_limit)
            if spend_reset_text is None:
                spend_reset_text = fallback.get("spend_reset_text")
            if extra_balance is None:
                extra_balance = fallback.get("extra_usage_balance")
            if isinstance(extra_balance, (int, float)):
                extra_value = round(float(extra_balance), 2)
            if isinstance(spend_limit_val, (int, float)):
                total_credits = round(float(spend_limit_val), 2)

            # Use scraped Claude Code usage meter percentages (from CDP)
            if isinstance(usage_payload, dict):
                scraped_plan_pct = usage_payload.get("plan_usage_pct")
                scraped_weekly_pct = usage_payload.get("weekly_pct")
                if isinstance(scraped_plan_pct, (int, float)):
                    pct_5h = round(float(scraped_plan_pct), 1)
                    used_5h = pct_5h
                    limit_5h = 100.0
                    _scraped_5h = True
                if isinstance(scraped_weekly_pct, (int, float)):
                    pct_1w = round(float(scraped_weekly_pct), 1)
                    used_1w = pct_1w
                    limit_1w = 100.0
                    _scraped_1w = True
        elif provider_key == "codex_cli":
            extra_value = 0.0
            # Use scraped Codex usage data (from CDP)
            codex_payload = (snapshot.get("raw_payload") or {}).get("codex_usage") if snapshot else {}
            if isinstance(codex_payload, dict):
                five_hr_remaining = codex_payload.get("five_hour_remaining_pct")
                weekly_remaining = codex_payload.get("weekly_remaining_pct")
                if isinstance(five_hr_remaining, (int, float)):
                    pct_5h = round(100.0 - float(five_hr_remaining), 1)
                    used_5h = pct_5h
                    limit_5h = 100.0
                    _scraped_5h = True
                if isinstance(weekly_remaining, (int, float)):
                    pct_1w = round(100.0 - float(weekly_remaining), 1)
                    used_1w = pct_1w
                    limit_1w = 100.0
                    _scraped_1w = True

        window_5h_meta = _window_reset_meta(usage_5h, five_hour_ms)
        window_1w_meta = _window_reset_meta(usage_1w, one_week_ms)
        if provider_key == "anthropic":
            window_5h_meta = {"reset_at": None, "remaining_seconds": None}
            window_1w_meta = {"reset_at": None, "remaining_seconds": None}

        # Use scraped percentages if set by provider block above; otherwise compute from DB
        if not isinstance(pct_5h, (int, float)):
            pct_5h = _pct(used_5h, limit_5h)
        if not isinstance(pct_1w, (int, float)):
            pct_1w = _pct(used_1w, limit_1w)

        response_providers[provider_key] = {
            "provider": provider_key,
            "display_name": display_name,
            "status": "ok",
            "age_seconds": age_seconds,
            "windows": {
                "five_hour": {
                    "label": "5 hr",
                    "used": round(used_5h, 2),
                    "limit": round(limit_5h, 2),
                    "percent": pct_5h,
                    "reset_at": window_5h_meta["reset_at"],
                    "remaining_seconds": window_5h_meta["remaining_seconds"],
                    "source": "scraped" if _scraped_5h else "computed",
                },
                "one_week": {
                    "label": "1 wk",
                    "used": round(used_1w, 2),
                    "limit": round(limit_1w, 2),
                    "percent": pct_1w,
                    "reset_at": window_1w_meta["reset_at"],
                    "remaining_seconds": window_1w_meta["remaining_seconds"],
                    "source": "scraped" if _scraped_1w else "computed",
                },
            } if is_window_based else None,
            "extra_usage": {
                "unit": provider_def["unit"],
                "value": extra_value,
                "total": round(float(total_credits), 2) if total_credits is not None else None,
                "label": "Extra Usage Balance" if provider_key == "anthropic" else None,
            },
            "spend_limit": {
                "used": round(float(spend_used), 2) if isinstance(spend_used, (int, float)) else None,
                "limit": round(float(total_credits), 2) if total_credits is not None else None,
                "reset_text": spend_reset_text,
            } if provider_key == "anthropic" else None,
            "tier": tier,
            "total_credits": round(float(total_credits), 2) if total_credits is not None else None,
            "pricing_notes": provider_def.get("pricing_notes"),
        }

    # Filter out balance-only providers
    response_providers = {k: v for k, v in response_providers.items() if k not in _BALANCE_ONLY_PROVIDERS}
    return {"providers": response_providers}


@router.post("/resources/poll")
async def resources_poll():
    """Trigger immediate resource polling."""
    if _balance_poller:
        results = await _balance_poller.poll_all(
            ["anthropic", "elevenlabs", "codex_cli"]
        )
        return {"status": "ok", "polled": len(results)}
    return {"status": "ok", "polled": 0, "note": "Poller not configured"}
