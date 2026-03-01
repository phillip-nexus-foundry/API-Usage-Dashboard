"""
Balance service.
Orchestrates balance checking across providers with priority cascade:
1. API endpoint (fast, authoritative)
2. Computed balance (ledger deposits minus DB costs)
3. Browser scraping (only if API fails AND confidence < 0.6)
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from dashboard.data.repositories.balance_repo import SQLAlchemyBalanceRepo
from dashboard.data.repositories.telemetry_repo import SQLAlchemyTelemetryRepo
from dashboard.application.reconciliation.engine import (
    ReconciliationEngine, DataPoint, ReconciledResult,
)
from dashboard.application.reconciliation.strategies import get_strategy, should_scrape
from dashboard.application.providers.base import ProviderAdapter
from dashboard.application.events import EventBus

logger = logging.getLogger(__name__)

# Provider name aliases for cost lookups
_PROVIDER_ALIASES = {
    "minimax": ["minimax", "mini_max", "minimaxai"],
}


class BalanceService:
    """Orchestrates balance tracking across multiple sources."""

    def __init__(
        self,
        balance_repo: SQLAlchemyBalanceRepo,
        telemetry_repo: SQLAlchemyTelemetryRepo,
        reconciliation_engine: ReconciliationEngine,
        providers: Dict[str, ProviderAdapter],
        config: dict,
        event_bus: Optional[EventBus] = None,
    ):
        self._balance_repo = balance_repo
        self._telemetry_repo = telemetry_repo
        self._reconciler = reconciliation_engine
        self._providers = providers
        self._config = config
        self._event_bus = event_bus

    async def check_balance(self, provider_name: str) -> Dict[str, Any]:
        """
        Get current balance using the best available method.
        Returns a dict compatible with the existing API response format.
        """
        balance_cfg = self._config.get("balance", {}).get(provider_name, {})
        if not balance_cfg or not isinstance(balance_cfg, dict):
            return {"status": "not_configured", "message": f"No config for {provider_name}"}

        # Handle multi-project providers (Moonshot)
        if balance_cfg.get("projects"):
            return await self._check_multi_project(provider_name, balance_cfg)

        # Gather data points from all sources
        data_points = []

        # 1. Try API balance (fast, authoritative)
        adapter = self._providers.get(provider_name)
        if adapter and adapter.supports_api_balance:
            api_result = await adapter.get_api_balance()
            if api_result.remaining is not None:
                data_points.append(DataPoint(
                    value=api_result.remaining,
                    source="api",
                    confidence=api_result.confidence,
                    timestamp=datetime.now(timezone.utc),
                ))
                # Save snapshot
                self._balance_repo.save_snapshot({
                    "provider": provider_name,
                    "snapshot_type": "balance",
                    "balance_amount": api_result.remaining,
                    "balance_currency": api_result.currency,
                    "balance_source": "api",
                    "confidence": api_result.confidence,
                })

        # 2. Compute from ledger
        ledger_balance = self._compute_ledger_balance(provider_name, balance_cfg)
        if ledger_balance is not None:
            data_points.append(DataPoint(
                value=ledger_balance["remaining"],
                source=ledger_balance.get("source", "computed"),
                confidence=ledger_balance.get("confidence", 0.7),
            ))

        # 3. Reconcile
        reconciled = self._reconciler.reconcile(provider_name, data_points)

        # Publish event
        if self._event_bus:
            await self._event_bus.publish(EventBus.BALANCE_CHECKED, {
                "provider": provider_name,
                "balance": reconciled.resolved_balance,
                "confidence": reconciled.confidence,
                "method": reconciled.method,
            })

        # Format response (compatible with existing API)
        return self._format_balance_response(provider_name, balance_cfg, reconciled, ledger_balance)

    async def check_all_balances(self) -> Dict[str, Any]:
        """Check balances for all configured providers."""
        result = {}
        balance_cfg = self._config.get("balance", {})
        for provider_name, cfg in balance_cfg.items():
            if isinstance(cfg, dict):
                result[provider_name] = await self.check_balance(provider_name)
        return result

    async def check_all_api_balances(self):
        """Scheduled job: check API balances for providers that support it."""
        for name, adapter in self._providers.items():
            if adapter.supports_api_balance:
                try:
                    result = await adapter.get_api_balance()
                    if result.remaining is not None:
                        self._balance_repo.save_snapshot({
                            "provider": name,
                            "snapshot_type": "balance",
                            "balance_amount": result.remaining,
                            "balance_currency": result.currency,
                            "balance_source": "api",
                            "confidence": result.confidence,
                        })
                        logger.info(f"API balance for {name}: {result.remaining}")
                except Exception as e:
                    logger.error(f"API balance check failed for {name}: {e}")

    def _compute_ledger_balance(
        self, provider_name: str, cfg: dict
    ) -> Optional[Dict[str, Any]]:
        """Calculate balance from ledger deposits minus costs."""
        ledger = cfg.get("ledger", [])
        if ledger is None:
            ledger = []
        if not isinstance(ledger, list):
            return None

        total_deposits = sum(e.get("amount", 0) for e in ledger)

        # Check for verified override
        if cfg.get("verified_usage_cost") is not None:
            try:
                usage_cost = float(cfg["verified_usage_cost"])
                return {
                    "remaining": round(total_deposits - usage_cost, 2),
                    "total_deposits": total_deposits,
                    "cumulative_cost": usage_cost,
                    "source": "ledger",
                    "cost_source": "verified_override",
                    "confidence": 0.85,
                }
            except (TypeError, ValueError):
                pass

        # Compute from DB
        db_cost = self._telemetry_repo.get_total_cost_by_provider(provider_name)
        return {
            "remaining": round(total_deposits - db_cost, 2),
            "total_deposits": total_deposits,
            "cumulative_cost": db_cost,
            "source": "computed",
            "cost_source": "computed",
            "confidence": 0.7,
        }

    async def _check_multi_project(
        self, provider_name: str, cfg: dict
    ) -> Dict[str, Any]:
        """Handle multi-project providers like Moonshot."""
        warn = cfg.get("warn_threshold", 20.0)
        crit = cfg.get("critical_threshold", 5.0)

        projects_result = {}
        total_deposits = 0.0
        total_cost = 0.0

        for proj_name, proj_cfg in cfg.get("projects", {}).items():
            if not isinstance(proj_cfg, dict):
                continue

            ledger = proj_cfg.get("ledger", [])
            proj_deposits = sum(e.get("amount", 0) for e in ledger)
            proj_models = proj_cfg.get("models", [])

            # Get cost for project's models
            proj_cost = 0.0
            for model in proj_models:
                proj_cost += self._telemetry_repo.get_total_cost_by_provider(model)
            # Also check by model name directly
            # (the telemetry_repo uses provider field, but we need model-based cost here)

            proj_remaining = proj_deposits - proj_cost

            proj_status = "ok"
            if proj_remaining <= crit:
                proj_status = "critical"
            elif proj_remaining <= warn:
                proj_status = "warn"

            personal = sum(e.get("amount", 0) for e in ledger if not e.get("is_voucher"))

            projects_result[proj_name] = {
                "status": proj_status,
                "total_deposits": round(proj_deposits, 2),
                "cumulative_cost": round(proj_cost, 6),
                "remaining": round(proj_remaining, 2),
                "personal_invested": round(personal, 2),
                "models": proj_models,
                "ledger": ledger,
            }

            total_deposits += proj_deposits
            total_cost += proj_cost

        remaining = total_deposits - total_cost

        # Try API balance if available
        adapter = self._providers.get(provider_name)
        balance_source = "ledger"
        if adapter and adapter.supports_api_balance:
            api_result = await adapter.get_api_balance()
            if api_result.remaining is not None:
                remaining = api_result.remaining
                balance_source = "api"

        worst = "ok"
        for proj in projects_result.values():
            if proj["status"] == "critical":
                worst = "critical"
                break
            if proj["status"] == "warn":
                worst = "warn"

        return {
            "status": worst,
            "total_deposits": round(total_deposits, 2),
            "cumulative_cost": round(total_cost, 6),
            "remaining": round(remaining, 2),
            "balance_source": balance_source,
            "warn_threshold": warn,
            "critical_threshold": crit,
            "projects": projects_result,
        }

    def _format_balance_response(
        self,
        provider_name: str,
        cfg: dict,
        reconciled: ReconciledResult,
        ledger_data: Optional[dict],
    ) -> Dict[str, Any]:
        """Format into the existing API response structure."""
        warn = cfg.get("warn_threshold", 10.0)
        crit = cfg.get("critical_threshold", 2.0)

        remaining = reconciled.resolved_balance
        status = "ok"
        if remaining <= crit:
            status = "critical"
        elif remaining <= warn:
            status = "warn"

        result = {
            "status": status,
            "remaining": round(remaining, 2),
            "warn_threshold": warn,
            "critical_threshold": crit,
            "balance_source": reconciled.method,
            "confidence": reconciled.confidence,
        }

        if ledger_data:
            result["total_deposits"] = ledger_data.get("total_deposits", 0)
            result["cumulative_cost"] = ledger_data.get("cumulative_cost", 0)
            result["cost_source"] = ledger_data.get("cost_source", "computed")

        if reconciled.drift_pct is not None:
            result["drift_pct"] = round(reconciled.drift_pct, 2)

        return result
