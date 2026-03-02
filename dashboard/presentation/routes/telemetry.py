"""
Telemetry routes: summary, timeseries, calls, sessions, models, tools.
Thin handlers that delegate to the telemetry repository and services.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["telemetry"])

# These will be injected by the app factory
_telemetry_repo = None
_config = None
_balance_service = None


def init(telemetry_repo, config, balance_service=None):
    global _telemetry_repo, _config, _balance_service
    _telemetry_repo = telemetry_repo
    _config = config
    _balance_service = balance_service


def _configured_providers():
    balance_cfg = _config.get("balance", {}) if _config else {}
    return sorted([n for n, c in balance_cfg.items() if isinstance(c, dict)])


def _configured_models():
    model_costs = _config.get("model_costs", {}) if _config else {}
    return sorted(model_costs.keys())


@router.get("/summary")
async def summary(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """Aggregate KPIs: total calls, cost, tokens, error rate."""
    if provider == "":
        provider = None
    if model == "":
        model = None

    since = datetime.fromtimestamp(start / 1000, tz=timezone.utc) if start else None
    until = datetime.fromtimestamp(end / 1000, tz=timezone.utc) if end else None

    result = _telemetry_repo.get_summary(since=since, until=until)
    totals = result["totals"]

    by_provider = [
        {"provider": p, **v}
        for p, v in result["by_provider"].items()
    ]
    by_model = [
        {"model": m, **v}
        for m, v in result["by_model"].items()
    ]

    # Ensure configured providers/models appear even with 0 calls
    prov_set = {e["provider"] for e in by_provider}
    for p in _configured_providers():
        if p not in prov_set:
            by_provider.append({"provider": p, "calls": 0, "cost": 0.0, "tokens": 0})

    model_set = {e["model"] for e in by_model}
    for m in _configured_models():
        if m not in model_set:
            by_model.append({"model": m, "calls": 0, "cost": 0.0, "tokens": 0})

    by_provider.sort(key=lambda x: (-x["calls"], x["provider"]))
    by_model.sort(key=lambda x: (-x["calls"], x["model"]))

    # Use authoritative balance-derived costs when available (no time filter).
    # For filtered queries, fall back to raw DB sums.
    total_cost = totals["cost"]
    if not since and not until and not provider and not model:
        total_cost = await _authoritative_total_cost(by_provider, totals["cost"])

    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_calls": totals["calls"],
        "total_cost": round(total_cost, 6),
        "total_tokens": totals["tokens"],
        "error_rate": totals["error_rate"],
        "error_count": totals["errors"],
        "session_count": totals["sessions"],
        "parse_errors": 0,
        "by_provider": by_provider,
        "by_model": by_model,
        "configured_providers": _configured_providers(),
        "configured_models": _configured_models(),
    }


async def _authoritative_total_cost(by_provider: list, db_total: float) -> float:
    """
    Compute total cost using authoritative balance data (deposits - remaining)
    for each configured provider, falling back to DB cost for unconfigured ones.
    This ensures the summary total matches the Balance Tracker cards exactly.
    """
    if not _config:
        return db_total

    balance_cfg = _config.get("balance", {})
    if not balance_cfg:
        return db_total

    total = 0.0
    db_provider_costs = {p["provider"]: p["cost"] for p in by_provider}
    accounted = set()

    for prov_name, cfg in balance_cfg.items():
        if not isinstance(cfg, dict):
            continue

        # Multi-project providers (e.g. Moonshot with API balance)
        if cfg.get("projects"):
            if _balance_service:
                try:
                    bal = await _balance_service.check_balance(prov_name)
                    deposits = bal.get("total_deposits", 0)
                    remaining = bal.get("remaining", deposits)
                    auth_cost = deposits - remaining
                    total += auth_cost
                    for entry in by_provider:
                        if entry["provider"] == prov_name:
                            entry["cost"] = round(auth_cost, 6)
                except Exception:
                    total += db_provider_costs.get(prov_name, 0.0)
            else:
                total += db_provider_costs.get(prov_name, 0.0)
            accounted.add(prov_name)
            continue

        # Single-ledger providers with verified_usage_cost
        if cfg.get("verified_usage_cost") is not None:
            baseline = float(cfg["verified_usage_cost"])
            incremental = 0.0
            cal_date = cfg.get("verified_usage_date")
            if cal_date and _telemetry_repo:
                from datetime import datetime as dt, timedelta
                local_tz = dt.now().astimezone().tzinfo
                cal_dt = dt.strptime(str(cal_date), "%Y-%m-%d").replace(tzinfo=local_tz) + timedelta(days=1)
                cal_ms = int(cal_dt.timestamp() * 1000)
                incremental = _telemetry_repo.get_cost_since(prov_name, cal_ms)
            auth_cost = baseline + incremental
            total += auth_cost
            for entry in by_provider:
                if entry["provider"] == prov_name:
                    entry["cost"] = round(auth_cost, 6)
            accounted.add(prov_name)
            continue

        # No authoritative source: use DB cost
        total += db_provider_costs.get(prov_name, 0.0)
        accounted.add(prov_name)

    # Add any providers in DB but not in balance config
    for prov_name, cost in db_provider_costs.items():
        if prov_name not in accounted:
            total += cost

    return total


@router.get("/timeseries")
async def timeseries(
    interval: str = Query("hour"),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None),
    end: Optional[int] = Query(None),
):
    """Time-bucketed data for charts."""
    since = datetime.fromtimestamp(start / 1000, tz=timezone.utc) if start else None
    until = datetime.fromtimestamp(end / 1000, tz=timezone.utc) if end else None

    data = _telemetry_repo.get_timeseries(
        interval=interval, since=since, until=until, provider=provider
    )

    # Build provider_costs and provider_tokens maps keyed by epoch ms
    provider_costs = {}
    provider_tokens = {}
    for row in data:
        prov = row["provider"]
        ts = row["timestamp"]
        if prov not in provider_costs:
            provider_costs[prov] = {}
            provider_tokens[prov] = {}
        provider_costs[prov][ts] = row["cost"]
        provider_tokens[prov][ts] = {
            "tokens": row.get("tokens", row.get("total_tokens", 0)),
            "cost": row["cost"],
        }

    for p in _configured_providers():
        provider_costs.setdefault(p, {})
        provider_tokens.setdefault(p, {})

    return {
        "interval": interval,
        "data": data,
        "provider_costs": provider_costs,
        "provider_tokens": provider_tokens,
        "configured_providers": _configured_providers(),
    }


@router.get("/calls")
async def calls(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None),
    end: Optional[int] = Query(None),
):
    """Paginated call list."""
    since = datetime.fromtimestamp(start / 1000, tz=timezone.utc) if start else None
    until = datetime.fromtimestamp(end / 1000, tz=timezone.utc) if end else None
    offset = (page - 1) * per_page

    records = _telemetry_repo.get_records(
        provider=provider, model=model,
        since=since, until=until,
        limit=per_page, offset=offset,
    )

    # Parse tool_names from JSON strings
    for rec in records:
        if isinstance(rec.get("tool_names"), str):
            try:
                rec["tool_names"] = json.loads(rec["tool_names"])
            except (json.JSONDecodeError, TypeError):
                rec["tool_names"] = []

    # Get total count (approximate via summary)
    summary = _telemetry_repo.get_summary(since=since, until=until)
    total = summary["totals"]["calls"]

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page if total > 0 else 0,
        "calls": records,
    }


@router.get("/sessions")
async def sessions_list():
    """Session list with per-session aggregates."""
    sessions = _telemetry_repo.get_sessions()
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str):
    """Single session detail."""
    records = _telemetry_repo.get_session_detail(session_id)
    total_cost = sum(r.get("cost_total", 0) for r in records)
    total_tokens = sum(r.get("tokens_total", 0) for r in records)
    return {
        "session_id": session_id,
        "calls": records,
        "total_calls": len(records),
        "total_cost": round(total_cost, 6),
        "total_tokens": total_tokens,
    }


@router.get("/models")
async def models(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None),
    end: Optional[int] = Query(None),
):
    """Per-model breakdown."""
    stats = _telemetry_repo.get_model_stats()

    model_map = {s["model"]: s for s in stats}
    for m in _configured_models():
        model_map.setdefault(m, {
            "model": m, "calls": 0, "cost": 0.0, "tokens": 0,
            "provider": "", "error_rate": 0.0, "avg_cache_hit_ratio": 0.0,
        })

    models_list = sorted(model_map.values(), key=lambda x: (-x["calls"], x["model"]))
    return {"models": models_list, "configured_models": _configured_models()}


@router.get("/tools")
async def tools():
    """Tool usage breakdown."""
    stats = _telemetry_repo.get_tool_stats()
    return {"tools": stats}
