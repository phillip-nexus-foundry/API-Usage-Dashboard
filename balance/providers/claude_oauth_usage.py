"""Claude usage via OAuth API (Rainmeter parity, Python port)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from balance.usage_windows import clamp_percent, compute_timepct, format_reset, parse_timestamp

TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"


def _find_nested_keys(obj: Any, keys: Tuple[str, ...]) -> Optional[Any]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and v is not None:
                return v
            found = _find_nested_keys(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_nested_keys(item, keys)
            if found is not None:
                return found
    return None


def _resolve_credentials_path(config: Dict[str, Any]) -> Path:
    path = config.get("claude_oauth_credentials_path") or "~/.claude/.credentials.json"
    return Path(os.path.expanduser(path))


def load_credentials(config: Dict[str, Any]) -> Tuple[Dict[str, Any], Path]:
    path = _resolve_credentials_path(config)
    if not path.exists():
        raise FileNotFoundError(f"Claude credentials file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Claude credentials JSON must be an object.")
    return data, path


def _extract_token_fields(credentials: Dict[str, Any]) -> Dict[str, Any]:
    access = _find_nested_keys(credentials, ("accesstoken", "access_token", "token"))
    refresh = _find_nested_keys(credentials, ("refreshtoken", "refresh_token"))
    expires = _find_nested_keys(credentials, ("expiresat", "expires_at", "exp"))
    client_id = _find_nested_keys(credentials, ("clientid", "client_id"))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires,
        "client_id": client_id,
    }


def _set_nested_token(credentials: Dict[str, Any], key_variants: Tuple[str, ...], value: Any) -> bool:
    if not isinstance(credentials, dict):
        return False
    for k, v in credentials.items():
        if k.lower() in key_variants:
            credentials[k] = value
            return True
        if isinstance(v, dict) and _set_nested_token(v, key_variants, value):
            return True
    return False


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(path)


def _util_to_pct(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    # Some APIs return 0..1 utilization.
    if 0.0 <= num <= 1.0:
        num *= 100.0
    return clamp_percent(num)


async def _refresh_if_needed(tokens: Dict[str, Any], credentials: Dict[str, Any], path: Path, timeout_s: int) -> Dict[str, Any]:
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    expires_at = parse_timestamp(tokens.get("expires_at"))
    if access:
        if not expires_at:
            return tokens
        remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if remaining > 60:
            return tokens

    if not refresh:
        return tokens

    payload: Dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }
    if tokens.get("client_id"):
        payload["client_id"] = tokens["client_id"]

    async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
        resp = await client.post(TOKEN_URL, json=payload)
        if resp.status_code >= 400:
            return tokens
        data = resp.json() if resp.content else {}
    new_access = data.get("access_token") or data.get("token")
    new_refresh = data.get("refresh_token") or refresh
    new_expires = data.get("expires_at") or data.get("expires_in")
    if isinstance(new_expires, (int, float)) and new_expires < 10_000_000_000:
        from datetime import datetime, timedelta, timezone
        new_expires = (datetime.now(timezone.utc) + timedelta(seconds=int(new_expires))).isoformat()
    if new_access:
        _set_nested_token(credentials, ("accesstoken", "access_token", "token"), new_access)
        _set_nested_token(credentials, ("refreshtoken", "refresh_token"), new_refresh)
        if new_expires is not None:
            _set_nested_token(credentials, ("expiresat", "expires_at", "exp"), new_expires)
        _atomic_write_json(path, credentials)
        tokens["access_token"] = new_access
        tokens["refresh_token"] = new_refresh
        tokens["expires_at"] = new_expires
    return tokens


def _pick_bucket(usage: Dict[str, Any], names: Tuple[str, ...]) -> Dict[str, Any]:
    for name in names:
        value = usage.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _pick_num(obj: Dict[str, Any], names: Tuple[str, ...]) -> Optional[float]:
    for name in names:
        if obj.get(name) is None:
            continue
        try:
            return float(obj[name])
        except (TypeError, ValueError):
            continue
    return None


def map_to_dashboard_payload(usage: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    five = _pick_bucket(usage, ("five_hour", "fiveHour", "FIVE_HOUR", "5h"))
    seven = _pick_bucket(usage, ("seven_day", "sevenDay", "SEVEN_DAY", "7d"))
    sonnet = _pick_bucket(usage, ("seven_day_sonnet", "sevenDaySonnet", "SEVEN_DAY_SONNET"))
    extra = _pick_bucket(usage, ("extra", "EXTRA", "extra_usage"))

    five_pct = _util_to_pct(_pick_num(five, ("utilization", "used_percent", "usedPct")))
    seven_pct = _util_to_pct(_pick_num(seven, ("utilization", "used_percent", "usedPct")))
    sonnet_pct = _util_to_pct(_pick_num(sonnet, ("utilization", "used_percent", "usedPct")))
    extra_pct = _util_to_pct(_pick_num(extra, ("utilization", "used_percent", "usedPct")))

    five_reset_raw = five.get("resets_at") or five.get("reset_at") or five.get("resetAt")
    seven_reset_raw = seven.get("resets_at") or seven.get("reset_at") or seven.get("resetAt")
    sonnet_reset_raw = sonnet.get("resets_at") or sonnet.get("reset_at") or sonnet.get("resetAt")

    spend_used = _pick_num(profile, ("spend_used", "spent", "monthly_spend", "usage"))
    spend_limit = _pick_num(profile, ("spend_limit", "limit", "monthly_limit"))
    extra_usage_balance = _pick_num(profile, ("extra_usage_balance", "extra_balance", "extra_remaining"))

    return {
        "plan_usage_pct": five_pct,
        "plan_usage_reset": format_reset(five_reset_raw),
        "weekly_pct": seven_pct,
        "weekly_reset": format_reset(seven_reset_raw),
        "extra_usage_pct": extra_pct,
        "seven_day_sonnet_pct": sonnet_pct,
        "seven_day_sonnet_reset": format_reset(sonnet_reset_raw),
        "plan_usage_time_pct": compute_timepct(five_reset_raw, 300),
        "weekly_time_pct": compute_timepct(seven_reset_raw, 7 * 24 * 60),
        "seven_day_sonnet_time_pct": compute_timepct(sonnet_reset_raw, 7 * 24 * 60),
        "spend_used": spend_used,
        "spend_limit": spend_limit,
        "spend_reset_text": format_reset(extra.get("resets_at") or extra.get("reset_at")),
        "extra_usage_balance": extra_usage_balance,
        "source": "oauth_usage_api",
    }


async def fetch_usage_payload(config: Dict[str, Any]) -> Dict[str, Any]:
    creds, path = load_credentials(config)
    timeout_s = int(config.get("usage_poll_timeout_seconds", 30))
    tokens = _extract_token_fields(creds)
    tokens = await _refresh_if_needed(tokens, creds, path, timeout_s)
    access = tokens.get("access_token")
    if not access:
        return {"error": "No Claude OAuth access token available."}

    headers = {"Authorization": f"Bearer {access}"}
    async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
        usage_resp = await client.get(USAGE_URL, headers=headers)
        if usage_resp.status_code >= 400:
            return {"error": f"Claude usage API failed ({usage_resp.status_code})."}
        usage_data = usage_resp.json() if usage_resp.content else {}

        profile_data: Dict[str, Any] = {}
        try:
            profile_resp = await client.get(PROFILE_URL, headers=headers)
            if profile_resp.status_code < 400 and profile_resp.content:
                profile_data = profile_resp.json()
        except Exception:
            profile_data = {}

    payload = map_to_dashboard_payload(usage_data if isinstance(usage_data, dict) else {}, profile_data if isinstance(profile_data, dict) else {})
    return payload
