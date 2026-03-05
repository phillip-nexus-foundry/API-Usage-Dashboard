"""
FastAPI application for API Usage Dashboard.
14 endpoints serving telemetry data and dashboard HTML.
"""
import os
import json
import time
import asyncio
import calendar
import yaml
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import asdict
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
import aiosqlite
import sqlite3
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except Exception:  # pragma: no cover - optional dependency fallback
    AsyncIOScheduler = None

from parsers.openclaw_reader import OpenClawReader
from balance.checker import BalanceChecker
from balance.poller import BalancePoller
from evals.evaluator import Evaluator


# ============================================================================
# RATE LIMIT AUTO-DETECTION
# ============================================================================

# Known Anthropic model families and a representative model to probe for each
ANTHROPIC_FAMILIES = {
    "claude-opus": {
        "probe_model": "claude-opus-4-6",
        "models": ["claude-opus-4-6"],
    },
    "claude-sonnet": {
        "probe_model": "claude-sonnet-4-6",
        "models": ["claude-sonnet-4-6", "claude-3-5-sonnet-20241022"],
    },
    "claude-haiku": {
        "probe_model": "claude-haiku-4-5-20251001",
        "models": ["claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-3-5-haiku-20241022", "claude-3-haiku-20240307"],
    },
}


async def probe_anthropic_rate_limits() -> Dict[str, Any]:
    """
    Probe Anthropic API with a minimal request per model family to read
    actual rate limit headers. Returns detected limits per family.
    """
    import httpx as _httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set, skipping rate limit auto-detection")
        return {}

    detected = {}
    for family_name, family_info in ANTHROPIC_FAMILIES.items():
        probe_model = family_info["probe_model"]
        try:
            async with _httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": probe_model,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    timeout=15.0,
                )

                rpm = resp.headers.get("anthropic-ratelimit-requests-limit")
                tpm = resp.headers.get("anthropic-ratelimit-tokens-limit")
                input_tpm = resp.headers.get("anthropic-ratelimit-input-tokens-limit")
                output_tpm = resp.headers.get("anthropic-ratelimit-output-tokens-limit")

                limits = {}
                if rpm:
                    limits["rpm"] = int(rpm)
                if tpm:
                    limits["tpm"] = int(tpm)
                if input_tpm:
                    limits["input_tpm"] = int(input_tpm)
                if output_tpm:
                    limits["output_tpm"] = int(output_tpm)
                limits["models"] = family_info["models"]
                limits["auto_detected"] = True

                detected[family_name] = limits
                logger.info(f"Auto-detected {family_name} rate limits: RPM={rpm} TPM={tpm} InputTPM={input_tpm} OutputTPM={output_tpm}")

        except Exception as e:
            logger.warning(f"Failed to probe rate limits for {family_name}: {e}")

    return detected


async def apply_auto_detected_limits():
    """
    Probe APIs for rate limits and merge into CONFIG.
    Auto-detected values are used unless the user has manually overridden them.
    """
    global CONFIG

    detected = await probe_anthropic_rate_limits()
    if not detected:
        return

    if "rate_limits" not in CONFIG:
        CONFIG["rate_limits"] = {}

    changed = False
    for family_name, detected_limits in detected.items():
        existing = CONFIG["rate_limits"].get(family_name, {})

        # Update if: family doesn't exist yet, was previously auto-detected,
        # or has never been explicitly marked (first run before any auto-detection)
        if not existing or existing.get("auto_detected") or "auto_detected" not in existing:
            CONFIG["rate_limits"][family_name] = detected_limits
            changed = True
        # else: Family was manually edited (auto_detected explicitly removed) — skip

    if changed:
        try:
            with open(config_path, "w") as f:
                yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
            logger.info("Saved auto-detected rate limits to config.yaml")
        except Exception as e:
            logger.error(f"Failed to save auto-detected rate limits: {e}")


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
config_path = Path(__file__).parent / "config.yaml"
with open(config_path) as f:
    CONFIG = yaml.safe_load(f)

# Initialize components
reader = OpenClawReader(
    db_path="dashboard.db",
    sessions_dir=CONFIG["sessions_dir"]
)
balance_checker = BalanceChecker(CONFIG, config_path=str(config_path))
evaluator = Evaluator(CONFIG)
balance_poller = BalancePoller(
    CONFIG,
    db_path=reader.db_path,
    profiles_dir=str(Path(__file__).parent / "browser_profiles"),
    config_path=str(config_path),
    alert_threshold_pct=5.0,
    autocorrect_threshold_pct=10.0,
    auto_correct=False,
)
scheduler = None
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

