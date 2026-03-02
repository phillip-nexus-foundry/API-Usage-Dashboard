"""
Spend limit routes: daily/monthly cost caps per provider or project.
"""
import yaml
import logging
from typing import Optional, List
from datetime import datetime, timezone

from fastapi import APIRouter, Body
from sqlalchemy import func

from dashboard.data.models import Record

router = APIRouter(tags=["spendlimits"])
logger = logging.getLogger(__name__)

_config = None
_config_path = None
_db = None


def init(config, config_path, db):
    global _config, _config_path, _db
    _config = config
    _config_path = config_path
    _db = db


def _compute_spend_entry(member_models, daily_limit, monthly_limit, reset_date_str, daily_cost_by_model):
    now = datetime.now(timezone.utc)
    month_start_ms = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    period_start_ms = month_start_ms
    if reset_date_str:
        try:
            reset_dt = datetime.strptime(str(reset_date_str), "%Y-%m-%d")
            while reset_dt > now:
                if reset_dt.month == 1:
                    reset_dt = reset_dt.replace(year=reset_dt.year - 1, month=12)
                else:
                    reset_dt = reset_dt.replace(month=reset_dt.month - 1)
            period_start_ms = int(reset_dt.timestamp() * 1000)
        except Exception:
            pass

    period_cost = 0.0
    if member_models and _db:
        with _db.session() as session:
            result = session.query(
                func.coalesce(func.sum(Record.cost_total), 0.0)
            ).filter(
                Record.timestamp >= period_start_ms,
                Record.model.in_(member_models),
            ).scalar()
            period_cost = float(result or 0.0)

    daily_cost = sum(daily_cost_by_model.get(m, 0.0) for m in member_models)

    next_reset = None
    if reset_date_str:
        try:
            next_dt = datetime.strptime(str(reset_date_str), "%Y-%m-%d")
            while next_dt <= now:
                if next_dt.month == 12:
                    next_dt = next_dt.replace(year=next_dt.year + 1, month=1)
                else:
                    next_dt = next_dt.replace(month=next_dt.month + 1)
            next_reset = next_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return {
        "daily_limit": daily_limit,
        "monthly_limit": monthly_limit,
        "reset_date": reset_date_str,
        "next_reset": next_reset,
        "models": member_models,
        "usage_daily": round(daily_cost, 6),
        "usage_period": round(period_cost, 6),
    }


@router.get("/spendlimits")
async def spendlimits():
    """Spend limit config and current usage."""
    spend_cfg = (_config or {}).get("spend_limits", {})

    # Pre-fetch daily costs per model
    daily_cost_by_model = {}
    if _db:
        now = datetime.now(timezone.utc)
        today_start_ms = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        with _db.session() as session:
            rows = session.query(
                Record.model,
                func.coalesce(func.sum(Record.cost_total), 0.0),
            ).filter(Record.timestamp >= today_start_ms).group_by(Record.model).all()
            daily_cost_by_model = {row[0]: float(row[1]) for row in rows}

    providers = {}
    for provider_name, prov_cfg in spend_cfg.items():
        if not isinstance(prov_cfg, dict):
            continue

        if prov_cfg.get("projects"):
            for proj_name, proj_cfg in prov_cfg["projects"].items():
                if not isinstance(proj_cfg, dict):
                    continue
                key = f"{provider_name}/{proj_name}"
                entry = _compute_spend_entry(
                    proj_cfg.get("models", []),
                    proj_cfg.get("daily"),
                    proj_cfg.get("monthly"),
                    proj_cfg.get("reset_date"),
                    daily_cost_by_model,
                )
                entry["provider"] = provider_name
                entry["project"] = proj_name
                providers[key] = entry
        else:
            providers[provider_name] = _compute_spend_entry(
                prov_cfg.get("models", []),
                prov_cfg.get("daily"),
                prov_cfg.get("monthly"),
                prov_cfg.get("reset_date"),
                daily_cost_by_model,
            )

    return {"providers": providers}


@router.post("/spendlimits")
async def spendlimits_update(
    provider: str = Body(...),
    project: Optional[str] = Body(None),
    daily: Optional[float] = Body(None),
    monthly: Optional[float] = Body(None),
    reset_date: Optional[str] = Body(None),
    models: Optional[List[str]] = Body(None),
):
    """Set or update spend limits."""
    if "spend_limits" not in _config:
        _config["spend_limits"] = {}

    if project:
        prov_cfg = _config["spend_limits"].setdefault(provider, {})
        if "projects" not in prov_cfg:
            prov_cfg["projects"] = {}
        target = prov_cfg["projects"].setdefault(project, {})
    else:
        target = _config["spend_limits"].setdefault(provider, {})

    for key, val in [("daily", daily), ("monthly", monthly)]:
        if val is not None:
            if val > 0:
                target[key] = val
            else:
                target.pop(key, None)

    if reset_date is not None:
        if reset_date:
            target["reset_date"] = reset_date
        else:
            target.pop("reset_date", None)

    if models is not None:
        target["models"] = models

    try:
        with open(_config_path, "w") as f:
            yaml.dump(_config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return {"error": f"Failed to save: {e}", "status": 500}

    label = f"{provider}/{project}" if project else provider
    logger.info(f"Updated spend limits for {label}: daily={daily}, monthly={monthly}")
    return {"status": "ok", "provider": provider, "project": project}
