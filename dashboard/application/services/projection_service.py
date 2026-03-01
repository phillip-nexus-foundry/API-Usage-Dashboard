"""
Projection service.
Computes burn rates, runway estimates, and cost forecasts.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from dashboard.data.repositories.telemetry_repo import SQLAlchemyTelemetryRepo

logger = logging.getLogger(__name__)


class ProjectionService:
    """Computes cost projections and burn rate analysis."""

    def __init__(self, telemetry_repo: SQLAlchemyTelemetryRepo, config: dict):
        self._telemetry = telemetry_repo
        self._config = config

    def get_daily_costs(
        self,
        days: int = 30,
        provider: Optional[str] = None,
    ) -> list[dict]:
        """Get daily cost breakdown."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return self._telemetry.get_timeseries(
            interval="day", since=since, provider=provider
        )

    def get_burn_rate(self, days: int = 7) -> Dict[str, Any]:
        """Calculate daily and weekly burn rate from recent data."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        summary = self._telemetry.get_summary(since=since)
        totals = summary.get("totals", {})

        total_cost = totals.get("cost", 0.0)
        daily_rate = total_cost / days if days > 0 else 0.0
        weekly_rate = daily_rate * 7

        return {
            "period_days": days,
            "total_cost": round(total_cost, 4),
            "daily_rate": round(daily_rate, 4),
            "weekly_rate": round(weekly_rate, 4),
            "monthly_rate": round(daily_rate * 30, 4),
            "by_provider": summary.get("by_provider", {}),
        }

    def get_projection(
        self,
        balance: Optional[float] = None,
        days_lookback: int = 7,
    ) -> Dict[str, Any]:
        """
        Project future costs based on recent usage patterns.
        If balance is provided, estimates runway (days until balance runs out).
        """
        burn = self.get_burn_rate(days_lookback)
        daily_rate = burn["daily_rate"]

        projection = {
            "daily_rate": daily_rate,
            "projected_weekly": round(daily_rate * 7, 4),
            "projected_monthly": round(daily_rate * 30, 4),
            "projected_yearly": round(daily_rate * 365, 4),
            "based_on_days": days_lookback,
        }

        if balance is not None and daily_rate > 0:
            runway_days = balance / daily_rate
            projection["runway_days"] = round(runway_days, 1)
            projection["runway_date"] = (
                datetime.now(timezone.utc) + timedelta(days=runway_days)
            ).isoformat()

        return projection