def _utc_now() -> datetime:
    """Timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _resource_status(snapshot: Dict[str, Any]) -> str:
    if snapshot.get("error"):
        return "critical"
    balance = snapshot.get("balance_amount")
    total_credits = snapshot.get("total_credits")
    if balance is not None and total_credits:
        try:
            pct_remaining = (float(balance) / float(total_credits)) * 100.0
            if pct_remaining <= 10:
                return "critical"
            if pct_remaining <= 25:
                return "warn"
        except Exception:
            pass
    if balance is not None:
        if balance <= 2:
            return "critical"
        if balance <= 10:
            return "warn"
    drift_pct = snapshot.get("drift_percentage")
    if drift_pct is not None:
        if abs(drift_pct) >= 10:
            return "critical"
        if abs(drift_pct) >= 5:
            return "warn"
    return snapshot.get("status") or "ok"


async def _run_resource_poll() -> List[Dict[str, Any]]:
    _reload_config_from_disk()
    balance_poller.refresh_config(CONFIG)
    # Poll all providers in background so both Balance and Resource cards stay live.
    return await balance_poller.poll_all(["anthropic", "elevenlabs", "codex_cli", "moonshot", "minimax"])


async def _resource_poll_job():
    try:
        await _run_resource_poll()
        logger.info("Resource polling job completed")
    except Exception as e:
        logger.error(f"Resource polling job failed: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifecycle: startup scan + watcher, clean shutdown."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    logger.info("Starting background session scan...")

    async def _initial_scan():
        try:
            await asyncio.to_thread(reader.scan)
            if reader.parse_errors:
                logger.warning(f"Encountered {len(reader.parse_errors)} parse errors during scan")
            logger.info("Initial background scan complete")
        except Exception as e:
            logger.error(f"Initial background scan failed: {e}")

    asyncio.create_task(_initial_scan())

    # File watcher: auto-scan when session files change
    class SessionFileHandler(FileSystemEventHandler):
        def __init__(self):
            self._timer = None
            self._last_scan_time = 0
            self._min_scan_interval = 5.0  # Minimum 5 seconds between scans

        def _debounced_scan(self):
            """Debounce: wait 1s after last change before scanning."""
            # Rate limit: don't scan too frequently
            import time
            now = time.time()
            if now - self._last_scan_time < self._min_scan_interval:
                logger.debug("Skipping scan - too soon since last scan")
                return
            
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(1.0, self._do_scan)
            self._timer.start()

        def _do_scan(self):
            import time
            self._last_scan_time = time.time()
            logger.info("File change detected, rescanning...")
            reader.scan()

        def on_modified(self, event):
            if event.src_path.endswith(".jsonl"):
                self._debounced_scan()

        def on_created(self, event):
            if event.src_path.endswith(".jsonl"):
                self._debounced_scan()

    observer = Observer()
    observer.schedule(SessionFileHandler(), CONFIG["sessions_dir"], recursive=False)
    observer.daemon = True
    observer.start()
    logger.info(f"Watching {CONFIG['sessions_dir']} for changes")

    # Auto-detect rate limits from provider APIs
    logger.info("Probing APIs for rate limits...")
    await apply_auto_detected_limits()

    # Resource polling scheduler
    global scheduler
    if AsyncIOScheduler:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            _resource_poll_job,
            trigger="cron",
            id="resource_poll_every_minute",
            replace_existing=True,
            minute="*",
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
    else:
        logger.warning("APScheduler not installed; background polling scheduler disabled")

    # Initial poll on startup for quick visibility (non-blocking).
    asyncio.create_task(_resource_poll_job())
    logger.info("Startup complete")
    try:
        yield
    finally:
        logger.info("Stopping file watcher...")
        if scheduler:
            scheduler.shutdown(wait=False)
        observer.stop()
        observer.join(timeout=3)


# Create FastAPI app
app = FastAPI(title="API Usage Dashboard", lifespan=lifespan)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _build_filter(provider: Optional[str] = None, model: Optional[str] = None):
    """Build WHERE clause and params for provider/model filtering."""
    clauses = []
    params = []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if model:
        clauses.append("model = ?")
        params.append(model)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _reload_config_from_disk():
    """Reload config.yaml and propagate it to components."""
    global CONFIG
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            CONFIG = yaml.safe_load(f) or {}
        balance_checker.config = CONFIG
        evaluator.config = CONFIG
        balance_poller.refresh_config(CONFIG)
    except Exception as e:
        logger.warning(f"Failed to reload config.yaml: {e}")


def _configured_providers() -> List[str]:
    """Provider names configured under balance."""
    balance_cfg = CONFIG.get("balance", {})
    if not isinstance(balance_cfg, dict):
        return []
    return sorted([name for name, cfg in balance_cfg.items() if isinstance(cfg, dict)])


def _configured_models() -> List[str]:
    """Model IDs configured under model_costs."""
    model_costs = CONFIG.get("model_costs", {})
    if not isinstance(model_costs, dict):
        return []
    return sorted(model_costs.keys())


async def _provider_cost_overrides() -> Dict[str, float]:
    """
    Canonical provider-level cumulative costs from balance checker.
    Includes verified/adjusted reconciliations from config when present.
    """
    overrides: Dict[str, float] = {}
    try:
        balances = await balance_checker.check_balances(reader)
        for prov, data in balances.items():
            if not isinstance(data, dict):
                continue
            cumulative = data.get("cumulative_cost")
            if cumulative is None:
                continue
            try:
                overrides[prov] = float(cumulative)
            except (TypeError, ValueError):
                continue
    except Exception as e:
        logger.warning(f"Failed to load provider cost overrides: {e}")
    return overrides


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
async def dashboard():
    """Serve dashboard HTML."""
    html_path = Path(__file__).parent / "static" / "index.html"
    return FileResponse(html_path, media_type="text/html")


@app.get("/api/summary")
async def summary(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Aggregate KPIs: total calls, cost, tokens, error rate, by-provider, by-model.
    Supports optional provider/model filters and time range.
    """
    _reload_config_from_disk()
    # Treat empty query params as unset so all-time override logic still applies.
    if provider == "":
        provider = None
    if model == "":
        model = None
    where, params = _build_filter(provider, model)
    if start:
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(start)
    if end:
        where = (where + " AND " if where else " WHERE ") + "timestamp <= ?"
        params.append(end)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        # Aggregate stats (filtered)
        cursor.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(cost_total),0), COALESCE(SUM(tokens_total),0),
                   SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END),
                   COUNT(DISTINCT session_id),
                   MIN(timestamp), MAX(timestamp)
            FROM records{where}
        """, params)
        row = cursor.fetchone()
        total_calls, total_cost, total_tokens, error_count, session_count, earliest_ts, latest_ts = row
        error_rate = round(error_count / total_calls, 4) if total_calls > 0 else 0.0

        # By provider (filtered)
        cursor.execute(f"""
            SELECT provider, COUNT(*) as calls, SUM(cost_total) as cost, SUM(tokens_total) as tokens
            FROM records{where}
            GROUP BY provider
            ORDER BY calls DESC
        """, params)
        by_provider_rows = [
            {"provider": r[0], "calls": r[1], "cost": round(r[2] or 0, 6), "tokens": r[3] or 0}
            for r in cursor.fetchall()
        ]
        provider_map = {entry["provider"]: entry for entry in by_provider_rows}
        configured_providers = _configured_providers()
        if provider:
            if provider in configured_providers and provider not in provider_map:
                provider_map[provider] = {"provider": provider, "calls": 0, "cost": 0.0, "tokens": 0}
        else:
            for prov in configured_providers:
                provider_map.setdefault(prov, {"provider": prov, "calls": 0, "cost": 0.0, "tokens": 0})
        by_provider = sorted(provider_map.values(), key=lambda x: (-x["calls"], x["provider"]))

        # By model (filtered)
        cursor.execute(f"""
            SELECT model, COUNT(*) as calls, SUM(cost_total) as cost, SUM(tokens_total) as tokens
            FROM records{where}
            GROUP BY model
            ORDER BY calls DESC
        """, params)
        by_model_rows = [
            {"model": r[0], "calls": r[1], "cost": round(r[2] or 0, 6), "tokens": r[3] or 0}
            for r in cursor.fetchall()
        ]
        model_map = {entry["model"]: entry for entry in by_model_rows}
        configured_models = _configured_models()
        if model:
            if model in configured_models and model not in model_map:
                model_map[model] = {"model": model, "calls": 0, "cost": 0.0, "tokens": 0}
        else:
            for mdl in configured_models:
                model_map.setdefault(mdl, {"model": mdl, "calls": 0, "cost": 0.0, "tokens": 0})
        by_model = sorted(model_map.values(), key=lambda x: (-x["calls"], x["model"]))

        # All-time summaries should respect provider reconciliations
        # (verified usage overrides / adjustments) when model isn't filtered.
        if start is None and end is None and model is None:
            overrides = await _provider_cost_overrides()
            if overrides:
                # Apply override to already-present providers.
                for entry in by_provider:
                    prov = entry["provider"]
                    if prov in overrides:
                        entry["cost"] = round(overrides[prov], 6)
                # Include configured providers that may not have DB rows yet.
                existing = {entry["provider"] for entry in by_provider}
                for prov, cost in overrides.items():
                    if prov not in existing:
                        by_provider.append(
                            {"provider": prov, "calls": 0, "cost": round(cost, 6), "tokens": 0}
                        )
                by_provider = sorted(by_provider, key=lambda x: (-x["calls"], x["provider"]))
                total_cost = sum(entry["cost"] for entry in by_provider)

    return {
        "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
        "total_calls": total_calls,
        "total_cost": round(total_cost, 6),
        "total_tokens": total_tokens,
        "error_rate": error_rate,
        "error_count": error_count,
        "session_count": session_count,
        "parse_errors": len(reader.parse_errors),
        "earliest_timestamp": earliest_ts,
        "latest_timestamp": latest_ts,
        "by_provider": by_provider,
        "by_model": by_model,
        "configured_providers": _configured_providers(),
        "configured_models": _configured_models(),
    }


@app.get("/api/timeseries")
async def timeseries(
    interval: str = Query("hour"),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Time-bucketed data for line/bar charts.
    Interval: minute, hour, day, week, month. Supports provider/model filters and time range.
    Returns per-provider cost breakdown for stacked charts.
    """
    _reload_config_from_disk()
    bucket_size = {
        "minute": 60,
        "hour": 3600,
        "day": 86400,
        "week": 604800,
        "month": 2592000,  # ~30 days
    }.get(interval, 3600)

    where, params = _build_filter(provider, model)

    # Add time range filters
    if start:
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(start)
    if end:
        where = (where + " AND " if where else " WHERE ") + "timestamp <= ?"
        params.append(end)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        # Aggregate totals per bucket
        cursor.execute(f"""
            SELECT
                (timestamp / 1000 / {bucket_size}) * {bucket_size} * 1000 as bucket,
                COUNT(*) as calls,
                SUM(cost_total) as cost,
                SUM(tokens_total) as tokens,
                SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END) as errors
            FROM records{where}
            GROUP BY bucket
            ORDER BY bucket ASC
        """, params)

        data = []
        for row in cursor:
            data.append({
                "timestamp": int(row[0]),
                "calls": row[1],
                "cost": round(row[2] or 0, 6),
                "tokens": row[3] or 0,
                "errors": row[4],
            })

        # Per-provider cost breakdown per bucket (for stacked bar chart)
        cursor.execute(f"""
            SELECT
                (timestamp / 1000 / {bucket_size}) * {bucket_size} * 1000 as bucket,
                provider,
                SUM(cost_total) as cost
            FROM records{where}
            GROUP BY bucket, provider
            ORDER BY bucket ASC
        """, params)

        provider_costs = {}
        for row in cursor:
            bucket_ts = int(row[0])
            prov = row[1]
            cost = round(row[2] or 0, 6)
            if prov not in provider_costs:
                provider_costs[prov] = {}
            provider_costs[prov][bucket_ts] = cost

        # Per-provider token breakdown per bucket (for multi-provider token chart)
        cursor.execute(f"""
            SELECT
                (timestamp / 1000 / {bucket_size}) * {bucket_size} * 1000 as bucket,
                provider,
                SUM(tokens_total) as tokens,
                SUM(cost_total) as cost
            FROM records{where}
            GROUP BY bucket, provider
            ORDER BY bucket ASC
        """, params)

        provider_tokens = {}
        for row in cursor:
            bucket_ts = int(row[0])
            prov = row[1]
            tokens = row[2] or 0
            cost = round(row[3] or 0, 6)
            if prov not in provider_tokens:
                provider_tokens[prov] = {}
            provider_tokens[prov][bucket_ts] = {"tokens": tokens, "cost": cost}

    configured_providers = _configured_providers()
    if provider:
        if provider in configured_providers:
            provider_costs.setdefault(provider, {})
            provider_tokens.setdefault(provider, {})
    else:
        for prov in configured_providers:
            provider_costs.setdefault(prov, {})
            provider_tokens.setdefault(prov, {})

    return {
        "interval": interval,
        "data": data,
        "provider_costs": provider_costs,
        "provider_tokens": provider_tokens,
        "configured_providers": configured_providers,
    }


@app.get("/api/calls")
async def calls(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    min_tokens: Optional[int] = Query(None),
    max_tokens: Optional[int] = Query(None),
    min_cost: Optional[float] = Query(None),
    max_cost: Optional[float] = Query(None),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Paginated individual call list with all 24 fields.
    Supports provider, model, token range, cost range, and time range filters.
    """
    offset = (page - 1) * per_page

    # Build WHERE clause for filters
    where_clauses = []
    params = []
    if provider:
        where_clauses.append("provider = ?")
        params.append(provider)
    if model:
        where_clauses.append("model = ?")
        params.append(model)
    if start is not None:
        where_clauses.append("timestamp >= ?")
        params.append(start)
    if end is not None:
        where_clauses.append("timestamp <= ?")
        params.append(end)
    if min_tokens is not None:
        where_clauses.append("tokens_total >= ?")
        params.append(min_tokens)
    if max_tokens is not None:
        where_clauses.append("tokens_total <= ?")
        params.append(max_tokens)
    if min_cost is not None:
        where_clauses.append("cost_total >= ?")
        params.append(min_cost)
    if max_cost is not None:
        where_clauses.append("cost_total <= ?")
        params.append(max_cost)
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    with sqlite3.connect(reader.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Total count (with filters)
        cursor.execute(f"SELECT COUNT(*) FROM records{where_sql}", params)
        total = cursor.fetchone()[0]

        # Fetch page (with filters)
        cursor.execute(f"""
            SELECT * FROM records{where_sql}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])
        
        calls_list = []
        for row in cursor:
            call = {
                "call_id": row["call_id"],
                "session_id": row["session_id"],
                "timestamp": row["timestamp"],
                "timestamp_iso": row["timestamp_iso"],
                "provider": row["provider"],
                "model": row["model"],
                "api": row["api"],
                "stop_reason": row["stop_reason"],
                "tokens_input": row["tokens_input"],
                "tokens_output": row["tokens_output"],
                "tokens_cache_read": row["tokens_cache_read"],
                "tokens_cache_write": row["tokens_cache_write"],
                "tokens_total": row["tokens_total"],
                "cache_hit_ratio": round(row["cache_hit_ratio"], 4),
                "cost_total": round(row["cost_total"], 6),
                "has_thinking": bool(row["has_thinking"]),
                "has_tool_calls": bool(row["has_tool_calls"]),
                "tool_names": json.loads(row["tool_names"] or "[]"),
                "content_length": row["content_length"],
                "is_error": bool(row["is_error"]),
            }
            calls_list.append(call)
    
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
        "calls": calls_list,
    }


