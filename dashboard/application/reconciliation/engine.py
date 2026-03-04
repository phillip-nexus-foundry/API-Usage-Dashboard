"""
Reconciliation engine.
Cross-references multiple data sources to produce high-confidence balances.
This is the core innovation that reduces scraping dependency.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from dashboard.data.repositories.balance_repo import SQLAlchemyBalanceRepo
from dashboard.data.repositories.telemetry_repo import SQLAlchemyTelemetryRepo

logger = logging.getLogger(__name__)


@dataclass
class DataPoint:
    """A balance observation from a single source."""
    value: float
    source: str  # api, computed, scraped, ledger
    confidence: float  # 0.0-1.0
    timestamp: Optional[datetime] = None


@dataclass
class ReconciledResult:
    """Output of reconciliation: a single balance with confidence."""
    provider: str
    resolved_balance: float
    confidence: float
    method: str  # api_authoritative, weighted_average, computed_only, ledger_only
    computed_balance: Optional[float] = None
    api_balance: Optional[float] = None
    scraped_balance: Optional[float] = None
    drift_amount: Optional[float] = None
    drift_pct: Optional[float] = None
    notes: str = ""


@dataclass
class DriftAlert:
    """Alert when systematic drift is detected."""
    provider: str
    direction: str  # "under" or "over"
    avg_drift_pct: float
    sample_count: int
    suggestion: str


# Confidence scores for each data source
SOURCE_CONFIDENCE = {
    "api": 0.95,       # API-reported balance (authoritative)
    "computed": 0.70,  # Computed from token counts + pricing table
    "scraped": 0.60,   # Browser-scraped from console pages
    "ledger": 0.80,    # Manual ledger deposits minus computed costs
}

# How recent an API check must be to be considered "fresh"
API_FRESHNESS_HOURS = 1


class ReconciliationEngine:
    """
    Cross-references multiple data sources to produce a high-confidence balance.

    Data sources ranked by trust:
    1. API-reported balance (confidence: 0.95) - authoritative but may lag
    2. Ledger-based (confidence: 0.80) - manual deposits minus DB costs
    3. Computed from token counts (confidence: 0.70) - depends on correct pricing
    4. Scraped from console (confidence: 0.60) - may be stale, UI can change
    """

    def __init__(
        self,
        balance_repo: SQLAlchemyBalanceRepo,
        telemetry_repo: SQLAlchemyTelemetryRepo,
        config: dict,
    ):
        self._balance_repo = balance_repo
        self._telemetry_repo = telemetry_repo
        self._config = config

    def reconcile(
        self,
        provider: str,
        data_points: List[DataPoint],
    ) -> ReconciledResult:
        """
        Given multiple data points from different sources, produce a single
        reconciled balance with confidence score.
        """
        if not data_points:
            return ReconciledResult(
                provider=provider,
                resolved_balance=0.0,
                confidence=0.0,
                method="no_data",
                notes="No data points available for reconciliation",
            )

        # Sort by confidence (highest first)
        data_points.sort(key=lambda dp: -dp.confidence)

        # Extract by source type
        api_point = next((dp for dp in data_points if dp.source == "api"), None)
        computed_point = next((dp for dp in data_points if dp.source in ("computed", "ledger")), None)
        scraped_point = next((dp for dp in data_points if dp.source == "scraped"), None)

        # Strategy selection
        result = self._select_strategy(provider, api_point, computed_point, scraped_point)

        # Log the reconciliation
        self._log_reconciliation(result)

        return result

    def _select_strategy(
        self,
        provider: str,
        api_point: Optional[DataPoint],
        computed_point: Optional[DataPoint],
        scraped_point: Optional[DataPoint],
    ) -> ReconciledResult:
        """Choose reconciliation strategy based on available data."""

        api_val = api_point.value if api_point else None
        computed_val = computed_point.value if computed_point else None
        scraped_val = scraped_point.value if scraped_point else None

        # Strategy 1: Fresh API balance is authoritative
        if api_point and api_point.confidence >= 0.9:
            is_fresh = True
            if api_point.timestamp:
                age = datetime.now(timezone.utc) - api_point.timestamp
                is_fresh = age < timedelta(hours=API_FRESHNESS_HOURS)

            if is_fresh:
                drift = None
                drift_pct = None
                notes = "API balance is fresh and authoritative"

                if computed_val is not None and api_val and api_val != 0:
                    drift = computed_val - api_val
                    drift_pct = (drift / api_val) * 100
                    if abs(drift_pct) > 5:
                        notes += f" (drift from computed: {drift_pct:+.1f}%)"

                return ReconciledResult(
                    provider=provider,
                    resolved_balance=api_val,
                    confidence=0.95,
                    method="api_authoritative",
                    computed_balance=computed_val,
                    api_balance=api_val,
                    scraped_balance=scraped_val,
                    drift_amount=drift,
                    drift_pct=drift_pct,
                    notes=notes,
                )

        # Strategy 2: Multiple sources available - weighted average
        if api_point and computed_point:
            # Weighted average of API and computed
            total_conf = api_point.confidence + computed_point.confidence
            resolved = (
                api_point.value * api_point.confidence
                + computed_point.value * computed_point.confidence
            ) / total_conf
            confidence = min(0.90, max(api_point.confidence, computed_point.confidence))

            drift = computed_val - api_val if api_val and api_val != 0 else None
            drift_pct = (drift / api_val * 100) if drift is not None and api_val else None

            return ReconciledResult(
                provider=provider,
                resolved_balance=round(resolved, 2),
                confidence=confidence,
                method="weighted_average",
                computed_balance=computed_val,
                api_balance=api_val,
                scraped_balance=scraped_val,
                drift_amount=drift,
                drift_pct=drift_pct,
                notes=f"Weighted average of API ({api_point.confidence}) and computed ({computed_point.confidence})",
            )

        # Strategy 3: Scraped + computed — prefer scraped (direct observation)
        if scraped_point and computed_point:
            drift = computed_val - scraped_val if scraped_val and scraped_val != 0 else None
            drift_pct = (drift / scraped_val * 100) if drift is not None and scraped_val else None
            return ReconciledResult(
                provider=provider,
                resolved_balance=round(scraped_val, 2),
                confidence=scraped_point.confidence,
                method="scraped_authoritative",
                computed_balance=computed_val,
                scraped_balance=scraped_val,
                drift_amount=drift,
                drift_pct=drift_pct,
                notes=f"Scraped balance from browser CDP (drift from computed: {drift_pct:+.1f}%)" if drift_pct else "Scraped balance from browser CDP",
            )

        # Strategy 4: Computed/ledger only
        if computed_point:
            return ReconciledResult(
                provider=provider,
                resolved_balance=round(computed_val, 2),
                confidence=computed_point.confidence,
                method="computed_only",
                computed_balance=computed_val,
                scraped_balance=scraped_val,
                notes="Only computed balance available (no API endpoint)",
            )

        # Strategy 5: Scraped only (last resort)
        if scraped_point:
            return ReconciledResult(
                provider=provider,
                resolved_balance=round(scraped_val, 2),
                confidence=scraped_point.confidence,
                method="scraped_only",
                scraped_balance=scraped_val,
                notes="Only scraped balance available (low confidence)",
            )

        return ReconciledResult(
            provider=provider,
            resolved_balance=0.0,
            confidence=0.0,
            method="no_data",
            notes="No data points available",
        )

    def _log_reconciliation(self, result: ReconciledResult):
        """Persist reconciliation result to audit log."""
        try:
            self._balance_repo.save_reconciliation({
                "provider": result.provider,
                "computed_balance": result.computed_balance,
                "api_balance": result.api_balance,
                "scraped_balance": result.scraped_balance,
                "resolved_balance": result.resolved_balance,
                "confidence": result.confidence,
                "method": result.method,
                "drift_amount": result.drift_amount,
                "drift_pct": result.drift_pct,
                "notes": result.notes,
            })
        except Exception as e:
            logger.error(f"Failed to log reconciliation for {result.provider}: {e}")

    def detect_drift(self, provider: str, lookback_days: int = 7) -> Optional[DriftAlert]:
        """
        Analyze recent reconciliations for systematic drift.
        If computed costs consistently differ from API, suggest pricing correction.
        """
        history = self._balance_repo.get_reconciliation_history(provider, limit=50)
        if len(history) < 3:
            return None  # Not enough data

        # Filter to entries with drift data
        drift_entries = [
            h for h in history
            if h.get("drift_pct") is not None and h.get("method") in ("api_authoritative", "weighted_average")
        ]

        if len(drift_entries) < 3:
            return None

        # Calculate average drift
        drift_values = [h["drift_pct"] for h in drift_entries]
        avg_drift = sum(drift_values) / len(drift_values)

        if abs(avg_drift) < 5:
            return None  # Within acceptable range

        direction = "under" if avg_drift < 0 else "over"
        suggestion = (
            f"Computed costs are consistently {abs(avg_drift):.1f}% {direction} "
            f"API-reported for {provider}. "
            f"Check if model pricing in config.yaml needs updating."
        )

        return DriftAlert(
            provider=provider,
            direction=direction,
            avg_drift_pct=avg_drift,
            sample_count=len(drift_entries),
            suggestion=suggestion,
        )

    async def reconcile_all(self) -> Dict[str, ReconciledResult]:
        """Reconcile balances for all configured providers."""
        results = {}
        balance_cfg = self._config.get("balance", {})

        for provider_name in balance_cfg:
            if not isinstance(balance_cfg[provider_name], dict):
                continue

            data_points = self._gather_data_points(provider_name)
            results[provider_name] = self.reconcile(provider_name, data_points)

        return results

    def _gather_data_points(self, provider: str) -> List[DataPoint]:
        """Gather all available data points for a provider."""
        points = []

        # 1. Latest API snapshot
        api_snap = self._balance_repo.get_latest_snapshot(provider, "balance")
        if api_snap and api_snap.get("balance_source") == "api" and api_snap.get("balance_amount") is not None:
            points.append(DataPoint(
                value=api_snap["balance_amount"],
                source="api",
                confidence=SOURCE_CONFIDENCE["api"],
                timestamp=datetime.fromisoformat(api_snap["timestamp"]) if api_snap.get("timestamp") else None,
            ))

        # 2. Computed balance (ledger deposits minus DB cost)
        provider_cfg = self._config.get("balance", {}).get(provider, {})
        ledger = provider_cfg.get("ledger", [])
        if ledger:
            total_deposits = sum(e.get("amount", 0) for e in ledger)

            # Use verified_usage_cost if available (higher confidence)
            if provider_cfg.get("verified_usage_cost") is not None:
                try:
                    usage_cost = float(provider_cfg["verified_usage_cost"])
                    computed_balance = total_deposits - usage_cost
                    points.append(DataPoint(
                        value=round(computed_balance, 2),
                        source="ledger",
                        confidence=SOURCE_CONFIDENCE["ledger"],
                    ))
                except (TypeError, ValueError):
                    pass
            else:
                # Compute from DB
                db_cost = self._telemetry_repo.get_total_cost_by_provider(provider)
                computed_balance = total_deposits - db_cost
                points.append(DataPoint(
                    value=round(computed_balance, 2),
                    source="computed",
                    confidence=SOURCE_CONFIDENCE["computed"],
                ))

        # 3. Latest scraped snapshot
        scraped_snap = self._balance_repo.get_latest_snapshot(provider, "balance")
        if (
            scraped_snap
            and scraped_snap.get("balance_source") == "scraper"
            and scraped_snap.get("balance_amount") is not None
        ):
            points.append(DataPoint(
                value=scraped_snap["balance_amount"],
                source="scraped",
                confidence=SOURCE_CONFIDENCE["scraped"],
                timestamp=datetime.fromisoformat(scraped_snap["timestamp"]) if scraped_snap.get("timestamp") else None,
            ))

        return points
