"""
SQLAlchemy implementation of BalanceRepository.
Handles resource snapshots and reconciliation log entries.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func

from dashboard.data.models import ResourceSnapshot, ReconciliationLog
from dashboard.data.database import Database

logger = logging.getLogger(__name__)


class SQLAlchemyBalanceRepo:
    """Concrete balance repository backed by SQLAlchemy."""

    def __init__(self, db: Database):
        self._db = db

    def save_snapshot(self, snapshot: dict) -> None:
        """Insert a resource snapshot."""
        with self._db.session() as session:
            obj = ResourceSnapshot(**snapshot)
            session.add(obj)

    def get_latest_snapshot(
        self, provider: str, snapshot_type: str = "balance"
    ) -> Optional[dict]:
        """Get most recent snapshot for a provider."""
        with self._db.session() as session:
            row = (
                session.query(ResourceSnapshot)
                .filter(
                    ResourceSnapshot.provider == provider,
                    ResourceSnapshot.snapshot_type == snapshot_type,
                )
                .order_by(ResourceSnapshot.timestamp.desc())
                .first()
            )
            return self._snapshot_to_dict(row) if row else None

    def get_snapshot_history(
        self, provider: str, days: int = 30
    ) -> list[dict]:
        """Historical snapshots for a provider."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._db.session() as session:
            rows = (
                session.query(ResourceSnapshot)
                .filter(
                    ResourceSnapshot.provider == provider,
                    ResourceSnapshot.timestamp >= cutoff,
                )
                .order_by(ResourceSnapshot.timestamp.desc())
                .all()
            )
            return [self._snapshot_to_dict(r) for r in rows]

    def save_reconciliation(self, entry: dict) -> None:
        """Insert a reconciliation log entry."""
        with self._db.session() as session:
            obj = ReconciliationLog(**entry)
            session.add(obj)

    def get_reconciliation_history(
        self, provider: str, limit: int = 50
    ) -> list[dict]:
        """Recent reconciliation entries for a provider."""
        with self._db.session() as session:
            rows = (
                session.query(ReconciliationLog)
                .filter(ReconciliationLog.provider == provider)
                .order_by(ReconciliationLog.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [self._recon_to_dict(r) for r in rows]

    def get_latest_reconciliation(self, provider: str) -> Optional[dict]:
        """Most recent reconciliation for a provider."""
        with self._db.session() as session:
            row = (
                session.query(ReconciliationLog)
                .filter(ReconciliationLog.provider == provider)
                .order_by(ReconciliationLog.timestamp.desc())
                .first()
            )
            return self._recon_to_dict(row) if row else None

    @staticmethod
    def _snapshot_to_dict(snap: ResourceSnapshot) -> dict:
        return {
            "id": snap.id,
            "provider": snap.provider,
            "snapshot_type": snap.snapshot_type,
            "timestamp": snap.timestamp.isoformat() if snap.timestamp else None,
            "balance_amount": snap.balance_amount,
            "balance_currency": snap.balance_currency,
            "balance_source": snap.balance_source,
            "tier": snap.tier,
            "confidence": snap.confidence,
            "computed_cost": snap.computed_cost,
            "drift_amount": snap.drift_amount,
            "drift_pct": snap.drift_pct,
            "error": snap.error,
        }

    @staticmethod
    def _recon_to_dict(recon: ReconciliationLog) -> dict:
        return {
            "id": recon.id,
            "provider": recon.provider,
            "timestamp": recon.timestamp.isoformat() if recon.timestamp else None,
            "computed_balance": recon.computed_balance,
            "api_balance": recon.api_balance,
            "scraped_balance": recon.scraped_balance,
            "resolved_balance": recon.resolved_balance,
            "confidence": recon.confidence,
            "method": recon.method,
            "drift_amount": recon.drift_amount,
            "drift_pct": recon.drift_pct,
            "notes": recon.notes,
        }
