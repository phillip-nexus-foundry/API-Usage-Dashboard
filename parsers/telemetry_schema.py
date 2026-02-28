"""
Canonical telemetry schema for API usage tracking.
24-field TelemetryRecord dataclass with model costs and cost computation.
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class TelemetryRecord:
    """Complete telemetry record from OpenClaw session JSONL."""
    call_id: str
    session_id: str
    parent_id: Optional[str]
    timestamp: int  # epoch milliseconds
    timestamp_iso: str
    api: str
    provider: str
    model: str
    stop_reason: str
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_write: int
    tokens_total: int
    cache_hit_ratio: float  # 0.0-1.0, computed as cache_read / (cache_read + input)
    cost_input: float
    cost_output: float
    cost_cache_read: float
    cost_cache_write: float
    cost_total: float
    has_thinking: bool
    has_tool_calls: bool
    tool_names: list  # List[str]
    content_length: int
    is_error: bool


# Model costs: per 1M tokens (input, output, cache_read, cache_write)
# From Anthropic pricing and Moonshot pricing sheets
#
# WARNING: OpenClaw telemetry reports cumulative cache size in the cacheWrite
# field for Anthropic models, not the tokens actually written in that call.
# This can cause massive overcharging if cache_write costs are applied to
# every call. See: fixes/cache-write-cost-bug.md for details.
MODEL_COSTS = {
    # Anthropic Claude models (current naming)
    "claude-opus-4-6": {
        "input": 5.00,
        "output": 25.00,
        "cache_read": 0.50,
        "cache_write": 6.25,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
    # Older Anthropic model names (still in some session files)
    "claude-3-5-sonnet-20241022": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-3-5-haiku-20241022": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_write": 1.00,
    },
    "claude-3-haiku-20240307": {
        "input": 0.25,
        "output": 1.25,
        "cache_read": 0.03,
        "cache_write": 0.30,
    },
    # Moonshot models (USD pricing from platform.moonshot.ai)
    "kimi-k2.5": {
        "input": 0.60,
        "output": 3.00,
        "cache_read": 0.10,
        "cache_write": 0.60,
    },
    "moonshot-v1-8k": {
        "input": 0.20,
        "output": 2.00,
        "cache_read": 0.10,
        "cache_write": 0.20,
    },
    # OpenClaw internal (no cost)
    "delivery-mirror": {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    },
    "gateway-injected": {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    },
    # MiniMax models (USD pricing from platform.minimax.io)
    "MiniMax-M2.5": {
        "input": 0.30,
        "output": 1.20,
        "cache_read": 0.03,
        "cache_write": 0.375,
    },
}


# Moonshot web operation surcharge (per call)
WEB_TOOL_SURCHARGE = 0.01
WEB_TOOL_NAMES = {"web_search", "web_fetch", "browser"}
# Bump whenever pricing semantics or canonicalization rules change.
COST_LOGIC_VERSION = "2026-02-28-moonshot-web-per-call-v1"


def is_anthropic_model(model: str) -> bool:
    """Best-effort model classifier for Anthropic Claude model IDs."""
    return model.startswith("claude")


def canonicalize_model_for_cost(model: str) -> str:
    """
    Normalize common model aliases/snapshots to canonical pricing keys.
    Keeps existing exact keys unchanged.
    """
    if model in MODEL_COSTS:
        return model

    m = (model or "").strip()
    lower = m.lower()

    if lower.startswith("kimi-k2.5"):
        return "kimi-k2.5"
    if lower.startswith("moonshot-v1-8k"):
        return "moonshot-v1-8k"
    if lower.startswith("minimax-m2.5"):
        return "MiniMax-M2.5"

    return m


def anthropic_billable_cache_write_delta(previous_highwater: Optional[int], current_raw: int) -> int:
    """
    Convert Anthropic cumulative cacheWrite telemetry into per-call billable tokens
    using a high-watermark strategy:
    - First seen value is billable (assume stream starts at billing boundary)
    - Subsequent values only bill when exceeding the historical max
    - Decreases/resets do not create negative bills and do not re-bill old ranges
    """
    if previous_highwater is None:
        return max(0, current_raw)
    if current_raw > previous_highwater:
        return current_raw - previous_highwater
    return 0


def compute_cost_breakdown(
    model: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int,
    tokens_cache_write_billable: int,
    tool_names: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Compute per-component and total USD costs.

    tokens_cache_write_billable should be the per-call billable cache-write tokens.
    For Anthropic telemetry this may differ from raw cacheWrite if the source value
    is cumulative.
    """
    canonical_model = canonicalize_model_for_cost(model)
    costs = MODEL_COSTS.get(canonical_model, {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    })

    cost_input = tokens_input * costs["input"] / 1_000_000
    cost_output = tokens_output * costs["output"] / 1_000_000
    cost_cache_read = tokens_cache_read * costs["cache_read"] / 1_000_000
    cost_cache_write = tokens_cache_write_billable * costs["cache_write"] / 1_000_000

    web_surcharge_count = 0
    if tool_names:
        # Moonshot web pricing is charged once per API call when any web tool is used.
        web_surcharge_count = 1 if any(t in WEB_TOOL_NAMES for t in tool_names) else 0
    cost_web_surcharge = web_surcharge_count * WEB_TOOL_SURCHARGE

    total = cost_input + cost_output + cost_cache_read + cost_cache_write + cost_web_surcharge

    return {
        "cost_input": round(cost_input, 6),
        "cost_output": round(cost_output, 6),
        "cost_cache_read": round(cost_cache_read, 6),
        "cost_cache_write": round(cost_cache_write, 6),
        "cost_web_surcharge": round(cost_web_surcharge, 6),
        "cost_total": round(total, 6),
        "web_surcharge_count": web_surcharge_count,
    }


def compute_dollar_cost(
    model: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int,
    tokens_cache_write: int,
    tool_names: list = None,
) -> float:
    """
    Independently compute USD cost from token counts.
    Includes Moonshot web operation surcharges ($0.01 per web_search/web_fetch/browser call).

    Args:
        model: Model identifier (e.g., "claude-opus-4-6")
        tokens_input: Input token count
        tokens_output: Output token count
        tokens_cache_read: Cache read token count
        tokens_cache_write: Cache write token count
        tool_names: List of tool names used in this call (for web surcharges)

    Returns:
        Total USD cost (float, rounded to 6 decimals)
    """
    breakdown = compute_cost_breakdown(
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cache_read=tokens_cache_read,
        tokens_cache_write_billable=tokens_cache_write,
        tool_names=tool_names,
    )
    return breakdown["cost_total"]
