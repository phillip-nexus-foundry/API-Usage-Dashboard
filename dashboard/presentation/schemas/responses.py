"""
Pydantic response models for the API.
Defines the contract between frontend and backend.
"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class BalanceResponse(BaseModel):
    """Balance for a single provider."""
    status: str  # ok, warn, critical, error
    remaining: Optional[float] = None
    total_deposits: Optional[float] = None
    cumulative_cost: Optional[float] = None
    cost_source: Optional[str] = None  # computed, verified_override, api
    balance_source: Optional[str] = None  # api, ledger, reconciled
    confidence: Optional[float] = None  # 0.0-1.0
    warn_threshold: Optional[float] = None
    critical_threshold: Optional[float] = None
    drift_pct: Optional[float] = None
    usage_calls: Optional[int] = None
    usage_cost: Optional[float] = None
    usage_tokens: Optional[int] = None
    personal_invested: Optional[float] = None
    ledger: Optional[List[Dict[str, Any]]] = None
    projects: Optional[Dict[str, Any]] = None
    scrape_error: Optional[str] = None


class SummaryResponse(BaseModel):
    """Aggregated KPIs."""
    timestamp: str
    total_calls: int
    total_cost: float
    total_tokens: int
    error_rate: float
    error_count: int
    session_count: int
    parse_errors: int = 0
    earliest_timestamp: Optional[int] = None
    latest_timestamp: Optional[int] = None
    by_provider: List[Dict[str, Any]]
    by_model: List[Dict[str, Any]]
    configured_providers: List[str]
    configured_models: List[str]


class ReconciliationEntry(BaseModel):
    """Single reconciliation audit log entry."""
    provider: str
    timestamp: Optional[str] = None
    computed_balance: Optional[float] = None
    api_balance: Optional[float] = None
    scraped_balance: Optional[float] = None
    resolved_balance: float
    confidence: float
    method: str
    drift_amount: Optional[float] = None
    drift_pct: Optional[float] = None
    notes: Optional[str] = None


class HealthResponse(BaseModel):
    """System health check."""
    status: str  # healthy, degraded
    database: str  # connected, error
    database_type: str  # sqlite, postgresql
    sessions_dir: str
    providers: Dict[str, str]  # provider -> status
    last_scan: Optional[str] = None
    record_count: Optional[int] = None
