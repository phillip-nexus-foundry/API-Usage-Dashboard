"""
Rate limit routes: per-model-family RPM/TPM usage tracking.
"""
import time
import yaml
import logging
from typing import Optional, List
from datetime import datetime, timezone

from fastapi import APIRouter, Body
from sqlalchemy import func, case

from dashboard.data.models import Record

router = APIRouter(tags=["ratelimits"])
logger = logging.getLogger(__name__)

_config = None
_config_path = None
_db = None


def init(config, config_path, db):
    global _config, _config_path, _db
    _config = config
    _config_path = config_path
    _db = db


def _configured_providers():
    balance_cfg = (_config or {}).get("balance", {})
    return [k for k, v in balance_cfg.items() if isinstance(v, dict)]


def _usage_by_field(field, since_ms):
    """Query per-model or per-provider usage since a timestamp."""
    col = Record.model if field == "model" else Record.provider
    with _db.session() as session:
        q = session.query(
            col,
            func.count(Record.id).label("calls"),
            func.coalesce(func.sum(Record.tokens_total), 0).label("tokens"),
            func.coalesce(func.sum(Record.tokens_input + Record.tokens_cache_read + Record.tokens_cache_write), 0).label("input_tokens"),
            func.coalesce(func.sum(Record.tokens_output), 0).label("output_tokens"),
        ).filter(Record.timestamp >= since_ms).group_by(col)
        return {row[0]: {"rpm": row[1], "tpm": row[2], "input_tpm": row[3], "output_tpm": row[4]} for row in q.all()}


def _usage_1h_by_field(field, since_ms):
    col = Record.model if field == "model" else Record.provider
    with _db.session() as session:
        q = session.query(
            col,
            func.count(Record.id).label("calls"),
            func.coalesce(func.sum(Record.tokens_total), 0).label("tokens"),
        ).filter(Record.timestamp >= since_ms).group_by(col)
        return {row[0]: {"rph": row[1], "tph": row[2]} for row in q.all()}


def _rate_limit_errors(field, since_ms):
    col = Record.model if field == "model" else Record.provider
    with _db.session() as session:
        q = session.query(
            col,
            func.max(Record.timestamp).label("last_error"),
            func.count(Record.id).label("error_count"),
        ).filter(
            Record.timestamp >= since_ms,
            Record.stop_reason == 'error',
            Record.tokens_total == 0,
        ).group_by(col)
        return {row[0]: {"last_error": row[1], "error_count": row[2]} for row in q.all()}


@router.get("/ratelimits")
async def ratelimits():
    """Rate limit config and current usage metrics per model family."""
    rate_cfg = (_config or {}).get("rate_limits", {})

    now_ms = int(time.time() * 1000)
    one_min_ago = now_ms - 60_000
    five_min_ago = now_ms - 300_000
    one_hour_ago = now_ms - 3_600_000

    raw_1m = _usage_by_field("model", one_min_ago)
    raw_5m = _usage_by_field("model", five_min_ago)
    raw_1h = _usage_1h_by_field("model", one_hour_ago)
    model_errors = _rate_limit_errors("model", one_hour_ago)

    provider_1m = _usage_by_field("provider", one_min_ago)
    provider_5m = _usage_by_field("provider", five_min_ago)
    provider_1h = _usage_1h_by_field("provider", one_hour_ago)
    provider_errors = _rate_limit_errors("provider", one_hour_ago)

    # All known models
    with _db.session() as session:
        all_models = [r[0] for r in session.query(Record.model).distinct().order_by(Record.model).all()]

    # Aggregate per family
    families = {}
    for family_name, family_cfg in rate_cfg.items():
        if not isinstance(family_cfg, dict):
            continue
        member_models = family_cfg.get("models", [])
        meta_keys = {"models", "auto_detected"}
        limits = {k: v for k, v in family_cfg.items() if k not in meta_keys}

        agg_1m = {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}
        agg_5m = {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}
        agg_1h = {"rph": 0, "tph": 0}
        agg_errors = {"last_error": 0, "error_count": 0}
        for mdl in member_models:
            if mdl in raw_1m:
                for k in agg_1m:
                    agg_1m[k] += raw_1m[mdl].get(k, 0)
            if mdl in raw_5m:
                for k in agg_5m:
                    agg_5m[k] += raw_5m[mdl].get(k, 0)
            if mdl in raw_1h:
                for k in agg_1h:
                    agg_1h[k] += raw_1h[mdl].get(k, 0)
            if mdl in model_errors:
                err = model_errors[mdl]
                agg_errors["error_count"] += err["error_count"]
                agg_errors["last_error"] = max(agg_errors["last_error"], err["last_error"])

        families[family_name] = {
            "limits": limits,
            "models": member_models,
            "auto_detected": bool(family_cfg.get("auto_detected")),
            "usage_1m": agg_1m,
            "usage_5m": agg_5m,
            "usage_1h": agg_1h,
            "rate_limit_errors": agg_errors if agg_errors["error_count"] > 0 else None,
        }

    providers = {}
    for provider_name in _configured_providers():
        provider_cfg = rate_cfg.get(provider_name, {})
        if not isinstance(provider_cfg, dict):
            provider_cfg = {}
        provider_models = provider_cfg.get("models", [])
        limits = {k: v for k, v in provider_cfg.items() if k not in {"models", "auto_detected"}}
        providers[provider_name] = {
            "limits": limits,
            "models": provider_models,
            "auto_detected": bool(provider_cfg.get("auto_detected")),
            "usage_1m": provider_1m.get(provider_name, {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}),
            "usage_5m": provider_5m.get(provider_name, {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}),
            "usage_1h": provider_1h.get(provider_name, {"rph": 0, "tph": 0}),
            "rate_limit_errors": provider_errors.get(provider_name),
        }

    return {
        "families": families,
        "providers": providers,
        "configured_providers": _configured_providers(),
        "all_models": all_models,
    }


@router.post("/ratelimits")
async def ratelimits_update(
    family: str = Body(...),
    rpm: Optional[int] = Body(None),
    tpm: Optional[int] = Body(None),
    rph: Optional[int] = Body(None),
    tph: Optional[int] = Body(None),
    models: Optional[List[str]] = Body(None),
):
    """Set or update rate limits for a model family."""
    if "rate_limits" not in _config:
        _config["rate_limits"] = {}

    family_cfg = _config["rate_limits"].setdefault(family, {})

    for key, val in [("rpm", rpm), ("tpm", tpm), ("rph", rph), ("tph", tph)]:
        if val is not None:
            if val > 0:
                family_cfg[key] = val
            else:
                family_cfg.pop(key, None)

    if models is not None:
        family_cfg["models"] = models

    family_cfg.pop("auto_detected", None)

    if not any(k for k in family_cfg if k not in ("models", "auto_detected")):
        if not family_cfg.get("models"):
            _config["rate_limits"].pop(family, None)

    try:
        with open(_config_path, "w") as f:
            yaml.dump(_config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return {"error": f"Failed to save: {e}", "status": 500}

    return {"status": "ok", "family": family, "limits": _config["rate_limits"].get(family, {})}
