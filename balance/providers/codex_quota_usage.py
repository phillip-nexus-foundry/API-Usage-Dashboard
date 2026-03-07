"""Codex quota usage from local session JSONL files (Rainmeter parity)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from balance.usage_windows import clamp_percent, compute_timepct, format_reset


def resolve_sessions_root(config: Dict[str, Any]) -> Path:
    path = (
        config.get("codex_sessions_path")
        or os.environ.get("CODEX_SESSIONS_PATH")
        or "~/.codex/sessions"
    )
    return Path(os.path.expanduser(path))


def _iter_session_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def _safe_json_loads(line: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _extract_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {}
    return payload if isinstance(payload, dict) else {}


def _bucket_used_pct(bucket: Dict[str, Any]) -> Optional[float]:
    if not isinstance(bucket, dict):
        return None
    for key in ("used_percent", "usedPct", "utilization"):
        if bucket.get(key) is not None:
            try:
                value = float(bucket[key])
                if 0.0 <= value <= 1.0:
                    value *= 100.0
                return clamp_percent(value)
            except (TypeError, ValueError):
                pass
    remaining = bucket.get("remaining_percent") or bucket.get("remainingPct")
    if remaining is not None:
        try:
            return clamp_percent(100.0 - float(remaining))
        except (TypeError, ValueError):
            pass
    rem = bucket.get("remaining")
    lim = bucket.get("limit")
    if rem is not None and lim not in (None, 0):
        try:
            return clamp_percent((1.0 - (float(rem) / float(lim))) * 100.0)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return None


def _extract_streams(rate_limits: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    global_stream: Dict[str, Any] = {}
    model_stream: Dict[str, Any] = {}
    if isinstance(rate_limits, dict):
        # Single-stream shape: {"limit_id":"codex","primary":{...},"secondary":{...}}
        if isinstance(rate_limits.get("primary"), dict) or isinstance(rate_limits.get("secondary"), dict):
            limit_id = str(rate_limits.get("limit_id") or "codex").lower()
            if limit_id == "codex":
                global_stream = rate_limits
            else:
                model_stream = rate_limits
        if isinstance(rate_limits.get("global"), dict):
            global_stream = rate_limits["global"]
        if isinstance(rate_limits.get("model"), dict):
            model_stream = rate_limits["model"]
    if isinstance(rate_limits, list):
        for stream in rate_limits:
            if not isinstance(stream, dict):
                continue
            limit_id = str(stream.get("limit_id") or stream.get("id") or "").lower()
            if limit_id == "codex" and not global_stream:
                global_stream = stream
            elif limit_id != "codex" and not model_stream:
                model_stream = stream
    return global_stream, model_stream


def _latest_rate_limits(root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    for path in _iter_session_files(root):
        try:
            with path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            continue
        for line in reversed(lines):
            evt = _safe_json_loads(line)
            if not evt:
                continue
            payload = _extract_payload(evt)
            if payload.get("type") != "token_count":
                continue
            rl = payload.get("rate_limits") or payload.get("rateLimits")
            if rl is None:
                continue
            if isinstance(rl, (dict, list)):
                return {"rate_limits": rl, "payload": payload}, str(path)
    return None, None


def map_to_dashboard_payload(rate_limits_payload: Dict[str, Any], source_file: Optional[str]) -> Dict[str, Any]:
    rate_limits = rate_limits_payload.get("rate_limits")
    payload = rate_limits_payload.get("payload", {})
    global_stream, model_stream = _extract_streams(rate_limits)
    global_primary = global_stream.get("primary", {})
    global_secondary = global_stream.get("secondary", {})
    model_primary = model_stream.get("primary", {})
    model_secondary = model_stream.get("secondary", {})

    global_5h_used = _bucket_used_pct(global_primary)
    global_7d_used = _bucket_used_pct(global_secondary)
    model_5h_used = _bucket_used_pct(model_primary)
    model_7d_used = _bucket_used_pct(model_secondary)

    global_7d_reset_raw = global_secondary.get("resets_at") or global_secondary.get("reset_at")
    global_5h_reset_raw = global_primary.get("resets_at") or global_primary.get("reset_at")
    model_name = payload.get("model") or payload.get("model_name")

    five_hour_remaining = None if global_5h_used is None else clamp_percent(100.0 - global_5h_used)
    weekly_remaining = None if global_7d_used is None else clamp_percent(100.0 - global_7d_used)

    return {
        "five_hour_remaining_pct": five_hour_remaining,
        "weekly_remaining_pct": weekly_remaining,
        "weekly_reset": format_reset(global_7d_reset_raw),
        "global_5h_used_pct": global_5h_used,
        "global_7d_used_pct": global_7d_used,
        "global_5h_reset": format_reset(global_5h_reset_raw),
        "global_7d_reset": format_reset(global_7d_reset_raw),
        "global_5h_time_pct": compute_timepct(global_5h_reset_raw, 300),
        "global_7d_time_pct": compute_timepct(global_7d_reset_raw, 7 * 24 * 60),
        "model_name": model_name,
        "model_5h_used_pct": model_5h_used,
        "model_7d_used_pct": model_7d_used,
        "source_file": source_file,
        "source": "local_codex_sessions",
    }


def fetch_usage_payload(config: Dict[str, Any]) -> Dict[str, Any]:
    root = resolve_sessions_root(config)
    if not root.exists():
        return {"error": f"Codex sessions path not found: {root}"}
    latest, src = _latest_rate_limits(root)
    if not latest:
        return {"error": "No Codex rate-limit events found in session JSONL files."}
    return map_to_dashboard_payload(latest, src)
