"""
Cost computation service.
Centralizes all cost calculation logic with cache-aware pricing
and API-reported cost cross-checking.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from dashboard.data.repositories.pricing_repo import SQLAlchemyPricingRepo

logger = logging.getLogger(__name__)

# Moonshot web operation surcharge (per call)
WEB_TOOL_SURCHARGE = 0.01
WEB_TOOL_NAMES = {"web_search", "web_fetch", "browser"}


def _infer_provider(model: str) -> str:
    """Infer provider from model name."""
    m = model.lower()
    if "claude" in m:
        return "anthropic"
    if "kimi" in m or "moonshot" in m:
        return "moonshot"
    if "minimax" in m or "m2.5" in m:
        return "minimax"
    return "unknown"


class CostResult:
    """Result of a cost computation with confidence metadata."""

    def __init__(
        self,
        cost_input: float = 0.0,
        cost_output: float = 0.0,
        cost_cache_read: float = 0.0,
        cost_cache_write: float = 0.0,
        cost_web_surcharge: float = 0.0,
        cost_total: float = 0.0,
        cost_api_reported: Optional[float] = None,
        confidence: float = 0.7,
        source: str = "computed",
        drift_pct: Optional[float] = None,
    ):
        self.cost_input = cost_input
        self.cost_output = cost_output
        self.cost_cache_read = cost_cache_read
        self.cost_cache_write = cost_cache_write
        self.cost_web_surcharge = cost_web_surcharge
        self.cost_total = cost_total
        self.cost_api_reported = cost_api_reported
        self.confidence = confidence
        self.source = source
        self.drift_pct = drift_pct

    def to_dict(self) -> dict:
        return {
            "cost_input": round(self.cost_input, 6),
            "cost_output": round(self.cost_output, 6),
            "cost_cache_read": round(self.cost_cache_read, 6),
            "cost_cache_write": round(self.cost_cache_write, 6),
            "cost_web_surcharge": round(self.cost_web_surcharge, 6),
            "cost_total": round(self.cost_total, 6),
            "cost_api_reported": round(self.cost_api_reported, 6) if self.cost_api_reported else None,
            "confidence": self.confidence,
            "source": self.source,
            "drift_pct": round(self.drift_pct, 2) if self.drift_pct else None,
        }


class CostService:
    """Computes and validates costs using pricing data and optional API-reported costs."""

    def __init__(self, pricing_repo: SQLAlchemyPricingRepo, config: dict):
        self._pricing = pricing_repo
        self._config = config
        # In-memory cache of model_costs from config for fast lookups
        self._config_costs = config.get("model_costs", {})

    def compute_cost(
        self,
        model: str,
        tokens_input: int,
        tokens_output: int,
        tokens_cache_read: int = 0,
        tokens_cache_write_billable: int = 0,
        tool_names: Optional[list] = None,
        cost_api_reported: Optional[float] = None,
        at_time: Optional[datetime] = None,
    ) -> CostResult:
        """
        Compute cost for a single API call.

        Priority:
        1. If cost_api_reported is available and reasonable, use it (confidence 0.95)
        2. Otherwise compute from pricing table (confidence 0.7)
        3. Cross-check computed vs API-reported and flag drift
        """
        provider = _infer_provider(model)

        # Get pricing (try DB first, fall back to config)
        pricing = self._get_pricing(provider, model, at_time)

        # Compute from token counts
        cost_input = tokens_input * pricing["input"] / 1_000_000
        cost_output = tokens_output * pricing["output"] / 1_000_000
        cost_cache_read = tokens_cache_read * pricing["cache_read"] / 1_000_000
        cost_cache_write = tokens_cache_write_billable * pricing["cache_write"] / 1_000_000

        # Web surcharge (Moonshot)
        cost_web = 0.0
        if tool_names and any(t in WEB_TOOL_NAMES for t in tool_names):
            cost_web = WEB_TOOL_SURCHARGE

        computed_total = cost_input + cost_output + cost_cache_read + cost_cache_write + cost_web

        # Determine final cost and confidence
        result = CostResult(
            cost_input=cost_input,
            cost_output=cost_output,
            cost_cache_read=cost_cache_read,
            cost_cache_write=cost_cache_write,
            cost_web_surcharge=cost_web,
            cost_total=computed_total,
            cost_api_reported=cost_api_reported,
            confidence=0.7,
            source="computed",
        )

        # If API-reported cost is available, cross-check
        if cost_api_reported is not None and cost_api_reported > 0:
            if computed_total > 0:
                drift = abs(computed_total - cost_api_reported) / cost_api_reported * 100
                result.drift_pct = drift

                if drift < 2:
                    # Close enough, use API-reported
                    result.cost_total = cost_api_reported
                    result.confidence = 0.95
                    result.source = "api_reported"
                elif drift < 10:
                    # Moderate drift, use API but flag
                    result.cost_total = cost_api_reported
                    result.confidence = 0.85
                    result.source = "api_reported_with_drift"
                    logger.info(
                        f"Cost drift for {model}: computed={computed_total:.6f} "
                        f"vs api={cost_api_reported:.6f} ({drift:.1f}%)"
                    )
                else:
                    # Large drift, investigate
                    result.cost_total = cost_api_reported
                    result.confidence = 0.75
                    result.source = "api_reported_high_drift"
                    logger.warning(
                        f"HIGH cost drift for {model}: computed={computed_total:.6f} "
                        f"vs api={cost_api_reported:.6f} ({drift:.1f}%)"
                    )
            else:
                # No computed cost (unknown model?), trust API
                result.cost_total = cost_api_reported
                result.confidence = 0.9
                result.source = "api_reported"

        return result

    def _get_pricing(
        self, provider: str, model: str, at_time: Optional[datetime] = None
    ) -> dict:
        """Get pricing for a model. Tries DB first, falls back to config."""
        # Try pricing history DB
        db_price = self._pricing.get_price(provider, model, at_time)
        if db_price:
            return {
                "input": db_price["input_price"],
                "output": db_price["output_price"],
                "cache_read": db_price["cache_read_price"],
                "cache_write": db_price["cache_write_price"],
            }

        # Fall back to config.yaml model_costs
        canonical = self._canonicalize_model(model)
        config_price = self._config_costs.get(canonical, {})
        return {
            "input": config_price.get("input", 0),
            "output": config_price.get("output", 0),
            "cache_read": config_price.get("cache_read", 0),
            "cache_write": config_price.get("cache_write", 0),
        }

    @staticmethod
    def _canonicalize_model(model: str) -> str:
        """Normalize model aliases to canonical pricing keys."""
        if not model:
            return model
        lower = model.lower()
        if lower.startswith("kimi-k2.5"):
            return "kimi-k2.5"
        if lower.startswith("moonshot-v1-8k"):
            return "moonshot-v1-8k"
        if lower.startswith("minimax-m2.5"):
            return "MiniMax-M2.5"
        return model

    @staticmethod
    def anthropic_billable_cache_write_delta(
        previous_highwater: Optional[int], current_raw: int
    ) -> int:
        """
        Convert Anthropic cumulative cacheWrite telemetry into per-call billable tokens.
        Uses high-watermark strategy.
        """
        if previous_highwater is None:
            return max(0, current_raw)
        if current_raw > previous_highwater:
            return current_raw - previous_highwater
        return 0
