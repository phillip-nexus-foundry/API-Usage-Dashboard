"""
Balance routes: provider balances, top-up, reconciliation history.
"""
import yaml
import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Body

router = APIRouter(tags=["balance"])
logger = logging.getLogger(__name__)

# Injected by app factory
_balance_service = None
_balance_repo = None
_config = None
_config_path = None


def init(balance_service, balance_repo, config, config_path):
    global _balance_service, _balance_repo, _config, _config_path
    _balance_service = balance_service
    _balance_repo = balance_repo
    _config = config
    _config_path = config_path


@router.get("/balance")
async def balance():
    """Provider balances with reconciliation data."""
    balances = await _balance_service.check_all_balances()

    # Attach reconciliation metadata
    for provider_name, data in balances.items():
        recon = _balance_repo.get_latest_reconciliation(provider_name)
        if recon:
            data["reconciliation"] = {
                "method": recon["method"],
                "confidence": recon["confidence"],
                "drift_pct": recon.get("drift_pct"),
                "timestamp": recon.get("timestamp"),
            }

    return balances


@router.get("/balance/{provider}/reconciliation")
async def reconciliation_history(provider: str, limit: int = 20):
    """Reconciliation audit trail for a provider."""
    history = _balance_repo.get_reconciliation_history(provider, limit=limit)
    return {"provider": provider, "history": history}


@router.post("/balance/topup")
async def balance_topup(
    provider: str = Body(...),
    amount: float = Body(...),
    note: str = Body(""),
    project: Optional[str] = Body(None),
):
    """Add a top-up entry to a provider's ledger."""
    balance_cfg = _config.get("balance", {})
    provider_cfg = balance_cfg.get(provider)

    if provider_cfg is None:
        return {"error": f"Unknown provider: {provider}", "status": 400}
    if amount <= 0:
        return {"error": "Amount must be positive", "status": 400}

    # Find target ledger
    if provider_cfg.get("projects"):
        if not project:
            return {"error": f"Specify a project", "status": 400}
        proj_cfg = provider_cfg["projects"].get(project)
        if not proj_cfg:
            return {"error": f"Unknown project '{project}'", "status": 400}
        if "ledger" not in proj_cfg:
            proj_cfg["ledger"] = []
        target_ledger = proj_cfg["ledger"]
    elif "ledger" in provider_cfg:
        target_ledger = provider_cfg["ledger"]
    else:
        return {"error": f"No ledger configured for '{provider}'", "status": 400}

    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "amount": round(amount, 2),
    }
    if note:
        entry["note"] = note

    target_ledger.append(entry)

    try:
        with open(_config_path, "w") as f:
            yaml.dump(_config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        target_ledger.pop()
        return {"error": f"Failed to save: {e}", "status": 500}

    logger.info(f"Top-up: {provider}{f'/{project}' if project else ''} +${amount:.2f}")
    updated = await _balance_service.check_all_balances()
    return {"status": "ok", "entry": entry, "balances": updated}


@router.post("/balance/topup/delete")
async def balance_topup_delete(
    provider: str = Body(...),
    index: int = Body(...),
    project: Optional[str] = Body(None),
):
    """Remove a ledger entry by index."""
    balance_cfg = _config.get("balance", {})
    provider_cfg = balance_cfg.get(provider)

    if provider_cfg is None:
        return {"error": f"Unknown provider: {provider}", "status": 400}

    if provider_cfg.get("projects"):
        if not project:
            return {"error": "Specify which project", "status": 400}
        proj_cfg = provider_cfg["projects"].get(project)
        if not proj_cfg:
            return {"error": f"Unknown project '{project}'", "status": 400}
        ledger = proj_cfg.get("ledger")
    else:
        ledger = provider_cfg.get("ledger")

    if not ledger or index < 0 or index >= len(ledger):
        return {"error": f"Invalid index {index}", "status": 400}

    removed = ledger.pop(index)

    try:
        with open(_config_path, "w") as f:
            yaml.dump(_config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        ledger.insert(index, removed)
        return {"error": f"Failed to save: {e}", "status": 500}

    updated = await _balance_service.check_all_balances()
    return {"status": "ok", "removed": removed, "balances": updated}
