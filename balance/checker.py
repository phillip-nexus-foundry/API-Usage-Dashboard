"""
Balance checking for API providers.
Supports two modes per provider:
  - "ledger": Manual deposits minus cumulative cost from sessions (filtered by provider)
  - "api": Live balance fetched from provider's API (with ledger fallback)
"""
import os
import json
import httpx
import sqlite3
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class BalanceChecker:
    """Checks remaining balance for each configured provider."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    async def check_balances(self, reader) -> Dict[str, Any]:
        """Check balances for all providers configured under balance: in config."""
        result = {}
        balance_cfg = self.config.get("balance", {})

        for provider_name, provider_cfg in balance_cfg.items():
            if not isinstance(provider_cfg, dict):
                continue
            result[provider_name] = await self._check_provider(
                provider_name, provider_cfg, reader
            )

        return result

    async def _check_provider(
        self, name: str, cfg: Dict[str, Any], reader
    ) -> Dict[str, Any]:
        """Route to API check, ledger check, or multi-project check based on config."""
        # Multi-project provider (e.g. moonshot with multiple API keys/projects)
        if cfg.get("projects"):
            return self._check_provider_with_projects(name, cfg, reader)

        # If provider has an api_key_env, try API first
        if cfg.get("api_key_env"):
            api_result = await self._check_api(name, cfg)
            # If API succeeded (got a remaining value), return it
            if api_result.get("remaining") is not None:
                return api_result
            # API failed — fall back to ledger if available
            if cfg.get("ledger"):
                ledger_result = self._check_ledger(name, cfg, reader)
                ledger_result["api_note"] = api_result.get("message", "API unavailable")
                return ledger_result
            return api_result

        # Ledger-only provider
        if cfg.get("ledger"):
            return self._check_ledger(name, cfg, reader)

        return {
            "status": "not_configured",
            "message": f"No ledger or api_key_env configured for {name}",
        }

    def _check_provider_with_projects(
        self, name: str, cfg: Dict[str, Any], reader
    ) -> Dict[str, Any]:
        """Check balance for a provider with multiple projects.
        Each project has its own ledger and model list for cost attribution.
        Returns aggregate totals + per-project breakdowns."""
        try:
            warn_threshold = cfg.get("warn_threshold", 20.0)
            critical_threshold = cfg.get("critical_threshold", 5.0)

            projects_result = {}
            total_deposits = 0.0
            total_cost = 0.0

            for proj_name, proj_cfg in cfg["projects"].items():
                if not isinstance(proj_cfg, dict):
                    continue

                ledger = proj_cfg.get("ledger", [])
                proj_deposits = sum(entry.get("amount", 0) for entry in ledger)
                proj_models = proj_cfg.get("models", [])
                proj_cost = self._get_models_cost(reader, proj_models) if proj_models else 0.0
                proj_remaining = proj_deposits - proj_cost

                proj_status = "ok"
                if proj_remaining <= critical_threshold:
                    proj_status = "critical"
                elif proj_remaining <= warn_threshold:
                    proj_status = "warn"

                personal = sum(
                    e.get("amount", 0) for e in ledger if not e.get("is_voucher")
                )

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

            # Provider status = worst across all projects
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
                "warn_threshold": warn_threshold,
                "critical_threshold": critical_threshold,
                "projects": projects_result,
            }

        except Exception as e:
            logger.error(f"Failed to check {name} multi-project balance: {e}")
            return {"status": "error", "message": str(e)}

    def _check_ledger(
        self, provider_name: str, cfg: Dict[str, Any], reader
    ) -> Dict[str, Any]:
        """Calculate balance: sum(ledger) - cumulative cost for THIS provider only."""
        try:
            ledger = cfg.get("ledger", [])
            if not ledger:
                return {
                    "status": "not_configured",
                    "message": "Add ledger entries to config.yaml",
                }

            total_deposits = sum(entry.get("amount", 0) for entry in ledger)

            # Query cost for THIS provider only
            cumulative_cost = self._get_provider_cost(reader, provider_name)

            remaining = total_deposits - cumulative_cost

            warn_threshold = cfg.get("warn_threshold", 20.0)
            critical_threshold = cfg.get("critical_threshold", 5.0)

            status = "ok"
            if remaining <= critical_threshold:
                status = "critical"
            elif remaining <= warn_threshold:
                status = "warn"

            return {
                "status": status,
                "total_deposits": round(total_deposits, 2),
                "cumulative_cost": round(cumulative_cost, 6),
                "remaining": round(remaining, 2),
                "warn_threshold": warn_threshold,
                "critical_threshold": critical_threshold,
            }

        except Exception as e:
            logger.error(f"Failed to check {provider_name} balance: {e}")
            return {"status": "error", "message": str(e)}

    def _get_provider_cost(self, reader, provider_name: str) -> float:
        """Get total cost for a specific provider from the database."""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(cost_total), 0) FROM records WHERE provider = ?",
                (provider_name,),
            )
            return cursor.fetchone()[0]

    def _get_models_cost(self, reader, models: list) -> float:
        """Get total cost for specific models from the database."""
        if not models:
            return 0.0
        placeholders = ",".join("?" * len(models))
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COALESCE(SUM(cost_total), 0) FROM records WHERE model IN ({placeholders})",
                models,
            )
            return cursor.fetchone()[0]

    async def _check_api(self, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Check balance via provider API. Returns result with remaining or error."""
        try:
            api_key_env = cfg["api_key_env"]
            api_key = os.environ.get(api_key_env)

            if not api_key:
                return {
                    "status": "no_api_key",
                    "message": f"Set {api_key_env} env var",
                }

            api_endpoint = cfg.get(
                "api_endpoint", self._default_endpoint(name)
            )
            if not api_endpoint:
                return {
                    "status": "no_endpoint",
                    "message": f"No API endpoint for {name}",
                }

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    api_endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=5.0,
                )

                if response.status_code in (401, 403):
                    return {
                        "status": "auth_error",
                        "message": "Token invalid or expired",
                    }

                response.raise_for_status()
                data = response.json()

            # Parse balance from response (provider-specific path)
            balance = self._parse_balance_response(name, data)

            warn_threshold = cfg.get("warn_threshold", 10.0)
            critical_threshold = cfg.get("critical_threshold", 2.0)

            status = "ok"
            if balance <= critical_threshold:
                status = "critical"
            elif balance <= warn_threshold:
                status = "warn"

            return {
                "status": status,
                "remaining": round(balance, 2),
                "warn_threshold": warn_threshold,
                "critical_threshold": critical_threshold,
            }

        except httpx.TimeoutException:
            return {"status": "unreachable", "message": "API timeout"}
        except httpx.RequestError as e:
            return {"status": "unreachable", "message": f"Network error: {str(e)}"}
        except Exception as e:
            logger.error(f"Failed API check for {name}: {e}")
            return {"status": "error", "message": str(e)}

    def _default_endpoint(self, provider_name: str) -> Optional[str]:
        """Default API endpoints for known providers."""
        defaults = {
            "moonshot": "https://api.moonshot.cn/v1/users/me/balance",
        }
        return defaults.get(provider_name)

    def _parse_balance_response(self, provider_name: str, data: dict) -> float:
        """Extract balance value from provider-specific API response format."""
        if provider_name == "moonshot":
            return data.get("data", {}).get("balance", 0.0)
        # Generic fallback: look for common fields
        if "balance" in data:
            return float(data["balance"])
        if "data" in data and "balance" in data["data"]:
            return float(data["data"]["balance"])
        return 0.0
