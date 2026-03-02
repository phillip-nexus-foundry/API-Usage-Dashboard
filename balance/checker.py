"""
Balance checking for API providers.
Supports two modes per provider:
  - "ledger": Manual deposits minus cumulative cost from sessions (filtered by provider)
  - "api": Live balance fetched from provider's API (with ledger fallback)
"""
import os
import httpx
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_PROVIDER_ALIASES = {
    "minimax": ["minimax", "mini_max", "minimaxai"],
}


class BalanceChecker:
    """Checks remaining balance for each configured provider."""

    def __init__(self, config: Dict[str, Any], config_path: str = None):
        self.config = config
        self.config_path = config_path

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

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
            result = self._check_provider_with_projects(name, cfg, reader)
            # If provider also has an API key, use live API balance instead of ledger math
            if cfg.get("api_key_env"):
                api_result = await self._check_api(name, cfg)
                if api_result.get("remaining") is not None:
                    api_balance = api_result["remaining"]
                    result["remaining"] = api_balance
                    result["balance_source"] = "api"
                    # Recalculate status based on live balance
                    warn = cfg.get("warn_threshold", 10.0)
                    crit = cfg.get("critical_threshold", 2.0)
                    if api_balance <= crit:
                        result["status"] = "critical"
                    elif api_balance <= warn:
                        result["status"] = "warn"
                    else:
                        result["status"] = "ok"
                else:
                    result["balance_source"] = "ledger"
                    result["api_note"] = api_result.get("message", "API unavailable")
            return result

        # If provider has an api_key_env, try API first
        if cfg.get("api_key_env"):
            api_result = await self._check_api(name, cfg)
            # If API succeeded (got a remaining value), return it
            if api_result.get("remaining") is not None:
                return api_result
            # API failed - fall back to ledger if configured (including empty [])
            if "ledger" in cfg:
                ledger_result = self._check_ledger(name, cfg, reader)
                ledger_result["api_note"] = api_result.get("message", "API unavailable")
                return ledger_result
            return api_result

        # Ledger-only provider
        if "ledger" in cfg:
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
                # Allow verified_usage_cost override at project level (same as provider level)
                if proj_cfg.get("verified_usage_cost") is not None:
                    try:
                        proj_cost = float(proj_cfg["verified_usage_cost"])
                    except (TypeError, ValueError):
                        pass
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
            if ledger is None:
                ledger = []
            if not isinstance(ledger, list):
                return {
                    "status": "not_configured",
                    "message": "Ledger must be a list in config.yaml",
                }

            warn_threshold = cfg.get("warn_threshold", 20.0)
            critical_threshold = cfg.get("critical_threshold", 5.0)

            # A configured but empty ledger should still be treated as active so
            # the deposit interface remains available.
            if len(ledger) == 0:
                return {
                    "status": "warn",
                    "total_deposits": 0.0,
                    "cumulative_cost": 0.0,
                    "raw_cumulative_cost": 0.0,
                    "cost_source": "computed",
                    "remaining": 0.0,
                    "warn_threshold": warn_threshold,
                    "critical_threshold": critical_threshold,
                }

            total_deposits = sum(entry.get("amount", 0) for entry in ledger)

            # Query cost for THIS provider only (with optional reconciliation override)
            raw_cumulative_cost = self._get_provider_cost(reader, provider_name)
            cumulative_cost = raw_cumulative_cost
            cost_source = "computed"

            if cfg.get("verified_usage_cost") is not None:
                try:
                    cumulative_cost = float(cfg.get("verified_usage_cost"))
                    cost_source = "verified_override"
                except (TypeError, ValueError):
                    cumulative_cost = raw_cumulative_cost
                    cost_source = "computed"
            elif cfg.get("usage_cost_adjustment") is not None:
                try:
                    adjustment = float(cfg.get("usage_cost_adjustment"))
                    cumulative_cost = raw_cumulative_cost + adjustment
                    cost_source = "computed_plus_adjustment"
                except (TypeError, ValueError):
                    cumulative_cost = raw_cumulative_cost
                    cost_source = "computed"

            remaining = total_deposits - cumulative_cost

            status = "ok"
            if remaining <= critical_threshold:
                status = "critical"
            elif remaining <= warn_threshold:
                status = "warn"

            return {
                "status": status,
                "total_deposits": round(total_deposits, 2),
                "cumulative_cost": round(cumulative_cost, 6),
                "raw_cumulative_cost": round(raw_cumulative_cost, 6),
                "cost_source": cost_source,
                "remaining": round(remaining, 2),
                "warn_threshold": warn_threshold,
                "critical_threshold": critical_threshold,
            }

        except Exception as e:
            logger.error(f"Failed to check {provider_name} balance: {e}")
            return {"status": "error", "message": str(e)}

    def _get_provider_cost(self, reader, provider_name: str) -> float:
        """Get total cost for a specific provider from the database."""
        aliases = _PROVIDER_ALIASES.get(provider_name, [provider_name])
        placeholders = ",".join("?" * len(aliases))
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COALESCE(SUM(cost_total), 0) FROM records WHERE provider IN ({placeholders})",
                aliases,
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
