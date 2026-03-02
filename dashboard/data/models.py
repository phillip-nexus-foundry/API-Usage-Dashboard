"""
SQLAlchemy ORM models for the API Usage Dashboard.
6 tables: records, file_index, resource_snapshots, reconciliation_log,
pricing_history, provider_state.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, BigInteger, Float, String, Text, Boolean, DateTime,
    UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utc_now():
    return datetime.now(timezone.utc)


class Record(Base):
    """Telemetry record from an API call."""
    __tablename__ = "records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String(128), unique=True, nullable=False)
    session_id = Column(String(128), nullable=False, index=True)
    parent_id = Column(String(128), nullable=True)
    timestamp = Column(BigInteger, nullable=False, index=True)  # epoch ms
    timestamp_iso = Column(String(64), nullable=False)
    api = Column(String(64), nullable=False)
    provider = Column(String(64), nullable=False, index=True)
    model = Column(String(128), nullable=False, index=True)
    stop_reason = Column(String(64), nullable=True)

    # Token counts
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    tokens_cache_read = Column(Integer, default=0)
    tokens_cache_write = Column(Integer, default=0)
    tokens_total = Column(Integer, default=0)
    cache_hit_ratio = Column(Float, default=0.0)

    # Computed costs (USD)
    cost_input = Column(Float, default=0.0)
    cost_output = Column(Float, default=0.0)
    cost_cache_read = Column(Float, default=0.0)
    cost_cache_write = Column(Float, default=0.0)
    cost_total = Column(Float, default=0.0)

    # NEW: API-reported cost for accuracy cross-check
    cost_api_reported = Column(Float, nullable=True)

    # Metadata
    has_thinking = Column(Boolean, default=False)
    has_tool_calls = Column(Boolean, default=False)
    tool_names = Column(Text, default="[]")  # JSON array
    content_length = Column(Integer, default=0)
    is_error = Column(Boolean, default=False)

    # NEW: Request latency
    duration_ms = Column(Integer, nullable=True)

    # Source tracking
    source_file = Column(String(512), nullable=True)
    source_line = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=_utc_now)


class FileIndex(Base):
    """Tracks parsed JSONL files for incremental processing."""
    __tablename__ = "file_index"

    path = Column(String(1024), primary_key=True)
    mtime = Column(Float, nullable=False)
    record_count = Column(Integer, default=0)
    parser_version = Column(String(64), nullable=True)

    # NEW: Enhanced change detection
    size = Column(Integer, nullable=True)
    checksum = Column(String(64), nullable=True)  # MD5 of first+last 1KB
    last_line_processed = Column(Integer, default=0)

    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class ResourceSnapshot(Base):
    """Balance/resource snapshot from any data source."""
    __tablename__ = "resource_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False, index=True)
    snapshot_type = Column(String(32), default="balance")  # balance, rate_limit

    timestamp = Column(DateTime, default=_utc_now, index=True)

    # Balance data
    balance_amount = Column(Float, nullable=True)
    balance_currency = Column(String(8), default="USD")
    balance_source = Column(String(32), nullable=True)  # api, scraper, computed, ledger

    # Rate limit data
    tier = Column(String(32), nullable=True)
    total_credits = Column(Float, nullable=True)
    rpm_limit = Column(Integer, nullable=True)
    rpm_used = Column(Integer, nullable=True)
    tpm_limit = Column(Integer, nullable=True)
    tpm_used = Column(Integer, nullable=True)

    # Computed vs actual
    computed_cost = Column(Float, nullable=True)
    drift_amount = Column(Float, nullable=True)
    drift_pct = Column(Float, nullable=True)

    # NEW: Confidence score (0.0-1.0)
    confidence = Column(Float, default=1.0)

    # Raw response for debugging
    raw_response = Column(Text, nullable=True)  # JSON
    error = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_snapshots_provider_time", "provider", "timestamp"),
        Index("idx_snapshots_type_time", "snapshot_type", "timestamp"),
    )


class ReconciliationLog(Base):
    """Audit trail of balance reconciliations across data sources."""
    __tablename__ = "reconciliation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=_utc_now, index=True)

    # Values from each source
    computed_balance = Column(Float, nullable=True)
    api_balance = Column(Float, nullable=True)
    scraped_balance = Column(Float, nullable=True)

    # Resolved result
    resolved_balance = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)  # 0.0-1.0
    method = Column(String(64), nullable=False)
    # Methods: api_authoritative, weighted_average, computed_only, ledger_only

    # Drift analysis
    drift_amount = Column(Float, nullable=True)
    drift_pct = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)


class PricingHistory(Base):
    """Versioned model pricing for accurate historical cost computation."""
    __tablename__ = "pricing_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)

    # Per 1M tokens
    input_price = Column(Float, nullable=False)
    output_price = Column(Float, nullable=False)
    cache_read_price = Column(Float, default=0.0)
    cache_write_price = Column(Float, default=0.0)

    effective_from = Column(DateTime, nullable=False)
    effective_until = Column(DateTime, nullable=True)  # NULL = still current
    source = Column(String(32), default="config")  # config, api, manual

    __table_args__ = (
        Index("idx_pricing_model_time", "provider", "model", "effective_from"),
    )


class ProviderState(Base):
    """Tracks provider health and data source availability."""
    __tablename__ = "provider_state"

    provider = Column(String(64), primary_key=True)
    last_api_check = Column(DateTime, nullable=True)
    last_scrape = Column(DateTime, nullable=True)
    api_available = Column(Boolean, default=True)
    scrape_needed = Column(Boolean, default=True)
    last_error = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)
