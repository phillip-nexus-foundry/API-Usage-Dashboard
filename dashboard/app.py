"""
App factory: creates and wires the FastAPI application.
Replaces the 2,000-line monolithic app.py.

Three tiers:
  - Data tier: database, repositories, parsers
  - Application tier: services, reconciliation, providers, events
  - Presentation tier: routes, schemas, middleware, static files
"""
import asyncio
import logging
import yaml
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from parsers.session_paths import resolve_sessions_dir

logger = logging.getLogger(__name__)

# Resolve paths relative to the project root (parent of dashboard/)
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def create_app() -> FastAPI:
    """
    Application factory. Creates FastAPI app with all tiers wired together.
    """
    config = _load_config()
    resolved_sessions_dir = resolve_sessions_dir(config.get("sessions_dir"))
    if config.get("sessions_dir") != resolved_sessions_dir:
        logger.warning(
            "Configured sessions_dir '%s' not found; using '%s'",
            config.get("sessions_dir"),
            resolved_sessions_dir,
        )

    # ========== DATA TIER ==========
    from dashboard.data.database import Database
    from dashboard.data.repositories.telemetry_repo import SQLAlchemyTelemetryRepo
    from dashboard.data.repositories.balance_repo import SQLAlchemyBalanceRepo
    from dashboard.data.repositories.file_index_repo import SQLAlchemyFileIndexRepo
    from dashboard.data.repositories.pricing_repo import SQLAlchemyPricingRepo

    db = Database(config)
    db.create_tables()

    telemetry_repo = SQLAlchemyTelemetryRepo(db)
    balance_repo = SQLAlchemyBalanceRepo(db)
    file_index_repo = SQLAlchemyFileIndexRepo(db)
    pricing_repo = SQLAlchemyPricingRepo(db)

    # Seed pricing from config if needed
    model_costs = config.get("model_costs", {})
    if model_costs:
        pricing_repo.seed_from_config(model_costs)

    # ========== APPLICATION TIER ==========
    from dashboard.application.events import EventBus
    from dashboard.application.services.cost_service import CostService
    from dashboard.application.services.balance_service import BalanceService
    from dashboard.application.services.ingestion_service import IngestionService
    from dashboard.application.services.projection_service import ProjectionService
    from dashboard.application.reconciliation.engine import ReconciliationEngine
    from dashboard.application.providers.anthropic import AnthropicProvider
    from dashboard.application.providers.moonshot import MoonshotProvider
    from dashboard.application.providers.minimax import MiniMaxProvider

    event_bus = EventBus()
    cost_service = CostService(pricing_repo, config)
    reconciliation_engine = ReconciliationEngine(balance_repo, telemetry_repo, config)

    providers = {
        "anthropic": AnthropicProvider(config),
        "moonshot": MoonshotProvider(config),
        "minimax": MiniMaxProvider(config),
    }

    # Legacy components (bridge to existing code during transition)
    balance_poller = None
    try:
        from balance.poller import BalancePoller
        # Use /app/data/ for persistent storage (Docker volume mount)
        data_dir = PROJECT_ROOT / "data"
        data_dir.mkdir(exist_ok=True)
        balance_poller = BalancePoller(
            config,
            db_path=str(data_dir / "dashboard.db"),
            profiles_dir=str(PROJECT_ROOT / "browser_profiles"),
            config_path=str(CONFIG_PATH),
            alert_threshold_pct=5.0,
            autocorrect_threshold_pct=10.0,
            auto_correct=False,
        )
    except Exception as e:
        logger.warning(f"Legacy BalancePoller not available: {e}")

    balance_service = BalanceService(
        balance_repo=balance_repo,
        telemetry_repo=telemetry_repo,
        reconciliation_engine=reconciliation_engine,
        providers=providers,
        config=config,
        event_bus=event_bus,
        balance_poller=balance_poller,
    )

    ingestion_service = IngestionService(
        telemetry_repo=telemetry_repo,
        file_index_repo=file_index_repo,
        cost_service=cost_service,
        event_bus=event_bus,
        sessions_dir=resolved_sessions_dir,
    )

    projection_service = ProjectionService(telemetry_repo, config)

    # ========== PRESENTATION TIER ==========
    from dashboard.presentation.routes import telemetry, balance, projection, system, resources, ratelimits, spendlimits
    from dashboard.presentation.middleware import setup_middleware

    # Initialize route modules with dependencies
    telemetry.init(telemetry_repo, config, balance_service=balance_service)
    balance.init(balance_service, balance_repo, config, str(CONFIG_PATH))
    projection.init(projection_service, telemetry_repo, config)
    system.init(
        config,
        db,
        ingestion_service=ingestion_service,
        balance_service=balance_service,
        balance_poller=balance_poller,
    )
    resources.init(config, balance_poller, db)
    ratelimits.init(config, str(CONFIG_PATH), db)
    spendlimits.init(config, str(CONFIG_PATH), db)

    # ========== LIFECYCLE ==========
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Application lifecycle: startup scan + watcher, clean shutdown."""
        # Initial scan
        logger.info("Scanning session files...")
        ingestion_service.scan_all()

        # File watcher
        observer = ingestion_service.setup_file_watcher()

        # Scheduler
        scheduler = None
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from dashboard.application.scheduler import configure_scheduler

            scheduler = AsyncIOScheduler()
            configure_scheduler(scheduler, balance_service, reconciliation_engine)

            # Legacy poller jobs (bridge during transition)
            if balance_poller:
                async def _legacy_poll():
                    try:
                        await balance_poller.poll_all(
                            ["anthropic", "elevenlabs", "codex_cli", "moonshot", "minimax"]
                        )
                    except Exception as e:
                        logger.error(f"Legacy poll failed: {e}")

                scheduler.add_job(
                    _legacy_poll,
                    trigger="cron",
                    id="legacy_resource_poll",
                    replace_existing=True,
                    hour="7-21",
                    minute="*/15",
                    jitter=900,
                    coalesce=True,
                    max_instances=1,
                )

            scheduler.start()
        except ImportError:
            logger.warning("APScheduler not installed; scheduler disabled")

        logger.info("Startup complete")
        try:
            yield
        finally:
            logger.info("Shutting down...")
            if scheduler:
                scheduler.shutdown(wait=False)
            if observer:
                observer.stop()
                observer.join(timeout=3)

    # ========== CREATE APP ==========
    app = FastAPI(
        title="API Usage Dashboard",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Middleware
    setup_middleware(app)

    # Register routers
    app.include_router(telemetry.router, prefix="/api")
    app.include_router(balance.router, prefix="/api")
    app.include_router(projection.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(resources.router, prefix="/api")
    app.include_router(ratelimits.router, prefix="/api")
    app.include_router(spendlimits.router, prefix="/api")

    # Static files - serve from presentation/static/ with fallback to legacy static/
    static_dir = Path(__file__).parent / "presentation" / "static"
    legacy_static = PROJECT_ROOT / "static"

    # Use whichever static dir has files
    if static_dir.exists() and any(static_dir.iterdir()):
        serve_static = static_dir
    elif legacy_static.exists():
        serve_static = legacy_static
    else:
        serve_static = static_dir
        serve_static.mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=str(serve_static)), name="static")

    @app.get("/")
    async def dashboard():
        """Serve dashboard HTML."""
        html_path = serve_static / "index.html"
        if html_path.exists():
            return FileResponse(html_path, media_type="text/html")
        return {"error": "Dashboard HTML not found", "path": str(html_path)}

    logger.info(f"App created: db={db.url}, static={serve_static}")
    return app
