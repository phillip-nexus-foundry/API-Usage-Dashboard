"""
Canonical telemetry schema for API usage tracking.
24-field TelemetryRecord dataclass with model costs and cost computation.
"""
from dataclasses import dataclass
from typing import Optional
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
MODEL_COSTS = {
    # Anthropic Claude models (current naming)
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
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
}


# Moonshot web operation surcharge (per call)
WEB_TOOL_SURCHARGE = 0.01
WEB_TOOL_NAMES = {"web_search", "web_fetch", "browser"}


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
    costs = MODEL_COSTS.get(model, {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    })

    cost = (
        (tokens_input * costs["input"] / 1_000_000) +
        (tokens_output * costs["output"] / 1_000_000) +
        (tokens_cache_read * costs["cache_read"] / 1_000_000) +
        (tokens_cache_write * costs["cache_write"] / 1_000_000)
    )

    # Add Moonshot web operation surcharges
    if tool_names:
        web_calls = sum(1 for t in tool_names if t in WEB_TOOL_NAMES)
        cost += web_calls * WEB_TOOL_SURCHARGE

    return round(cost, 6)