@app.get("/api/sessions")
async def sessions_list():
    """
    Session list with per-session aggregates.
    """
    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                session_id,
                COUNT(*) as calls,
                SUM(cost_total) as cost,
                SUM(tokens_total) as tokens,
                MIN(timestamp) as first_call,
                MAX(timestamp) as last_call,
                SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END) as errors
            FROM records
            GROUP BY session_id
            ORDER BY last_call DESC
        """)
        
        sessions = []
        for row in cursor:
            sessions.append({
                "session_id": row[0],
                "calls": row[1],
                "cost": round(row[2] or 0, 6),
                "tokens": row[3] or 0,
                "first_call": row[4],
                "last_call": row[5],
                "errors": row[6],
            })
    
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str):
    """
    Single session detail with all calls.
    """
    records = reader.get_records({"session_id": session_id})
    
    # Convert to dicts
    calls_list = []
    for record in records:
        calls_list.append({
            "call_id": record.call_id,
            "timestamp": record.timestamp,
            "timestamp_iso": record.timestamp_iso,
            "provider": record.provider,
            "model": record.model,
            "tokens_total": record.tokens_total,
            "cost_total": round(record.cost_total, 6),
            "stop_reason": record.stop_reason,
            "is_error": record.is_error,
        })
    
    return {
        "session_id": session_id,
        "calls": calls_list,
        "total_calls": len(calls_list),
        "total_cost": round(sum(r.cost_total for r in records), 6),
        "total_tokens": sum(r.tokens_total for r in records),
    }


@app.get("/api/models")
async def models(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Per-model breakdown: calls, tokens, cost, error rate. Supports provider/model/time filters.
    """
    _reload_config_from_disk()
    where, params = _build_filter(provider, model)
    if start:
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(start)
    if end:
        where = (where + " AND " if where else " WHERE ") + "timestamp <= ?"
        params.append(end)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                model,
                COUNT(*) as calls,
                SUM(cost_total) as cost,
                SUM(tokens_total) as tokens,
                SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END) as errors,
                AVG(cache_hit_ratio) as avg_cache_hit
            FROM records{where}
            GROUP BY model
            ORDER BY calls DESC
        """, params)
        
        model_stats = {}
        for row in cursor:
            calls = row[1]
            error_rate = row[4] / calls if calls > 0 else 0
            model_stats[row[0]] = {
                "model": row[0],
                "calls": calls,
                "cost": round(row[2] or 0, 6),
                "tokens": row[3] or 0,
                "error_rate": round(error_rate, 4),
                "avg_cache_hit_ratio": round(row[5] or 0, 4),
            }

    configured_models = _configured_models()
    if model:
        if model in configured_models and model not in model_stats:
            model_stats[model] = {
                "model": model,
                "calls": 0,
                "cost": 0.0,
                "tokens": 0,
                "error_rate": 0.0,
                "avg_cache_hit_ratio": 0.0,
            }
    else:
        for mdl in configured_models:
            model_stats.setdefault(mdl, {
                "model": mdl,
                "calls": 0,
                "cost": 0.0,
                "tokens": 0,
                "error_rate": 0.0,
                "avg_cache_hit_ratio": 0.0,
            })

    models_list = sorted(model_stats.values(), key=lambda x: (-x["calls"], x["model"]))
    return {"models": models_list, "configured_models": configured_models}


@app.get("/api/tools")
async def tools(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Tool usage frequency breakdown. Supports provider/model/time filters.
    """
    where, params = _build_filter(provider, model)
    if start:
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(start)
    if end:
        where = (where + " AND " if where else " WHERE ") + "timestamp <= ?"
        params.append(end)
    # Add tool_names filter on top of other filters
    extra = " AND tool_names IS NOT NULL AND tool_names != '[]'" if where else " WHERE tool_names IS NOT NULL AND tool_names != '[]'"

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(f"SELECT tool_names FROM records{where}{extra}", params)
        
        tool_counts = {}
        for row in cursor:
            try:
                tools_list = json.loads(row[0])
                for tool in tools_list:
                    tool_counts[tool] = tool_counts.get(tool, 0) + 1
            except:
                pass
        
        # Sort by count descending
        tools_list = [
            {"tool": name, "count": count}
            for name, count in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)
        ]
    
    return {"tools": tools_list}


