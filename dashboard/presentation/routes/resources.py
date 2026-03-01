"""
Resource availability routes.
RPM/TPM rate limit tracking and resource polling.
"""
from fastapi import APIRouter

router = APIRouter(tags=["resources"])

# Injected by app factory
_config = None
_balance_poller = None


def init(config, balance_poller=None):
    global _config, _balance_poller
    _config = config
    _balance_poller = balance_poller


@router.get("/resources")
async def resources():
    """Resource availability - rate limits and usage windows."""
    # This endpoint is complex and provider-specific.
    # For now, delegate to the legacy balance_poller if available.
    # Will be fully refactored in Phase 4.
    if _balance_poller:
        snapshots = _balance_poller.get_latest_snapshots()
    else:
        snapshots = {}

    return {"providers": snapshots}


@router.post("/resources/poll")
async def resources_poll():
    """Trigger immediate resource polling."""
    if _balance_poller:
        results = await _balance_poller.poll_all(
            ["anthropic", "elevenlabs", "codex_cli", "moonshot", "minimax"]
        )
        return {"status": "ok", "polled": len(results)}
    return {"status": "ok", "polled": 0, "note": "Poller not configured"}
