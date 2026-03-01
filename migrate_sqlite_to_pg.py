"""
One-time migration script: SQLite (dashboard.db) -> PostgreSQL.
Migrates records, file_index, and resource_snapshots.
Seeds pricing_history from config.yaml.

Usage:
    python migrate_sqlite_to_pg.py
"""
import sqlite3
import json
import yaml
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def migrate():
    config = load_config()

    # Initialize new database
    from dashboard.data.database import Database
    from dashboard.data.models import Base, Record, FileIndex, ResourceSnapshot
    from dashboard.data.repositories.pricing_repo import SQLAlchemyPricingRepo

    db = Database(config)
    db.create_tables()
    logger.info(f"Target database ready: {db.url}")

    # Connect to SQLite source
    sqlite_path = config.get("database_path", "dashboard.db")
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    logger.info(f"Source SQLite: {sqlite_path}")

    # Migrate records
    cursor = src.execute("SELECT COUNT(*) FROM records")
    total_records = cursor.fetchone()[0]
    logger.info(f"Migrating {total_records} records...")

    batch_size = 500
    migrated = 0
    cursor = src.execute("SELECT * FROM records ORDER BY rowid")

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break

        with db.session() as session:
            for row in rows:
                data = dict(row)
                # Map SQLite column names to ORM field names
                record = Record(
                    call_id=data.get("call_id"),
                    session_id=data.get("session_id"),
                    parent_id=data.get("parent_id"),
                    timestamp=data.get("timestamp"),
                    timestamp_iso=data.get("timestamp_iso", ""),
                    api=data.get("api", ""),
                    provider=data.get("provider", ""),
                    model=data.get("model", ""),
                    stop_reason=data.get("stop_reason"),
                    tokens_input=data.get("tokens_input", 0),
                    tokens_output=data.get("tokens_output", 0),
                    tokens_cache_read=data.get("tokens_cache_read", 0),
                    tokens_cache_write=data.get("tokens_cache_write", 0),
                    tokens_total=data.get("tokens_total", 0),
                    cache_hit_ratio=data.get("cache_hit_ratio", 0.0),
                    cost_input=data.get("cost_input", 0.0),
                    cost_output=data.get("cost_output", 0.0),
                    cost_cache_read=data.get("cost_cache_read", 0.0),
                    cost_cache_write=data.get("cost_cache_write", 0.0),
                    cost_total=data.get("cost_total", 0.0),
                    has_thinking=bool(data.get("has_thinking", False)),
                    has_tool_calls=bool(data.get("has_tool_calls", False)),
                    tool_names=data.get("tool_names", "[]"),
                    content_length=data.get("content_length", 0),
                    is_error=bool(data.get("is_error", False)),
                    source_file=data.get("source_file"),
                )
                session.add(record)
            migrated += len(rows)

        logger.info(f"  Migrated {migrated}/{total_records} records")

    # Migrate file_index
    try:
        cursor = src.execute("SELECT * FROM file_index")
        fi_rows = cursor.fetchall()
        with db.session() as session:
            for row in fi_rows:
                data = dict(row)
                fi = FileIndex(
                    path=data.get("filepath", data.get("path", "")),
                    mtime=data.get("mtime", 0),
                    record_count=data.get("record_count", 0),
                    parser_version=data.get("parser_version"),
                )
                session.add(fi)
        logger.info(f"Migrated {len(fi_rows)} file_index entries")
    except Exception as e:
        logger.warning(f"file_index migration skipped: {e}")

    # Migrate resource_snapshots
    try:
        cursor = src.execute("SELECT * FROM resource_snapshots")
        snap_rows = cursor.fetchall()
        with db.session() as session:
            for row in snap_rows:
                data = dict(row)
                snap = ResourceSnapshot(
                    provider=data.get("provider", ""),
                    snapshot_type=data.get("snapshot_type", "balance"),
                    balance_amount=data.get("balance_amount"),
                    balance_currency=data.get("balance_currency", "USD"),
                    balance_source=data.get("balance_source"),
                    tier=data.get("tier"),
                    total_credits=data.get("total_credits"),
                    rpm_limit=data.get("rpm_limit"),
                    rpm_used=data.get("rpm_used"),
                    computed_cost=data.get("computed_cost"),
                    drift_amount=data.get("drift_amount"),
                    drift_pct=data.get("drift_pct"),
                    raw_response=data.get("raw_response"),
                    error=data.get("error"),
                )
                session.add(snap)
        logger.info(f"Migrated {len(snap_rows)} resource_snapshots")
    except Exception as e:
        logger.warning(f"resource_snapshots migration skipped: {e}")

    # Seed pricing history from config
    model_costs = config.get("model_costs", {})
    pricing_repo = SQLAlchemyPricingRepo(db)
    created = pricing_repo.seed_from_config(model_costs)
    logger.info(f"Seeded {created} pricing entries from config.yaml")

    src.close()
    logger.info("Migration complete!")


if __name__ == "__main__":
    migrate()