@app.get("/api/balance")
async def balance():
    """
    Provider balances + alert evaluation.
    Includes ledger, usage stats, and spending totals per provider.
    Also includes providers from DB that may not have balance config.
    """
    _reload_config_from_disk()
    balances = await balance_checker.check_balances(reader)
    snapshots = balance_poller.get_latest_snapshots()
    now_ms = int(_utc_now().timestamp() * 1000)
    stale_error_ms = int(CONFIG.get("scrape_error_stale_after_seconds", 900)) * 1000
    show_scrape_errors = bool(CONFIG.get("show_scrape_errors", False))

    snapshot_aliases = {
        "anthropic": ["anthropic"],
        "elevenlabs": ["elevenlabs"],
        "codex_cli": ["codex_cli", "openclaw"],
        "moonshot": ["moonshot"],
        "minimax": ["minimax", "mini_max", "minimaxai"],
    }
    for snapshot_provider, snapshot in snapshots.items():
        scrape_error = snapshot.get("error")
        ts = snapshot.get("timestamp")
        if scrape_error and ts and (now_ms - int(ts)) > stale_error_ms:
            scrape_error = None
        aliases = snapshot_aliases.get(snapshot_provider, [snapshot_provider])
        attached = False
        for alias in aliases:
            if alias in balances:
                balances[alias]["scrape_error"] = scrape_error if show_scrape_errors else None
                attached = True
        if not attached:
            balances.setdefault(snapshot_provider, {})["scrape_error"] = scrape_error if show_scrape_errors else None

    # Attach ledger history for any provider that has one
    balance_cfg = CONFIG.get("balance", {})
    for provider_name, provider_cfg in balance_cfg.items():
        if not isinstance(provider_cfg, dict):
            continue
        if provider_cfg.get("projects"):
            # Multi-project provider: projects already populated by checker
            # Build a combined ledger with project tags for the card header
            combined_ledger = []
            for proj_name, proj_cfg in provider_cfg["projects"].items():
                if not isinstance(proj_cfg, dict):
                    continue
                for entry in proj_cfg.get("ledger", []):
                    tagged = dict(entry)
                    tagged["project"] = proj_name
                    combined_ledger.append(tagged)
            balances.setdefault(provider_name, {})["ledger"] = combined_ledger
            # Aggregate personal_invested across projects
            total_personal = 0.0
            for proj_name, proj_cfg in provider_cfg["projects"].items():
                if isinstance(proj_cfg, dict):
                    total_personal += sum(
                        e.get("amount", 0) for e in proj_cfg.get("ledger", [])
                        if not e.get("is_voucher")
                    )
            balances[provider_name]["personal_invested"] = round(total_personal, 2)
        elif "ledger" in provider_cfg:
            balances.setdefault(provider_name, {})["ledger"] = provider_cfg["ledger"]
            personal = sum(
                e.get("amount", 0) for e in provider_cfg["ledger"]
                if not e.get("is_voucher")
            )
            balances[provider_name]["personal_invested"] = round(personal, 2)

    # Add usage stats (calls, cost) for ALL providers in the DB
    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT provider, COUNT(*) as calls, COALESCE(SUM(cost_total), 0) as cost,
                   COALESCE(SUM(tokens_total), 0) as tokens
            FROM records GROUP BY provider ORDER BY calls DESC
        """)
        for row in cursor:
            prov = row[0]
            balances.setdefault(prov, {})
            balances[prov]["usage_calls"] = row[1]
            # If checker applied reconciliation override, keep that as canonical usage cost.
            if "cumulative_cost" in balances.get(prov, {}):
                balances[prov]["usage_cost"] = round(balances[prov]["cumulative_cost"], 6)
            else:
                balances[prov]["usage_cost"] = round(row[2], 6)
            balances[prov]["usage_tokens"] = row[3]

        # For multi-project providers, also add per-project usage stats
        for provider_name, provider_cfg in balance_cfg.items():
            if not isinstance(provider_cfg, dict) or not provider_cfg.get("projects"):
                continue
            for proj_name, proj_cfg in provider_cfg["projects"].items():
                if not isinstance(proj_cfg, dict):
                    continue
                proj_models = proj_cfg.get("models", [])
                if not proj_models:
                    continue
                placeholders = ",".join("?" * len(proj_models))
                cursor.execute(f"""
                    SELECT COUNT(*), COALESCE(SUM(cost_total), 0), COALESCE(SUM(tokens_total), 0)
                    FROM records WHERE model IN ({placeholders})
                """, proj_models)
                row = cursor.fetchone()
                proj_data = balances.get(provider_name, {}).get("projects", {}).get(proj_name, {})
                proj_data["usage_calls"] = row[0]
                proj_data["usage_cost"] = round(row[1], 6)
                proj_data["usage_tokens"] = row[2]

    # Balance Tracker should only include configured balance providers.
    configured_balance_providers = set((CONFIG.get("balance") or {}).keys())
    balances = {k: v for k, v in balances.items() if k in configured_balance_providers}

    # Prefer latest scraped balances when available to keep cards live without API keys.
    # Skip providers whose poller returns "api_only" (no browser scraping).
    for provider_name, provider_data in balances.items():
        # Preserve live billing API results; do not overwrite with browser snapshots.
        if provider_data.get("balance_source") == "api":
            # Keep project-level values aligned with API balance for single-project providers.
            projects = provider_data.get("projects")
            if isinstance(projects, dict) and len(projects) == 1:
                only_project = next(iter(projects.values()))
                if isinstance(only_project, dict) and provider_data.get("remaining") is not None:
                    only_project["remaining"] = round(float(provider_data["remaining"]), 2)
                    if only_project.get("total_deposits") is not None:
                        only_project["cumulative_cost"] = round(
                            float(only_project["total_deposits"]) - float(only_project["remaining"]),
                            6,
                        )
                        only_project["usage_cost"] = only_project["cumulative_cost"]
                    crit = float(provider_data.get("critical_threshold", 2.0))
                    warn = float(provider_data.get("warn_threshold", 10.0))
                    rem = float(only_project["remaining"])
                    if rem <= crit:
                        only_project["status"] = "critical"
                    elif rem <= warn:
                        only_project["status"] = "warn"
                    else:
                        only_project["status"] = "ok"
            continue
        snapshot = snapshots.get(provider_name)
        if not snapshot:
            continue
        snap_source = snapshot.get("balance_source")
        if snap_source == "api_only":
            continue
        snap_balance = snapshot.get("balance_amount")
        if snap_balance is None:
            continue
        provider_data["remaining"] = round(float(snap_balance), 2)
        provider_data.pop("api_note", None)
        provider_data["scrape_error"] = None
        if provider_data.get("total_deposits") is not None:
            provider_data["cumulative_cost"] = round(
                float(provider_data["total_deposits"]) - float(provider_data["remaining"]),
                6,
            )
            provider_data["usage_cost"] = provider_data["cumulative_cost"]
        # Keep selected project card aligned for single-project providers.
        projects = provider_data.get("projects")
        if isinstance(projects, dict) and len(projects) == 1:
            only_project = next(iter(projects.values()))
            if isinstance(only_project, dict):
                only_project["remaining"] = provider_data["remaining"]
                if only_project.get("total_deposits") is not None:
                    only_project["cumulative_cost"] = round(
                        float(only_project["total_deposits"]) - float(only_project["remaining"]),
                        6,
                    )
                    only_project["usage_cost"] = only_project["cumulative_cost"]

    # Final consistency pass: for Moonshot API mode, keep single-project
    # display values aligned with provider-level API remaining.
    moonshot_data = balances.get("moonshot")
    if isinstance(moonshot_data, dict) and moonshot_data.get("balance_source") == "api":
        projects = moonshot_data.get("projects")
        if isinstance(projects, dict) and len(projects) == 1 and moonshot_data.get("remaining") is not None:
            only_project = next(iter(projects.values()))
            if isinstance(only_project, dict):
                only_project["remaining"] = round(float(moonshot_data["remaining"]), 2)
                if only_project.get("total_deposits") is not None:
                    only_project["cumulative_cost"] = round(
                        float(only_project["total_deposits"]) - float(only_project["remaining"]),
                        6,
                    )
                    only_project["usage_cost"] = only_project["cumulative_cost"]
                crit = float(moonshot_data.get("critical_threshold", 2.0))
                warn = float(moonshot_data.get("warn_threshold", 10.0))
                rem = float(only_project["remaining"])
                if rem <= crit:
                    only_project["status"] = "critical"
                elif rem <= warn:
                    only_project["status"] = "warn"
                else:
                    only_project["status"] = "ok"

    return balances


def _get_claude_code_tier_display() -> str:
    """Get Claude Code tier display name from env var or config."""
    # Check env var first (CLAUDE_CODE_TIER=pro|max_100|max_200)
    tier = os.environ.get('CLAUDE_CODE_TIER', '').lower().strip()
    if not tier:
        # Fall back to config
        tier = CONFIG.get('claude_code_tier', 'pro').lower().strip()
    
    tier_map = {
        'pro': 'Claude Code Pro ($20/mo)',
        'max_100': 'Claude Code Max ($100/mo)',
        'max_200': 'Claude Code Max ($200/mo)',
    }
    return tier_map.get(tier, 'Claude Code Pro ($20/mo)')


@app.get("/api/resources")
async def resources():
    """Resource availability cards with usage windows matching actual provider limits."""
    _reload_config_from_disk()
    # Balance-based providers are excluded from Resource Availability.
    # This section only shows providers with RPM/TPM rate limits (window-based).
    # MiniMax and Moonshot are pay-per-use balance providers, not rate-limited resources.
    _BALANCE_ONLY_PROVIDERS = {"minimax", "moonshot"}
    provider_defs = {
        "anthropic": {
            "display_name": _get_claude_code_tier_display(),
            "usage_provider_aliases": ["anthropic"],
            "window_limits": {"one_week": 20.00},
            "unit": "usd",
        },
        "elevenlabs": {
            "display_name": "ElevenLabs",
            "usage_provider_aliases": ["elevenlabs"],
            "window_limits": {"one_month": 100000},
            "unit": "credits",
        },
        "codex_cli": {
            "display_name": "Codex CLI",
            "usage_provider_aliases": ["openclaw", "codex_cli"],
            "window_limits": {"five_hour": 400, "one_week": 1050},
            "unit": "credits",
            "pricing_notes": {
                "minimum_purchase": "1,000 credits per purchase",
                "messages_per_purchase": "250-1,300 CLI or Extension messages",
                "cloud_tasks_per_purchase": "40-250 cloud tasks",
            },
        },
    }

    now_ms = int(_utc_now().timestamp() * 1000)
    five_hour_start = now_ms - (5 * 60 * 60 * 1000)  # 5 hours ago
    one_week_start = now_ms - (7 * 24 * 60 * 60 * 1000)
    show_scrape_errors = bool(CONFIG.get("show_scrape_errors", False))
    snapshots = balance_poller.get_latest_snapshots(list(provider_defs.keys()))

    def _find_last_good_window_scrape(provider_key: str) -> Optional[Dict[str, Any]]:
        """Return the latest historical scrape with usable window usage fields."""
        usage_field_map = {
            "anthropic": ("claude_usage", ("plan_usage_pct", "weekly_pct")),
            "codex_cli": ("codex_usage", ("five_hour_remaining_pct", "weekly_remaining_pct")),
        }
        usage_info = usage_field_map.get(provider_key)
        if not usage_info:
            return None

        payload_key, required_fields = usage_info
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT raw_response
                FROM resource_snapshots
                WHERE provider = ?
                ORDER BY timestamp DESC
                LIMIT 50
                """,
                (provider_key,),
            )
            rows = cursor.fetchall()

        candidates: List[Dict[str, Any]] = []
        # Skip the latest row because it matches `snapshot` and may contain nulls.
        for idx, (raw_response,) in enumerate(rows):
            if idx == 0 or not raw_response:
                continue
            try:
                parsed = json.loads(raw_response)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            payload = parsed.get("payload")
            if not isinstance(payload, dict):
                continue
            usage_payload = payload.get(payload_key)
            if isinstance(usage_payload, dict):
                candidates.append(usage_payload)

        for usage_payload in candidates:
            if all(usage_payload.get(field) is not None for field in required_fields):
                return usage_payload
        for usage_payload in candidates:
            if any(usage_payload.get(field) is not None for field in required_fields):
                return usage_payload
        return None

    def _window_usage(provider_aliases: List[str], since_ms: int) -> Dict[str, Any]:
        placeholders = ",".join("?" * len(provider_aliases))
        params: List[Any] = [*provider_aliases, since_ms]
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    COUNT(*),
                    COALESCE(SUM(cost_total), 0.0),
                    MIN(timestamp),
                    MAX(timestamp)
                FROM records
                WHERE provider IN ({placeholders})
                  AND timestamp >= ?
                """,
                params,
            )
            row = cursor.fetchone() or (0, 0.0, None, None)
            return {
                "calls": int(row[0] or 0),
                "cost": float(row[1] or 0.0),
                "first_ts": int(row[2]) if row[2] is not None else None,
                "last_ts": int(row[3]) if row[3] is not None else None,
            }

    def _to_provider_units(unit: str, usage_cost: float, usage_calls: int, provider_key: str) -> float:
        if unit == "usd":
            return round(usage_cost, 2)
        if provider_key == "codex_cli":
            # Placeholder conversion until direct Codex CLI usage ingestion is available.
            estimated = max(int(round(usage_calls * 4)), int(round(usage_cost * 100)))
            return float(estimated)
        if provider_key == "elevenlabs":
            # Placeholder conversion: 1 USD ~= 100 credits for dashboard visibility.
            return float(int(round(usage_cost * 100)))
        return 0.0

    def _pct(used: float, limit: float) -> float:
        if not limit or limit <= 0:
            return 0.0
        return round(min(100.0, (used / limit) * 100.0), 1)

    def _window_reset_meta(window_usage: Dict[str, Any], window_ms: int) -> Dict[str, Optional[int]]:
        first_ts = window_usage.get("first_ts")
        if not first_ts:
            return {"reset_at": None, "remaining_seconds": None}
        reset_at = int(first_ts) + int(window_ms)
        remaining_seconds = max(0, (reset_at - now_ms) // 1000)
        return {"reset_at": reset_at, "remaining_seconds": int(remaining_seconds)}

    response_providers: Dict[str, Dict[str, Any]] = {}
    usage_allow_fallback = bool(CONFIG.get("usage_allow_fallback", False))
    allow_stale_window_scrape = bool(CONFIG.get("allow_stale_window_scrape", False))
    for provider_key, provider_def in provider_defs.items():
        usage_5h = _window_usage(provider_def["usage_provider_aliases"], five_hour_start)
        usage_1w = _window_usage(provider_def["usage_provider_aliases"], one_week_start)
        used_5h = _to_provider_units(provider_def["unit"], usage_5h["cost"], usage_5h["calls"], provider_key)
        used_1w = _to_provider_units(provider_def["unit"], usage_1w["cost"], usage_1w["calls"], provider_key)

        limit_5h = float(provider_def.get("window_limits", {}).get("five_hour", 0))
        limit_1w = float(provider_def.get("window_limits", {}).get("one_week", 0))
        snapshot = snapshots.get(provider_key, {})
        ts = snapshot.get("timestamp")
        age_seconds = max(0, (now_ms - int(ts)) // 1000) if ts else None

        is_window_based = provider_key in {"anthropic", "codex_cli"}
        extra_value = round(used_1w, 2)
        display_name = provider_def["display_name"]
        tier = snapshot.get("tier")
        total_credits = snapshot.get("total_credits")
        if provider_key == "elevenlabs":
            # ElevenLabs card is balance-based: display current credits only.
            bal = snapshot.get("balance_amount")
            if bal is not None:
                extra_value = round(float(bal), 2)
            if tier:
                display_name = f"ElevenLabs ({tier})"
        elif provider_key == "anthropic":
            extra_limit = float(CONFIG.get("claude_extra_usage_limit", 25.0))
            # Display remaining extra usage budget over configured limit.
            extra_value = round(max(extra_limit - used_1w, 0.0), 2)
            total_credits = extra_limit
            usage_payload = (snapshot.get("raw_payload") or {}).get("claude_usage") if snapshot else {}
            fallback = CONFIG.get("claude_usage_fallback", {}) or {}
            spend_used = usage_payload.get("spend_used") if isinstance(usage_payload, dict) else None
            spend_limit = usage_payload.get("spend_limit") if isinstance(usage_payload, dict) else None
            spend_reset_text = usage_payload.get("spend_reset_text") if isinstance(usage_payload, dict) else None
            extra_balance = usage_payload.get("extra_usage_balance") if isinstance(usage_payload, dict) else None
            if usage_allow_fallback and spend_used is None:
                spend_used = fallback.get("spend_used")
            if usage_allow_fallback and spend_limit is None:
                spend_limit = fallback.get("spend_limit", extra_limit)
            if usage_allow_fallback and spend_reset_text is None:
                spend_reset_text = fallback.get("spend_reset_text")
            if usage_allow_fallback and extra_balance is None:
                extra_balance = fallback.get("extra_usage_balance")
            if isinstance(extra_balance, (int, float)):
                extra_value = round(float(extra_balance), 2)
            if isinstance(spend_limit, (int, float)):
                total_credits = round(float(spend_limit), 2)
        elif provider_key == "codex_cli":
            extra_value = 0.0

        window_5h_meta = _window_reset_meta(usage_5h, 5 * 60 * 60 * 1000)
        window_1w_meta = _window_reset_meta(usage_1w, 7 * 24 * 60 * 60 * 1000)

        # Use scraped window percentages from CDP for Claude Code and Codex
        # instead of local telemetry (which doesn't have rate limit data).
        scraped_pct_5h = None
        scraped_pct_1w = None
        scraped_reset_5h = None
        scraped_reset_1w = None
        raw_payload = (snapshot.get("raw_payload") or {}) if snapshot else {}

        # Track data source for UI indicators
        data_source = "telemetry"  # default
        if snapshot:
            data_source = snapshot.get("balance_source", "telemetry")
        
        if provider_key == "anthropic":
            claude_usage = raw_payload.get("claude_usage") or {}
            if isinstance(claude_usage, dict):
                scraped_pct_5h = claude_usage.get("plan_usage_pct")
                scraped_pct_1w = claude_usage.get("weekly_pct")
                scraped_reset_5h = claude_usage.get("plan_usage_reset")
                scraped_reset_1w = claude_usage.get("weekly_reset")
                # Check if using fallback values
                if claude_usage.get("source") == "fallback" or claude_usage.get("fallback_last_updated"):
                    data_source = "fallback"
            # If current scrape returned nulls, look back for last good values
            if allow_stale_window_scrape and (scraped_pct_5h is None or scraped_pct_1w is None):
                last_good = _find_last_good_window_scrape(provider_key)
                if last_good:
                    if scraped_pct_5h is None:
                        scraped_pct_5h = last_good.get("plan_usage_pct")
                    if scraped_pct_1w is None:
                        scraped_pct_1w = last_good.get("weekly_pct")
                    if scraped_reset_5h is None:
                        scraped_reset_5h = last_good.get("plan_usage_reset")
                    if scraped_reset_1w is None:
                        scraped_reset_1w = last_good.get("weekly_reset")
        elif provider_key == "codex_cli":
            codex_usage = raw_payload.get("codex_usage") or {}
            if isinstance(codex_usage, dict):
                # Codex reports "remaining" pct; convert to "used" pct
                rem_5h = codex_usage.get("five_hour_remaining_pct")
                rem_1w = codex_usage.get("weekly_remaining_pct")
                if rem_5h is not None:
                    scraped_pct_5h = 100 - int(rem_5h)
                if rem_1w is not None:
                    scraped_pct_1w = 100 - int(rem_1w)
                scraped_reset_1w = codex_usage.get("weekly_reset")
                # Check if using fallback values
                if codex_usage.get("source") == "fallback" or codex_usage.get("fallback_last_updated"):
                    data_source = "fallback"
            # If current scrape returned nulls, look back for last good values
            if allow_stale_window_scrape and (scraped_pct_5h is None or scraped_pct_1w is None):
                last_good = _find_last_good_window_scrape(provider_key)
                if last_good:
                    if scraped_pct_5h is None and last_good.get("five_hour_remaining_pct") is not None:
                        scraped_pct_5h = 100 - int(last_good["five_hour_remaining_pct"])
                    if scraped_pct_1w is None and last_good.get("weekly_remaining_pct") is not None:
                        scraped_pct_1w = 100 - int(last_good["weekly_remaining_pct"])
                    if scraped_reset_1w is None:
                        scraped_reset_1w = last_good.get("weekly_reset")

        # Prefer scraped percentages over local telemetry computation
        pct_5h = float(scraped_pct_5h) if scraped_pct_5h is not None else _pct(used_5h, limit_5h)
        pct_1w = float(scraped_pct_1w) if scraped_pct_1w is not None else _pct(used_1w, limit_1w)

        # Use scraped reset text if available; clear telemetry-based meta
        # for providers where local telemetry can't compute resets.
        if provider_key in {"anthropic", "codex_cli"}:
            window_5h_meta = {"reset_at": None, "remaining_seconds": None, "reset_text": scraped_reset_5h}
            window_1w_meta = {"reset_at": None, "remaining_seconds": None, "reset_text": scraped_reset_1w}

        response_providers[provider_key] = {
            "provider": provider_key,
            "display_name": display_name,
            "status": _resource_status(snapshot) if snapshot else "warn",
            "age_seconds": age_seconds,
            "windows": {
                "five_hour": {
                    "label": "5 hr",
                    "used": round(used_5h, 2),
                    "limit": round(limit_5h, 2),
                    "percent": round(pct_5h, 1),
                    "reset_at": window_5h_meta["reset_at"],
                    "remaining_seconds": window_5h_meta["remaining_seconds"],
                    "reset_text": window_5h_meta.get("reset_text"),
                },
                "one_week": {
                    "label": "1 wk",
                    "used": round(used_1w, 2),
                    "limit": round(limit_1w, 2),
                    "percent": round(pct_1w, 1),
                    "reset_at": window_1w_meta["reset_at"],
                    "remaining_seconds": window_1w_meta["remaining_seconds"],
                    "reset_text": window_1w_meta.get("reset_text"),
                },
            } if is_window_based else None,
            "extra_usage": {
                "unit": provider_def["unit"],
                "value": extra_value,
                "total": round(float(total_credits), 2) if total_credits is not None else None,
                "label": "Extra Usage Balance" if provider_key == "anthropic" else None,
            },
            "spend_limit": {
                "used": round(float(spend_used), 2) if provider_key == "anthropic" and isinstance(spend_used, (int, float)) else None,
                "limit": round(float(total_credits), 2) if provider_key == "anthropic" and total_credits is not None else None,
                "reset_text": spend_reset_text if provider_key == "anthropic" else None,
            } if provider_key == "anthropic" else None,
            "tier": tier,
            "total_credits": round(float(total_credits), 2) if total_credits is not None else None,
            "pricing_notes": provider_def.get("pricing_notes"),
            "error": (snapshot.get("error") if (snapshot and show_scrape_errors) else None),
            "data_source": data_source,
        }

    # Enforce exclusion of balance-only providers (e.g. minimax, moonshot)
    response_providers = {k: v for k, v in response_providers.items() if k not in _BALANCE_ONLY_PROVIDERS}
    return {"providers": response_providers}


@app.post("/api/resources/poll")
async def resources_poll_now():
    """Trigger immediate balance polling and return latest results."""
    results = await _run_resource_poll()
    resource_data = await resources()
    return {"status": "ok", "polled": len(results), "providers": resource_data.get("providers", {})}


@app.post("/api/balance/topup")
async def balance_topup(
    provider: str = Body(...),
    amount: float = Body(...),
    note: str = Body(""),
    project: Optional[str] = Body(None),
    top_up_date: Optional[str] = Body(None, alias="topup_date"),
):
    """
    Add a top-up entry to a provider's ledger in config.yaml.
    For multi-project providers, specify which project to add to.
    Optionally specify top_up_date for backdating entries (YYYY-MM-DD format).
    """
    global CONFIG

    balance_cfg = CONFIG.get("balance", {})
    provider_cfg = balance_cfg.get(provider)
    provider_key = str(provider).strip().lower()

    if provider_cfg is None:
        return {"error": f"Unknown provider: {provider}", "status": 400}
    if provider_key == "moonshot":
        return {
            "error": "Manual Moonshot ledger edits are disabled. Moonshot must be tracked from live API balance only.",
            "status": 400,
        }

    if amount <= 0:
        return {"error": "Amount must be positive", "status": 400}

    # Determine which ledger to append to
    if provider_cfg.get("projects"):
        if not project:
            proj_names = list(provider_cfg["projects"].keys())
            return {"error": f"Specify a project: {', '.join(proj_names)}", "status": 400}
        proj_cfg = provider_cfg["projects"].get(project)
        if not proj_cfg:
            return {"error": f"Unknown project '{project}' for {provider}", "status": 400}
        if "ledger" not in proj_cfg:
            proj_cfg["ledger"] = []
        target_ledger = proj_cfg["ledger"]
    elif "ledger" in provider_cfg:
        target_ledger = provider_cfg["ledger"]
    else:
        return {"error": f"Provider '{provider}' has no ledger configured", "status": 400}

    # Use provided date or default to today
    entry_date = top_up_date if top_up_date else _utc_now().strftime("%Y-%m-%d")
    
    # Create new ledger entry
    entry = {
        "date": entry_date,
        "amount": round(amount, 2),
    }
    if note:
        entry["note"] = note

    # Append to in-memory config
    target_ledger.append(entry)

    # Write back to config.yaml
    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        target_ledger.pop()
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}

    logger.info(f"Added top-up for {provider}{f'/{project}' if project else ''}: ${amount:.2f} ({note})")

    updated = await balance_checker.check_balances(reader)
    return {"status": "ok", "entry": entry, "balances": updated}


@app.post("/api/balance/topup/delete")
async def balance_topup_delete(
    provider: str = Body(...),
    index: int = Body(..., description="0-based index of the ledger entry to remove"),
    project: Optional[str] = Body(None),
):
    """
    Remove a ledger entry by index from a provider's ledger in config.yaml.
    For multi-project providers, specify which project's ledger.
    """
    global CONFIG

    balance_cfg = CONFIG.get("balance", {})
    provider_cfg = balance_cfg.get(provider)
    provider_key = str(provider).strip().lower()

    if provider_cfg is None:
        return {"error": f"Unknown provider: {provider}", "status": 400}
    if provider_key == "moonshot":
        return {
            "error": "Manual Moonshot ledger edits are disabled. Moonshot must be tracked from live API balance only.",
            "status": 400,
        }

    # Determine which ledger to operate on
    if provider_cfg.get("projects"):
        if not project:
            return {"error": "Specify which project's ledger entry to remove", "status": 400}
        proj_cfg = provider_cfg["projects"].get(project)
        if not proj_cfg:
            return {"error": f"Unknown project '{project}' for {provider}", "status": 400}
        ledger = proj_cfg.get("ledger")
    else:
        ledger = provider_cfg.get("ledger")

    if not ledger:
        return {"error": f"No ledger entries found", "status": 400}

    if index < 0 or index >= len(ledger):
        return {"error": f"Invalid index {index} (ledger has {len(ledger)} entries)", "status": 400}

    removed = ledger.pop(index)

    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        ledger.insert(index, removed)
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}

    logger.info(f"Removed ledger entry #{index} from {provider}{f'/{project}' if project else ''}: ${removed.get('amount', 0):.2f}")

    updated = await balance_checker.check_balances(reader)
    return {"status": "ok", "removed": removed, "balances": updated}


@app.get("/api/evals")
async def evals():
    """
    Run 8 evaluations, return scores/grades.
    """
    results = evaluator.evaluate(reader)
    return {"evals": [asdict(r) for r in results]}


@app.get("/api/cost/daily")
async def cost_daily(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=3650, description="Rolling day window if start/end are omitted"),
    row_limit: int = Query(5000, ge=100, le=50000, description="Max grouped rows returned"),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Daily cost breakdown by provider/model. Supports provider/model/time filters.
    """
    _reload_config_from_disk()
    where, params = _build_filter(provider, model)
    if start:
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(start)
    if end:
        where = (where + " AND " if where else " WHERE ") + "timestamp <= ?"
        params.append(end)
    if start is None and end is None:
        rolling_start_ms = int((_utc_now() - timedelta(days=days)).timestamp() * 1000)
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(rolling_start_ms)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                DATE(timestamp / 1000, 'unixepoch') as day,
                provider,
                model,
                COUNT(*) as calls,
                SUM(cost_total) as cost
            FROM records{where}
            GROUP BY day, provider, model
            ORDER BY day DESC, cost DESC
            LIMIT ?
        """, params + [row_limit])
        
        daily = []
        for row in cursor:
            daily.append({
                "day": row[0],
                "provider": row[1],
                "model": row[2],
                "calls": row[3],
                "cost": round(row[4] or 0, 6),
            })
    
    return {
        "daily": daily,
        "window_days": days if (start is None and end is None) else None,
        "row_limit": row_limit,
        "truncated": len(daily) >= row_limit,
        "configured_providers": _configured_providers(),
        "configured_models": _configured_models(),
    }


@app.get("/api/today-cost")
async def today_cost():
    """
    Total cost across all providers since midnight in America/Los_Angeles.
    """
    now_pacific = datetime.now(PACIFIC_TZ)
    midnight_pacific = now_pacific.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_ms = int(midnight_pacific.timestamp() * 1000)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COALESCE(SUM(cost_total), 0.0)
            FROM records
            WHERE timestamp >= ?
            """,
            (midnight_ms,),
        )
        total_cost = float(cursor.fetchone()[0] or 0.0)

    return {
        "today_cost": round(total_cost, 6),
        "start_of_day": midnight_pacific.isoformat(),
        "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
    }


