"""
Anthropic provider adapter.
Uses API rate-limit headers and usage endpoints for balance/usage data.
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


class AnthropicProvider:
    """Anthropic API provider adapter."""

    def __init__(self, config: dict):
        self._config = config
        self._api_key = os.environ.get("ANTHROPIC_API_KEY")

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def supports_api_balance(self) -> bool:
        # Anthropic doesn't have a direct balance endpoint,
        # but we can infer from rate-limit headers and usage
        return bool(self._api_key)

    @property
    def supports_api_usage(self) -> bool:
        return bool(self._api_key)

    async def get_api_balance(self) -> BalanceResponse:
        """
        Anthropic doesn't have a direct balance API.
        We use ledger deposits minus computed costs, with optional
        rate-limit header probing to verify account tier.
        """
        if not self._api_key:
            return BalanceResponse(
                remaining=None,
                error="ANTHROPIC_API_KEY not set",
                confidence=0.0,
            )

        # Try to probe rate limits to at least verify the key works
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    timeout=15.0,
                )

                # Extract rate limit info from headers
                raw_data = {
                    "rpm_limit": resp.headers.get("anthropic-ratelimit-requests-limit"),
                    "tpm_limit": resp.headers.get("anthropic-ratelimit-tokens-limit"),
                    "status_code": resp.status_code,
                }

                # We can't get actual balance from API, return None
                # Balance will come from ledger/reconciliation
                return BalanceResponse(
                    remaining=None,
                    source="api_probe",
                    confidence=0.3,  # Low confidence since we don't get actual balance
                    raw_data=raw_data,
                )

        except Exception as e:
            logger.warning(f"Anthropic API probe failed: {e}")
            return BalanceResponse(
                remaining=None,
                error=str(e),
                confidence=0.0,
            )

    async def get_api_usage(
        self, since: Optional[datetime] = None
    ) -> Optional[list[UsageRecord]]:
        """Anthropic usage data comes from JSONL session files, not API."""
        return None
