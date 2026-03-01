"""
Provider adapter protocol.
Each API provider implements this to expose balance/usage capabilities.
"""
from typing import Protocol, Optional
from datetime import datetime
from dataclasses import dataclass


@dataclass
class BalanceResponse:
    """Result of a balance check."""
    remaining: Optional[float]
    currency: str = "USD"
    source: str = "api"  # api, ledger, computed
    confidence: float = 0.95
    error: Optional[str] = None
    raw_data: Optional[dict] = None


@dataclass
class UsageRecord:
    """A single usage record from the provider API."""
    timestamp: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cost: Optional[float] = None


class ProviderAdapter(Protocol):
    """Interface that each provider implements."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g., 'anthropic', 'moonshot')."""
        ...

    @property
    def supports_api_balance(self) -> bool:
        """Can we check balance via API?"""
        ...

    @property
    def supports_api_usage(self) -> bool:
        """Can we fetch per-request usage records via API?"""
        ...

    async def get_api_balance(self) -> BalanceResponse:
        """Fetch balance via official API."""
        ...

    async def get_api_usage(
        self, since: Optional[datetime] = None
    ) -> Optional[list[UsageRecord]]:
        """Fetch usage records via official API."""
        ...