@app.get("/api/cost/projection")
async def cost_projection(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    timeframe: str = Query(
        "month",
        description="Projection timeframe: this_day, week, partial_week, rolling_week, month, rolling_month, all",
    ),
    start: Optional[int] = Query(None, description="Start timestamp (epoch ms)"),
    end: Optional[int] = Query(None, description="End timestamp (epoch ms)"),
):
    """
    Cost projection with timeframe-aware scaling. Supports provider/model/time filters.
    """
    valid_timeframes = {"this_day", "week", "partial_week", "rolling_week", "month", "rolling_month", "all"}
    tf = (timeframe or "month").strip().lower()
    if tf not in valid_timeframes:
        return JSONResponse(
            {"error": f"Invalid timeframe '{timeframe}'. Expected one of: {sorted(valid_timeframes)}"},
            status_code=400,
        )

    now_utc = _utc_now()
    now_ms = int(now_utc.timestamp() * 1000)
    now_pacific = datetime.now(PACIFIC_TZ)
    day_start = now_pacific.replace(hour=0, minute=0, second=0, microsecond=0)

    effective_start = start
    effective_end = end if end is not None else now_ms

    if tf == "this_day":
        effective_start = int(day_start.timestamp() * 1000)
        effective_end = now_ms
    elif tf == "all":
        effective_start = None
        effective_end = now_ms

    if effective_start is None:
        if tf in {"week", "partial_week", "rolling_week"}:
            effective_start = int((now_utc - timedelta(days=7)).timestamp() * 1000)
        elif tf == "month":
            month_start = now_pacific.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            effective_start = int(month_start.timestamp() * 1000)
        elif tf == "rolling_month":
            effective_start = int((now_utc - timedelta(days=30)).timestamp() * 1000)
        elif tf == "all":
            effective_start = None

    where, params = _build_filter(provider, model)
    if effective_start is not None:
        where = (where + " AND " if where else " WHERE ") + "timestamp >= ?"
        params.append(effective_start)
    if effective_end is not None:
        where = (where + " AND " if where else " WHERE ") + "timestamp <= ?"
        params.append(effective_end)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                COALESCE(SUM(cost_total), 0.0) as total_cost,
                MIN(timestamp) as first_ts,
                MAX(timestamp) as last_ts
            FROM records{where}
        """, params)
        row = cursor.fetchone() or (0.0, None, None)
        total_cost = float(row[0] or 0.0)

    ms_per_day = 24 * 60 * 60 * 1000

    if tf == "this_day":
        start_of_day = now_pacific.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed_hours = max((now_pacific - start_of_day).total_seconds() / 3600.0, 1.0 / 60.0)
        based_on_days = elapsed_hours / 24.0
        avg_daily = total_cost / based_on_days if based_on_days > 0 else 0.0
        projected_value = avg_daily
    elif tf in {"week", "partial_week", "rolling_week"}:
        if effective_start is not None and effective_end is not None:
            window_days = max((effective_end - effective_start) / ms_per_day, 1.0 / 24.0)
        else:
            window_days = 7.0
        based_on_days = window_days
        avg_daily = total_cost / based_on_days if based_on_days > 0 else 0.0
        projected_value = avg_daily * 7.0
    elif tf == "rolling_month":
        based_on_days = 30.0
        avg_daily = total_cost / based_on_days if based_on_days > 0 else 0.0
        projected_value = avg_daily * 30.0
    elif tf == "month":
        days_so_far = float(max(now_pacific.day, 1))
        days_in_month = float(calendar.monthrange(now_pacific.year, now_pacific.month)[1])
        based_on_days = days_so_far
        avg_daily = total_cost / based_on_days if based_on_days > 0 else 0.0
        projected_value = avg_daily * days_in_month
    else:  # all
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            where_min, params_min = _build_filter(provider, model)
            cursor.execute(f"SELECT MIN(timestamp), MAX(timestamp) FROM records{where_min}", params_min)
            min_max = cursor.fetchone() or (None, None)
        if min_max[0] is not None and min_max[1] is not None:
            total_days = max((int(min_max[1]) - int(min_max[0])) / ms_per_day, 1.0)
        else:
            total_days = 1.0
        based_on_days = total_days
        avg_daily = total_cost / based_on_days if based_on_days > 0 else 0.0
        projected_value = avg_daily * 30.0
    
    return {
        "timeframe": tf,
        "days_of_data": round(based_on_days, 4),
        "avg_daily_cost": round(avg_daily, 6),
        "projected_monthly_cost": round(projected_value, 6),
    }


@app.get("/api/config")
async def config():
    """
    Model costs and settings (no secrets).
    """
    return {
        "model_costs": CONFIG.get("model_costs", {}),
        "eval_thresholds": CONFIG.get("eval_thresholds", {}),
    }


@app.get("/api/ratelimits")
async def ratelimits():
    """
    Return rate limit configuration and current usage metrics.
    Rate limits are per model family (e.g. claude-haiku includes all haiku variants).
    Usage is aggregated across all models in each family.
    """
    _reload_config_from_disk()
    rate_cfg = CONFIG.get("rate_limits", {})
    configured_providers = _configured_providers()

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()
        now_ms = int(time.time() * 1000)
        one_min_ago = now_ms - 60_000
        five_min_ago = now_ms - 300_000
        one_hour_ago = now_ms - 3_600_000

        # Per-model usage in last 1 minute
        cursor.execute("""
            SELECT model, COUNT(*) as calls, COALESCE(SUM(tokens_total), 0) as tokens,
                   COALESCE(SUM(tokens_input + tokens_cache_read + tokens_cache_write), 0) as input_tokens,
                   COALESCE(SUM(tokens_output), 0) as output_tokens
            FROM records WHERE timestamp >= ?
            GROUP BY model
        """, (one_min_ago,))
        raw_1m = {}
        for row in cursor:
            raw_1m[row[0]] = {"rpm": row[1], "tpm": row[2], "input_tpm": row[3], "output_tpm": row[4]}

        cursor.execute("""
            SELECT provider, COUNT(*) as calls, COALESCE(SUM(tokens_total), 0) as tokens,
                   COALESCE(SUM(tokens_input + tokens_cache_read + tokens_cache_write), 0) as input_tokens,
                   COALESCE(SUM(tokens_output), 0) as output_tokens
            FROM records WHERE timestamp >= ?
            GROUP BY provider
        """, (one_min_ago,))
        provider_1m = {}
        for row in cursor:
            provider_1m[row[0]] = {"rpm": row[1], "tpm": row[2], "input_tpm": row[3], "output_tpm": row[4]}

        # Per-model usage in last 5 minutes (for peak-minute calculation)
        cursor.execute("""
            SELECT model, COUNT(*) as calls, COALESCE(SUM(tokens_total), 0) as tokens,
                   COALESCE(SUM(tokens_input + tokens_cache_read + tokens_cache_write), 0) as input_tokens,
                   COALESCE(SUM(tokens_output), 0) as output_tokens
            FROM records WHERE timestamp >= ?
            GROUP BY model
        """, (five_min_ago,))
        raw_5m = {}
        for row in cursor:
            raw_5m[row[0]] = {"rpm": row[1], "tpm": row[2], "input_tpm": row[3], "output_tpm": row[4]}

        cursor.execute("""
            SELECT provider, COUNT(*) as calls, COALESCE(SUM(tokens_total), 0) as tokens,
                   COALESCE(SUM(tokens_input + tokens_cache_read + tokens_cache_write), 0) as input_tokens,
                   COALESCE(SUM(tokens_output), 0) as output_tokens
            FROM records WHERE timestamp >= ?
            GROUP BY provider
        """, (five_min_ago,))
        provider_5m = {}
        for row in cursor:
            provider_5m[row[0]] = {"rpm": row[1], "tpm": row[2], "input_tpm": row[3], "output_tpm": row[4]}

        # Per-model usage in last 1 hour
        cursor.execute("""
            SELECT model, COUNT(*) as calls, COALESCE(SUM(tokens_total), 0) as tokens,
                   COALESCE(SUM(tokens_input + tokens_cache_read + tokens_cache_write), 0) as input_tokens,
                   COALESCE(SUM(tokens_output), 0) as output_tokens
            FROM records WHERE timestamp >= ?
            GROUP BY model
        """, (one_hour_ago,))
        raw_1h = {}
        for row in cursor:
            raw_1h[row[0]] = {"rph": row[1], "tph": row[2]}

        cursor.execute("""
            SELECT provider, COUNT(*) as calls, COALESCE(SUM(tokens_total), 0) as tokens
            FROM records WHERE timestamp >= ?
            GROUP BY provider
        """, (one_hour_ago,))
        provider_1h = {}
        for row in cursor:
            provider_1h[row[0]] = {"rph": row[1], "tph": row[2]}

        # Recent rate limit errors (last 1 hour) — stop_reason='error' with 0 tokens
        cursor.execute("""
            SELECT model, MAX(timestamp) as last_error, COUNT(*) as error_count
            FROM records
            WHERE timestamp >= ? AND stop_reason = 'error' AND tokens_total = 0
            GROUP BY model
        """, (one_hour_ago,))
        rate_limit_errors = {}
        for row in cursor:
            rate_limit_errors[row[0]] = {"last_error": row[1], "error_count": row[2]}

        cursor.execute("""
            SELECT provider, MAX(timestamp) as last_error, COUNT(*) as error_count
            FROM records
            WHERE timestamp >= ? AND stop_reason = 'error' AND tokens_total = 0
            GROUP BY provider
        """, (one_hour_ago,))
        provider_errors = {}
        for row in cursor:
            provider_errors[row[0]] = {"last_error": row[1], "error_count": row[2]}

        # All known models
        cursor.execute("SELECT DISTINCT model FROM records ORDER BY model")
        all_models = [row[0] for row in cursor.fetchall()]

    # Aggregate usage per family
    families = {}
    for family_name, family_cfg in rate_cfg.items():
        if not isinstance(family_cfg, dict):
            continue
        member_models = family_cfg.get("models", [])
        meta_keys = {"models", "auto_detected"}
        limits = {k: v for k, v in family_cfg.items() if k not in meta_keys}

        # Sum usage across all member models
        agg_1m = {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}
        agg_5m = {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}
        agg_1h = {"rph": 0, "tph": 0}
        agg_errors = {"last_error": 0, "error_count": 0}
        for mdl in member_models:
            if mdl in raw_1m:
                agg_1m["rpm"] += raw_1m[mdl]["rpm"]
                agg_1m["tpm"] += raw_1m[mdl]["tpm"]
                agg_1m["input_tpm"] += raw_1m[mdl].get("input_tpm", 0)
                agg_1m["output_tpm"] += raw_1m[mdl].get("output_tpm", 0)
            if mdl in raw_5m:
                agg_5m["rpm"] += raw_5m[mdl]["rpm"]
                agg_5m["tpm"] += raw_5m[mdl]["tpm"]
                agg_5m["input_tpm"] += raw_5m[mdl].get("input_tpm", 0)
                agg_5m["output_tpm"] += raw_5m[mdl].get("output_tpm", 0)
            if mdl in raw_1h:
                agg_1h["rph"] += raw_1h[mdl]["rph"]
                agg_1h["tph"] += raw_1h[mdl]["tph"]
            if mdl in rate_limit_errors:
                err = rate_limit_errors[mdl]
                agg_errors["error_count"] += err["error_count"]
                agg_errors["last_error"] = max(agg_errors["last_error"], err["last_error"])

        families[family_name] = {
            "limits": limits,
            "models": member_models,
            "auto_detected": bool(family_cfg.get("auto_detected")),
            "usage_1m": agg_1m,
            "usage_5m": agg_5m,
            "usage_1h": agg_1h,
            "rate_limit_errors": agg_errors if agg_errors["error_count"] > 0 else None,
        }

    providers = {}
    for provider_name in configured_providers:
        provider_cfg = rate_cfg.get(provider_name, {})
        if not isinstance(provider_cfg, dict):
            provider_cfg = {}
        provider_models = provider_cfg.get("models", [])
        limits = {k: v for k, v in provider_cfg.items() if k not in {"models", "auto_detected"}}
        providers[provider_name] = {
            "limits": limits,
            "models": provider_models,
            "auto_detected": bool(provider_cfg.get("auto_detected")),
            "usage_1m": provider_1m.get(provider_name, {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}),
            "usage_5m": provider_5m.get(provider_name, {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0}),
            "usage_1h": provider_1h.get(provider_name, {"rph": 0, "tph": 0}),
            "rate_limit_errors": provider_errors.get(provider_name),
        }

    return {
        "families": families,
        "providers": providers,
        "configured_providers": configured_providers,
        "all_models": all_models,
    }


@app.post("/api/ratelimits/probe")
async def ratelimits_probe():
    """Re-probe provider APIs to auto-detect current rate limits."""
    await apply_auto_detected_limits()
    return {"status": "ok", "message": "Rate limits re-probed from provider APIs"}


@app.post("/api/ratelimits")
async def ratelimits_update(
    family: str = Body(..., description="Family name (e.g. claude-haiku)"),
    rpm: Optional[int] = Body(None, description="Requests per minute limit"),
    tpm: Optional[int] = Body(None, description="Tokens per minute limit"),
    rph: Optional[int] = Body(None, description="Requests per hour limit"),
    tph: Optional[int] = Body(None, description="Tokens per hour limit"),
    models: Optional[List[str]] = Body(None, description="Model IDs in this family"),
):
    """
    Set or update rate limits for a model family in config.yaml.
    Pass null/0 to remove a specific limit.
    """
    global CONFIG

    if "rate_limits" not in CONFIG:
        CONFIG["rate_limits"] = {}

    family_cfg = CONFIG["rate_limits"].setdefault(family, {})

    for key, val in [("rpm", rpm), ("tpm", tpm), ("rph", rph), ("tph", tph)]:
        if val is not None:
            if val > 0:
                family_cfg[key] = val
            else:
                family_cfg.pop(key, None)

    if models is not None:
        family_cfg["models"] = models

    # Mark as manually set so auto-detection won't overwrite
    family_cfg.pop("auto_detected", None)

    # Clean up empty entries (but keep if it still has models list)
    if not any(k for k in family_cfg if k not in ("models", "auto_detected")):
        if not family_cfg.get("models"):
            CONFIG["rate_limits"].pop(family, None)

    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}

    logger.info(f"Updated rate limits for family {family}: rpm={rpm}, tpm={tpm}, rph={rph}, tph={tph}")
    return {"status": "ok", "family": family, "limits": CONFIG["rate_limits"].get(family, {})}


# ============================================================================
# SPEND LIMITS
# ============================================================================

def _compute_spend_entry(member_models, daily_limit, monthly_limit, reset_date_str, daily_cost_by_model):
    """Compute spend usage for a set of models with given limits."""
    now = _utc_now()
    month_start_ms = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    # Period cost since reset_date or start of month
    period_start_ms = month_start_ms
    if reset_date_str:
        try:
            reset_dt = datetime.strptime(str(reset_date_str), "%Y-%m-%d")
            while reset_dt > now:
                if reset_dt.month == 1:
                    reset_dt = reset_dt.replace(year=reset_dt.year - 1, month=12)
                else:
                    reset_dt = reset_dt.replace(month=reset_dt.month - 1)
            period_start_ms = int(reset_dt.timestamp() * 1000)
        except Exception:
            pass

    period_cost = 0.0
    if member_models:
        placeholders = ",".join("?" * len(member_models))
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COALESCE(SUM(cost_total), 0)
                FROM records WHERE timestamp >= ? AND model IN ({placeholders})
            """, [period_start_ms] + member_models)
            period_cost = cursor.fetchone()[0] or 0.0

    daily_cost = sum(daily_cost_by_model.get(m, 0.0) for m in member_models)

    # Next reset date
    next_reset = None
    if reset_date_str:
        try:
            next_dt = datetime.strptime(str(reset_date_str), "%Y-%m-%d")
            while next_dt <= now:
                if next_dt.month == 12:
                    next_dt = next_dt.replace(year=next_dt.year + 1, month=1)
                else:
                    next_dt = next_dt.replace(month=next_dt.month + 1)
            next_reset = next_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return {
        "daily_limit": daily_limit,
        "monthly_limit": monthly_limit,
        "reset_date": reset_date_str,
        "next_reset": next_reset,
        "models": member_models,
        "usage_daily": round(daily_cost, 6),
        "usage_period": round(period_cost, 6),
    }


