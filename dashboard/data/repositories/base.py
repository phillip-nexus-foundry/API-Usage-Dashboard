"""
Repository protocol interfaces.
These define the contract between the data tier and the application tier.
Concrete implementations (SQLAlchemy-backed) implement these protocols.
"""
from typing import Protocol, Optional, Sequence
from datetime import datetime


class TelemetryRepository(Protocol):
    """Abstract interface for telemetry record access."""

    def insert_records(self, records: Sequence[dict]) -> int:
        """Bulk insert telemetry records. Returns count inserted (skips duplicates)."""
        ...

    def get_records(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 1000,
        offset: int = 0,
        sort_desc: bool = True,
    ) -> list[dict]:
        """Query records with optional filters."""
        ...

    def get_summary(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> dict:
        """Aggregated summary: total calls, cost, tokens, by provider/model."""
        ...

    def get_timeseries(
        self,
        interval: str = "hour",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        provider: Optional[str] = None,
    ) -> list[dict]:
        """Time-bucketed aggregations for charting."""
        ...

    def get_sessions(self) -> list[dict]:
        """List distinct sessions with call counts and cost totals."""
        ...

    def get_session_detail(self, session_id: str) -> list[dict]:
        """All records for a given session."""
        ...

    def get_model_stats(self) -> list[dict]:
        """Per-model usage statistics."""
        ...

    def get_tool_stats(self) -> list[dict]:
        """Tool usage breakdown."""
        ...

    def get_total_cost_by_provider(self, provider: str) -> float:
        """Sum of cost_total for a provider."""
        ...


class BalanceRepository(Protocol):
    """Abstract interface for balance snapshots and reconciliation."""

    def save_snapshot(self, snapshot: dict) -> None:
        """Insert a resource snapshot."""
        ...

    def get_latest_snapshot(self, provider: str, snapshot_type: str = "balance") -> Optional[dict]:
        """Get most recent snapshot for a provider."""
        ...

    def get_snapshot_history(
        self, provider: str, days: int = 30
    ) -> list[dict]:
        """Historical snapshots for a provider."""
        ...

    def save_reconciliation(self, entry: dict) -> None:
        """Insert a reconciliation log entry."""
        ...

    def get_reconciliation_history(
        self, provider: str, limit: int = 50
    ) -> list[dict]:
        """Recent reconciliation entries for a provider."""
        ...

    def get_latest_reconciliation(self, provider: str) -> Optional[dict]:
        """Most recent reconciliation for a provider."""
        ...


class FileIndexRepository(Protocol):
    """Abstract interface for file tracking."""

    def get_file_entry(self, path: str) -> Optional[dict]:
        """Get tracking info for a file."""
        ...

    def upsert_file_entry(self, entry: dict) -> None:
        """Insert or update a file tracking entry."""
        ...

    def get_all_entries(self) -> list[dict]:
        """All tracked files."""
        ...


class PricingRepository(Protocol):
    """Abstract interface for versioned pricing data."""

    def get_price(
        self, provider: str, model: str, at_time: Optional[datetime] = None
    ) -> Optional[dict]:
        """Get pricing for a model at a specific time. Returns current if at_time is None."""
        ...

    def upsert_price(self, entry: dict) -> None:
        """Insert or update a pricing entry."""
        ...

    def get_all_current_prices(self) -> list[dict]:
        """All currently active pricing entries."""
        ...


class ProviderStateRepository(Protocol):
    """Abstract interface for provider health tracking."""

    def get_state(self, provider: str) -> Optional[dict]:
        """Get current state for a provider."""
        ...

    def upsert_state(self, provider: str, updates: dict) -> None:
        """Update provider state."""
        ...

    def get_all_states(self) -> list[dict]:
        """All provider states."""
        ...
