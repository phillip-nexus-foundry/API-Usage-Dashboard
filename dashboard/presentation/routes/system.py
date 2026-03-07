"""
System routes: config, health, evals.
"""
from dataclasses import asdict

from fastapi import APIRouter

router = APIRouter(tags=["system"])

# Injected by app factory
_config = None
_db = None
_evaluator = None
_ingestion_service = None
_balance_service = None
_balance_poller = None


def init(
    config,
    db,
    evaluator=None,
    ingestion_service=None,
    balance_service=None,
    balance_poller=None,
):
    global _config, _db, _evaluator, _ingestion_service, _balance_service, _balance_poller
    _config = config
    _db = db
    _evaluator = evaluator
    _ingestion_service = ingestion_service
    _balance_service = balance_service
    _balance_poller = balance_poller


@router.get("/config")
async def get_config():
    """Current configuration (safe subset)."""
    return {
        "model_costs": _config.get("model_costs", {}),
        "rate_limits": _config.get("rate_limits", {}),
        "spend_limits": _config.get("spend_limits", {}),
        "balance_providers": list((_config.get("balance") or {}).keys()),
    }


@router.get("/health")
async def health():
    """System health check."""
    db_status = "connected"
    db_type = "postgresql" if _db.is_postgres else "sqlite"
    try:
        from dashboard.data.models import Record
        with _db.session() as session:
            count = session.query(Record).count()
    except Exception as e:
        db_status = f"error: {e}"
        count = None

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "database_type": db_type,
        "record_count": count,
        "sessions_dir": _config.get("sessions_dir", ""),
    }


@router.get("/evals")
async def evals():
    """Run evaluations and return scores/grades."""
    if _evaluator is None:
        return {"evals": [], "error": "Evaluator not configured"}
    # The evaluator needs the old reader interface; bridge it
    # For now, return empty until evaluator is refactored
    return {"evals": []}


@router.post("/refresh")
async def refresh():
    """
    Force a full refresh cycle:
    1) ingest new telemetry
    2) poll resource snapshots
    3) recompute all balances
    """
    out = {
        "status": "ok",
        "ingestion": None,
        "resource_poll": None,
        "balance_refresh": None,
    }

    try:
        if _ingestion_service is not None:
            ingestion_result = _ingestion_service.scan_all()
            out["ingestion"] = asdict(ingestion_result)

        if _balance_poller is not None:
            polled = await _balance_poller.poll_all(
                ["anthropic", "elevenlabs", "codex_cli", "moonshot", "minimax"]
            )
            out["resource_poll"] = {
                "polled": len(polled),
                "providers": [p.get("provider") for p in polled if isinstance(p, dict)],
            }

        if _balance_service is not None:
            balances = await _balance_service.check_all_balances()
            out["balance_refresh"] = {
                "providers": len(balances),
                "names": sorted(list(balances.keys())),
            }

        return out
    except Exception as e:
        out["status"] = "error"
        out["error"] = str(e)
        return out