@app.get("/api/spendlimits")
async def spendlimits():
    """
    Return spend limit configuration and current usage.
    Supports both flat provider-level limits and per-project limits.
    """
    spend_cfg = CONFIG.get("spend_limits", {})

    # Pre-fetch daily costs per model
    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()
        today_start_ms = int(_utc_now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        cursor.execute("""
            SELECT model, COALESCE(SUM(cost_total), 0) as cost
            FROM records WHERE timestamp >= ?
            GROUP BY model
        """, (today_start_ms,))
        daily_cost_by_model = {row[0]: row[1] for row in cursor.fetchall()}

    providers = {}
    for provider_name, prov_cfg in spend_cfg.items():
        if not isinstance(prov_cfg, dict):
            continue

        if prov_cfg.get("projects"):
            # Multi-project: emit one entry per project keyed as "provider/project"
            for proj_name, proj_cfg in prov_cfg["projects"].items():
                if not isinstance(proj_cfg, dict):
                    continue
                key = f"{provider_name}/{proj_name}"
                entry = _compute_spend_entry(
                    proj_cfg.get("models", []),
                    proj_cfg.get("daily"),
                    proj_cfg.get("monthly"),
                    proj_cfg.get("reset_date"),
                    daily_cost_by_model,
                )
                entry["provider"] = provider_name
                entry["project"] = proj_name
                providers[key] = entry
        else:
            # Flat provider-level limit
            providers[provider_name] = _compute_spend_entry(
                prov_cfg.get("models", []),
                prov_cfg.get("daily"),
                prov_cfg.get("monthly"),
                prov_cfg.get("reset_date"),
                daily_cost_by_model,
            )

    return {"providers": providers}


@app.post("/api/spendlimits")
async def spendlimits_update(
    provider: str = Body(..., description="Provider name (e.g. anthropic)"),
    project: Optional[str] = Body(None, description="Project name for multi-project providers"),
    daily: Optional[float] = Body(None, description="Daily cost cap"),
    monthly: Optional[float] = Body(None, description="Monthly cost cap"),
    reset_date: Optional[str] = Body(None, description="Monthly reset date (YYYY-MM-DD)"),
    models: Optional[List[str]] = Body(None, description="Model IDs covered"),
):
    """
    Set or update spend limits for a provider (or project) in config.yaml.
    Pass null/0 to remove a specific limit.
    """
    global CONFIG

    if "spend_limits" not in CONFIG:
        CONFIG["spend_limits"] = {}

    # Determine target config node
    if project:
        prov_cfg = CONFIG["spend_limits"].setdefault(provider, {})
        if "projects" not in prov_cfg:
            prov_cfg["projects"] = {}
        target = prov_cfg["projects"].setdefault(project, {})
    else:
        target = CONFIG["spend_limits"].setdefault(provider, {})

    for key, val in [("daily", daily), ("monthly", monthly)]:
        if val is not None:
            if val > 0:
                target[key] = val
            else:
                target.pop(key, None)

    if reset_date is not None:
        if reset_date:
            target["reset_date"] = reset_date
        else:
            target.pop("reset_date", None)

    if models is not None:
        target["models"] = models

    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}

    label = f"{provider}/{project}" if project else provider
    logger.info(f"Updated spend limits for {label}: daily={daily}, monthly={monthly}, reset_date={reset_date}")
    return {"status": "ok", "provider": provider, "project": project}


