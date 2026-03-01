"""
Scheduler configuration.
Decoupled APScheduler job definitions for background tasks.
"""
import logging

logger = logging.getLogger(__name__)


def configure_scheduler(scheduler, balance_service, reconciliation_engine):
    """Register all scheduled jobs. Called once during app startup."""

    async def _balance_poll_job():
        try:
            await balance_service.check_all_api_balances()
            logger.info("API balance poll completed")
        except Exception as e:
            logger.error(f"API balance poll failed: {e}")

    async def _reconciliation_job():
        try:
            results = await reconciliation_engine.reconcile_all()
            for provider, result in results.items():
                logger.info(
                    f"Reconciled {provider}: {result.resolved_balance:.2f} "
                    f"(confidence: {result.confidence:.2f}, method: {result.method})"
                )

                # Check for drift
                drift = reconciliation_engine.detect_drift(provider)
                if drift:
                    logger.warning(f"DRIFT ALERT for {provider}: {drift.suggestion}")
        except Exception as e:
            logger.error(f"Reconciliation job failed: {e}")

    # Every 5 minutes: check API balances for providers that support it
    scheduler.add_job(
        _balance_poll_job,
        trigger="interval",
        minutes=5,
        id="api_balance_poll",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    # Every 15 minutes: run reconciliation
    scheduler.add_job(
        _reconciliation_job,
        trigger="interval",
        minutes=15,
        id="reconciliation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    logger.info("Scheduler configured with balance polling and reconciliation jobs")
