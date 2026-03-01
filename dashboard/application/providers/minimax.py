"""
MiniMax provider adapter.
Ledger-based balance tracking (no known balance API endpoint).
"""
import logging
from datetime import datetime
from typing import Optional

from dashboard.application.providers.base import (
    BalanceResponse, UsageRecord,
)

logger = logging.getLogger(__name__)


class MiniMaxProvider:
    """MiniMax provider adapter."""

    def __init__(self, config: dict):
        self._config = config

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def supports_api_balance(self) -> bool:
        return False

    @property
    def supports_api_usage(self) -> bool:
        return False

    async def get_api_balance(self) -> BalanceResponse:
        """MiniMax has no known balance API. Returns None, relying on ledger."""
        return BalanceResponse(
            remaining=None,
            source="none",
            confidence=0.0,
            error="No API balance endpoint available for MiniMax",
        )

    async def get_api_usage(
        self, since: Optional[datetime] = None
    ) -> Optional[list[UsageRecord]]:
        return None
