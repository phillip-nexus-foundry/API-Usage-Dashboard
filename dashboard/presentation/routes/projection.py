"""
Projection routes: daily costs, monthly forecast, burn rate.
"""
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["projection"])

# Injected by app factory
_projection_service = None
_telemetry_repo = None
_config = None


def init(projection_service, telemetry_repo, config):
    global _projection_service, _telemetry_repo, _config
    _projection_service = projection_service
    _telemetry_repo = telemetry_repo
    _config = config


def _configured_providers():
    balance_cfg = _config.get("balance", {}) if _config else {}
    return sorted([n for n, c in balance_cfg.items() if isinstance(c, dict)])


def _configured_models():
    return sorted((_config or {}).get("model_costs", {}).keys())


@router.get("/cost/daily")
async def cost_daily(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=3650),
):
    """Daily cost breakdown."""
    daily = _projection_service.get_daily_costs(days=days, provider=provider)
    return {
        "daily": daily,
        "window_days": days,
        "configured_providers": _configured_providers(),
        "configured_models": _configured_models(),
    }


@router.get("/cost/projection")
async def cost_projection():
    """Monthly projection from 7-day trailing average."""
    projection = _projection_service.get_projection(days_lookback=7)
    return projection
