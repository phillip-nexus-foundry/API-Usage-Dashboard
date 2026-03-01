"""
Moonshot (Kimi) provider adapter.
Uses the Moonshot balance API for live balance checking.
"""
import os
import logging
from datetime import datetime
from typing import Optional

import httpx

from dashboard.application.providers.base import (
    BalanceResponse, UsageRecord,
)

logger = logging.getLogger(__name__)


class MoonshotProvider:
    """Moonshot API provider adapter."""

    BALANCE_ENDPOINT = "https://api.moonshot.cn/v1/users/me/balance"

    def __init__(self, config: dict):
        self._config = config
        balance_cfg = config.get("balance", {}).get("moonshot", {})
        api_key_env = balance_cfg.get("api_key_env", "MOONSHOT_API_KEY")
        self._api_key = os.environ.get(api_key_env)

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
                resp = await client.get(
                    self.BALANCE_ENDPOINT,
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
                balance = data.get("data", {}).get("balance", 0.0)

                return BalanceResponse(
                    remaining=round(float(balance), 2),
                    source="api",
                    confidence=0.95,
                    raw_data=data,
                )

        except httpx.TimeoutException:
            return BalanceResponse(
                remaining=None, error="API timeout", confidence=0.0
            )
        except Exception as e:
            logger.error(f"Moonshot balance check failed: {e}")
            return BalanceResponse(
                remaining=None, error=str(e), confidence=0.0
            )

    async def get_api_usage(
        self, since: Optional[datetime] = None
    ) -> Optional[list[UsageRecord]]:
        """Moonshot doesn't provide a usage history API."""
        return None
