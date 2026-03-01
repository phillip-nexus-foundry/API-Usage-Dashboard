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


def init(config, db, evaluator=None):
    global _config, _db, _evaluator
    _config = config
    _db = db
    _evaluator = evaluator


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