@app.post("/api/refresh")
async def refresh():
    """
    Force full refresh: scan telemetry + poll resources + recompute balances.
    """
    logger.info("Manual refresh triggered")
    _reload_config_from_disk()
    reader.scan()
    poll_results = await _run_resource_poll()
    balances = await balance_checker.check_balances(reader)
    return {
        "status": "refreshed",
        "refresh_version": "2026-03-05-codex-moonfix",
        "parse_errors": len(reader.parse_errors),
        "polled": len(poll_results),
        "polled_providers": [p.get("provider") for p in poll_results if isinstance(p, dict)],
        "balance_providers": sorted(list(balances.keys())),
    }


# ============================================================================
# CLAUDE CODE & CODEX CLI FALLBACK MANAGEMENT
# ============================================================================

@app.get("/api/fallback/usage")
async def get_fallback_usage():
    """
    Get current fallback values for Claude Code and Codex CLI usage.
    These are used when browser CDP scraping is unavailable.
    """
    _reload_config_from_disk()
    return {
        "build_tag": "moonshot-ledger-lock-2026-03-05",
        "claude_code": CONFIG.get("claude_usage_fallback", {}),
        "codex_cli": CONFIG.get("codex_usage_fallback", {}),
    }


@app.post("/api/fallback/usage/claude")
async def update_claude_fallback(
    spend_used: Optional[float] = Body(None),
    spend_limit: Optional[float] = Body(None),
    spend_reset_text: Optional[str] = Body(None),
    extra_usage_balance: Optional[float] = Body(None),
    plan_usage_pct: Optional[int] = Body(None),
    weekly_pct: Optional[int] = Body(None),
):
    """
    Update fallback values for Claude Code usage.
    These values are used when browser CDP scraping fails or is disabled.
    """
    global CONFIG
    
    if "claude_usage_fallback" not in CONFIG:
        CONFIG["claude_usage_fallback"] = {}
    
    fallback = CONFIG["claude_usage_fallback"]
    
    if spend_used is not None:
        fallback["spend_used"] = round(spend_used, 2)
    if spend_limit is not None:
        fallback["spend_limit"] = round(spend_limit, 2)
    if spend_reset_text is not None:
        fallback["spend_reset_text"] = spend_reset_text
    if extra_usage_balance is not None:
        fallback["extra_usage_balance"] = round(extra_usage_balance, 2)
    if plan_usage_pct is not None:
        fallback["plan_usage_pct"] = plan_usage_pct
    if weekly_pct is not None:
        fallback["weekly_pct"] = weekly_pct
    
    # Update timestamp
    from datetime import datetime
    fallback["last_updated"] = datetime.now().isoformat()
    fallback["source"] = "manual_update"
    
    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}
    
    logger.info(f"Updated Claude Code fallback values: spend_used={spend_used}, extra_balance={extra_usage_balance}")
    return {"status": "ok", "fallback": fallback}


