"""
Moonshot (Kimi) provider adapter.
Uses the Moonshot balance API for live balance checking.
"""
import os
import logging
import re
from datetime import datetime
from typing import Optional

import httpx

from dashboard.application.providers.base import (
    BalanceResponse, UsageRecord,
)

logger = logging.getLogger(__name__)


class MoonshotProvider:
    """Moonshot API provider adapter."""

    BALANCE_ENDPOINTS = (
        "https://api.moonshot.ai/v1/users/me/balance",
        "https://api.moonshot.cn/v1/users/me/balance",
    )

    def __init__(self, config: dict):
        self._config = config
        balance_cfg = config.get("balance", {}).get("moonshot", {})
        api_key_env = balance_cfg.get("api_key_env", "MOONSHOT_API_KEY")
        self._api_key = (
            os.environ.get(api_key_env)
            or balance_cfg.get("api_key")
            or config.get("moonshot_api_key")
        )

    @property
    def name(self) -> str:
        return "moonshot"

    @property
    def supports_api_balance(self) -> bool:
        return bool(self._api_key)

    @property
    def supports_api_usage(self) -> bool:
        return False

    async def get_api_balance(self) -> BalanceResponse:
        """Fetch balance from Moonshot balance API."""
        if not self._api_key:
            return BalanceResponse(
                remaining=None,
                error="MOONSHOT_API_KEY not set",
                confidence=0.0,
            )

        try:
            async with httpx.AsyncClient() as client:
                last_exc = None
                for endpoint in self.BALANCE_ENDPOINTS:
                    try:
                        resp = await client.get(
                            endpoint,
                            headers={"Authorization": f"Bearer {self._api_key}"},
                            timeout=5.0,
                        )

                        if resp.status_code in (401, 403):
                            return BalanceResponse(
                                remaining=None,
                                error="Token invalid or expired",
                                confidence=0.0,
                            )

                        resp.raise_for_status()
                        data = resp.json()
                        balance = self._extract_balance(data)
                        if balance is None:
                            raise ValueError("Balance field missing in Moonshot API response")

                        return BalanceResponse(
                            remaining=round(float(balance), 2),
                            source="api",
                            confidence=0.95,
                            raw_data=data,
                        )
                    except Exception as exc:
                        last_exc = exc
                raise last_exc or RuntimeError("Moonshot balance endpoint unavailable")

        except httpx.TimeoutException:
            return BalanceResponse(
                remaining=None, error="API timeout", confidence=0.0
            )
        except Exception as e:
            logger.error(f"Moonshot balance check failed: {e}")
            return BalanceResponse(
                remaining=None, error=str(e), confidence=0.0
            )

    @staticmethod
    def _extract_balance(data: dict) -> Optional[float]:
        """Parse Moonshot balance payload across known response variants."""
        payload = data.get("data", {}) if isinstance(data, dict) else {}

        for key in ("available_balance", "available", "cash_balance", "remain", "remaining", "balance"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if value is None and isinstance(data, dict):
                value = data.get(key)
            parsed = MoonshotProvider._coerce_num(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _coerce_num(value) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                return float(value)
            except Exception:
                return None
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
            if not match:
                return None
            try:
                return float(match.group(0))
            except Exception:
                return None
        return None

    async def get_api_usage(
        self, since: Optional[datetime] = None
    ) -> Optional[list[UsageRecord]]:
        """Moonshot doesn't provide a usage history API."""
        return None
