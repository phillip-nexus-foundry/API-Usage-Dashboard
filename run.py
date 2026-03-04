"""
Entry point for the API Usage Dashboard.
Launches the FastAPI app via uvicorn.

Usage:
    python run.py              # Start with default config
    python run.py --migrate    # Run DB migrations then start
    python run.py --init-db    # Create tables and seed pricing, then start
"""
import os
import sys
import yaml
import uvicorn
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def init_database(config: dict):
    """Create all tables and seed pricing from config."""
    from dashboard.data.database import Database
    from dashboard.data.repositories.pricing_repo import SQLAlchemyPricingRepo

    db = Database(config)
    db.create_tables()
    logger.info(f"Database tables created ({db.url})")

    # Seed pricing history from config
    model_costs = config.get("model_costs", {})
    if model_costs:
        pricing_repo = SQLAlchemyPricingRepo(db)
        created = pricing_repo.seed_from_config(model_costs)
        if created:
            logger.info(f"Seeded {created} pricing entries from config.yaml")


def main():
    config = load_config()

    if "--init-db" in sys.argv:
        init_database(config)

    server = config.get("server", {})
    host = os.environ.get("HOST", server.get("host", "127.0.0.1"))
    port = int(os.environ.get("PORT", server.get("port", 8050)))

    logger.info(f"Starting API Usage Dashboard on {host}:{port}")
    uvicorn.run(
        "dashboard.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