@app.post("/api/fallback/usage/codex")
async def update_codex_fallback(
    five_hour_remaining_pct: Optional[int] = Body(None),
    weekly_remaining_pct: Optional[int] = Body(None),
    weekly_reset: Optional[str] = Body(None),
):
    """
    Update fallback values for Codex CLI usage.
    These values are used when browser CDP scraping fails or is disabled.
    """
    global CONFIG
    
    if "codex_usage_fallback" not in CONFIG:
        CONFIG["codex_usage_fallback"] = {}
    
    fallback = CONFIG["codex_usage_fallback"]
    
    if five_hour_remaining_pct is not None:
        fallback["five_hour_remaining_pct"] = five_hour_remaining_pct
    if weekly_remaining_pct is not None:
        fallback["weekly_remaining_pct"] = weekly_remaining_pct
    if weekly_reset is not None:
        fallback["weekly_reset"] = weekly_reset
    
    # Update timestamp
    from datetime import datetime
    fallback["last_updated"] = datetime.now().isoformat()
    fallback["source"] = "manual_update"
    
    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}
    
    logger.info(f"Updated Codex CLI fallback values: 5h_remaining={five_hour_remaining_pct}, weekly_remaining={weekly_remaining_pct}")
    return {"status": "ok", "fallback": fallback}


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle unexpected exceptions gracefully, including MemoryError."""
    try:
        logger.error(f"Unhandled exception: {exc}")
        # For MemoryError, return a minimal response to avoid cascading failures
        if isinstance(exc, MemoryError):
            import gc
            gc.collect()
            return JSONResponse(
                status_code=503,
                content={"error": "Server out of memory", "status": 503},
            )
        return JSONResponse(
            status_code=500,
            content={
                "error": str(exc),
                "status": 500,
            },
        )
    except Exception:
        # Last-resort fallback: if even JSONResponse fails, return raw bytes
        from starlette.responses import Response
        return Response(
            content=b'{"error":"internal error","status":500}',
            status_code=500,
            media_type="application/json",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=CONFIG["server"]["host"],
        port=CONFIG["server"]["port"],
        log_level="info",
    )
