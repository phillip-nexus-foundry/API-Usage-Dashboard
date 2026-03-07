"""Shared usage-window helpers (Rainmeter parity)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def clamp_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 0:
        return 0.0
    if num > 100:
        return 100.0
    return round(num, 1)


def parse_timestamp(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def format_reset(value) -> Optional[str]:
    dt = parse_timestamp(value)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    seconds = int((dt - now).total_seconds())
    if seconds <= 0:
        return "Resets soon"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"Resets in {days}d {hours}h"
    if hours > 0:
        return f"Resets in {hours}h {minutes}m"
    return f"Resets in {minutes}m"


def compute_timepct(reset_value, window_minutes: Optional[int]) -> Optional[float]:
    if not window_minutes or window_minutes <= 0:
        return None
    reset_dt = parse_timestamp(reset_value)
    if reset_dt is None:
        return None
    now = datetime.now(timezone.utc)
    remaining = max(0.0, (reset_dt - now).total_seconds())
    window_seconds = float(window_minutes) * 60.0
    elapsed = max(0.0, window_seconds - remaining)
    pct = (elapsed / window_seconds) * 100.0
    return clamp_percent(pct)

