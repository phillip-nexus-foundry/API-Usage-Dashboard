"""
SQLAlchemy implementation of PricingRepository.
Manages versioned pricing history for accurate historical cost computation.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_

from dashboard.data.models import PricingHistory
from dashboard.data.database import Database

logger = logging.getLogger(__name__)

# Map model names to providers
_MODEL_PROVIDERS = {
    "claude": "anthropic",
    "kimi": "moonshot",
    "moonshot": "moonshot",
    "minimax": "minimax",
    "m2.5": "minimax",
}


def infer_provider(model: str) -> str:
    """Infer provider from model name."""
    model_lower = model.lower()
    for key, provider in _MODEL_PROVIDERS.items():
        if key in model_lower:
            return provider
    return "unknown"


class SQLAlchemyPricingRepo:
    """Concrete pricing repository backed by SQLAlchemy."""

    def __init__(self, db: Database):
        self._db = db

    def get_price(
        self, provider: str, model: str, at_time: Optional[datetime] = None
    ) -> Optional[dict]:
        """Get pricing for a model at a specific time."""
        if at_time is None:
            at_time = datetime.now(timezone.utc)

        with self._db.session() as session:
            row = (
                session.query(PricingHistory)
                .filter(
                    PricingHistory.provider == provider,
                    PricingHistory.model == model,
                    PricingHistory.effective_from <= at_time,
                )
                .filter(
                    (PricingHistory.effective_until == None)  # noqa: E711
                    | (PricingHistory.effective_until > at_time)
                )
                .order_by(PricingHistory.effective_from.desc())
                .first()
            )
            return self._to_dict(row) if row else None

    def upsert_price(self, entry: dict) -> None:
        """Insert or update a pricing entry. Closes existing entry if model/provider match."""
        with self._db.session() as session:
            # Close any existing open pricing for this model
            existing = (
                session.query(PricingHistory)
                .filter(
                    PricingHistory.provider == entry["provider"],
                    PricingHistory.model == entry["model"],
                    PricingHistory.effective_until == None,  # noqa: E711
                )
                .first()
            )

            if existing:
                # Check if prices actually changed
                if (
                    existing.input_price == entry.get("input_price")
                    and existing.output_price == entry.get("output_price")
                    and existing.cache_read_price == entry.get("cache_read_price", 0)
                    and existing.cache_write_price == entry.get("cache_write_price", 0)
                ):
                    return  # No change, skip

                # Close the old entry
                existing.effective_until = entry.get(
                    "effective_from", datetime.now(timezone.utc)
                )

            obj = PricingHistory(**entry)
            session.add(obj)

    def get_all_current_prices(self) -> list[dict]:
        """All currently active pricing entries."""
        with self._db.session() as session:
            rows = (
                session.query(PricingHistory)
                .filter(PricingHistory.effective_until == None)  # noqa: E711
                .order_by(PricingHistory.provider, PricingHistory.model)
                .all()
            )
            return [self._to_dict(r) for r in rows]

    def seed_from_config(self, model_costs: dict) -> int:
        """
        Seed pricing_history from config.yaml model_costs section.
        Only inserts if no current pricing exists for the model.
        Returns count of entries created.
        """
        created = 0
        now = datetime.now(timezone.utc)

        for model_name, costs in model_costs.items():
            provider = infer_provider(model_name)
            existing = self.get_price(provider, model_name)
            if existing:
                continue

            self.upsert_price({
                "provider": provider,
                "model": model_name,
                "input_price": costs.get("input", 0),
                "output_price": costs.get("output", 0),
                "cache_read_price": costs.get("cache_read", 0),
                "cache_write_price": costs.get("cache_write", 0),
                "effective_from": now,
                "source": "config",
            })
            created += 1

        return created

    @staticmethod
    def _to_dict(ph: PricingHistory) -> dict:
        return {
            "id": ph.id,
            "provider": ph.provider,
            "model": ph.model,
            "input_price": ph.input_price,
            "output_price": ph.output_price,
            "cache_read_price": ph.cache_read_price,
            "cache_write_price": ph.cache_write_price,
            "effective_from": ph.effective_from.isoformat() if ph.effective_from else None,
            "effective_until": ph.effective_until.isoformat() if ph.effective_until else None,
            "source": ph.source,
        }
