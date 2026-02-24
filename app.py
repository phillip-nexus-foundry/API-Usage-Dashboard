"""
FastAPI application for API Usage Dashboard.
14 endpoints serving telemetry data and dashboard HTML.
"""
import os
import json
import yaml
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import asdict

from fastapi import FastAPI, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import aiosqlite
import sqlite3

from parsers.openclaw_reader import OpenClawReader
from balance.checker import BalanceChecker
from evals.evaluator import Evaluator


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
balance_checker = BalanceChecker(CONFIG)
evaluator = Evaluator(CONFIG)

# Create FastAPI app
app = FastAPI(title="API Usage Dashboard")

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


# ============================================================================
# INITIALIZATION
# ============================================================================

@app.on_event("startup")
async def startup():
    """Parse session files on startup."""
    logger.info("Scanning session files...")
    reader.scan()
    if reader.parse_errors:
        logger.warning(f"Encountered {len(reader.parse_errors)} parse errors during scan")
    logger.info("Startup complete")


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
):
    """
    Aggregate KPIs: total calls, cost, tokens, error rate, by-provider, by-model.
    Supports optional provider/model filters.
    """
    where, params = _build_filter(provider, model)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        # Aggregate stats (filtered)
        cursor.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(cost_total),0), COALESCE(SUM(tokens_total),0),
                   SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END),
                   COUNT(DISTINCT session_id)
            FROM records{where}
        """, params)
        row = cursor.fetchone()
        total_calls, total_cost, total_tokens, error_count, session_count = row
        error_rate = round(error_count / total_calls, 4) if total_calls > 0 else 0.0

        # By provider (filtered)
        cursor.execute(f"""
            SELECT provider, COUNT(*) as calls, SUM(cost_total) as cost, SUM(tokens_total) as tokens
            FROM records{where}
            GROUP BY provider
            ORDER BY calls DESC
        """, params)
        by_provider = [
            {"provider": r[0], "calls": r[1], "cost": round(r[2] or 0, 6), "tokens": r[3] or 0}
            for r in cursor.fetchall()
        ]

        # By model (filtered)
        cursor.execute(f"""
            SELECT model, COUNT(*) as calls, SUM(cost_total) as cost, SUM(tokens_total) as tokens
            FROM records{where}
            GROUP BY model
            ORDER BY calls DESC
        """, params)
        by_model = [
            {"model": r[0], "calls": r[1], "cost": round(r[2] or 0, 6), "tokens": r[3] or 0}
            for r in cursor.fetchall()
        ]

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_calls": total_calls,
        "total_cost": round(total_cost, 6),
        "total_tokens": total_tokens,
        "error_rate": error_rate,
        "error_count": error_count,
        "session_count": session_count,
        "parse_errors": len(reader.parse_errors),
        "by_provider": by_provider,
        "by_model": by_model,
    }


@app.get("/api/timeseries")
async def timeseries(
    interval: str = Query("hour"),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
):
    """
    Time-bucketed data for line/bar charts.
    Interval: minute, hour, day. Supports provider/model filters.
    """
    bucket_size = {
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }.get(interval, 3600)

    where, params = _build_filter(provider, model)

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        # Bucket by timestamp and aggregate
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
        for row in cursor.fetchall():
            data.append({
                "timestamp": int(row[0]),
                "calls": row[1],
                "cost": round(row[2] or 0, 6),
                "tokens": row[3] or 0,
                "errors": row[4],
            })
    
    return {"interval": interval, "data": data}


@app.get("/api/calls")
async def calls(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
):
    """
    Paginated individual call list with all 24 fields.
    Supports optional provider and model filters.
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
        for row in cursor.fetchall():
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
        for row in cursor.fetchall():
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
):
    """
    Per-model breakdown: calls, tokens, cost, error rate. Supports provider/model filters.
    """
    where, params = _build_filter(provider, model)

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
        
        models_list = []
        for row in cursor.fetchall():
            calls = row[1]
            error_rate = row[4] / calls if calls > 0 else 0
            models_list.append({
                "model": row[0],
                "calls": calls,
                "cost": round(row[2] or 0, 6),
                "tokens": row[3] or 0,
                "error_rate": round(error_rate, 4),
                "avg_cache_hit_ratio": round(row[5] or 0, 4),
            })
    
    return {"models": models_list}


@app.get("/api/tools")
async def tools(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
):
    """
    Tool usage frequency breakdown. Supports provider/model filters.
    """
    where, params = _build_filter(provider, model)
    # Add tool_names filter on top of provider/model
    extra = " AND tool_names IS NOT NULL AND tool_names != '[]'" if where else " WHERE tool_names IS NOT NULL AND tool_names != '[]'"

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(f"SELECT tool_names FROM records{where}{extra}", params)
        
        tool_counts = {}
        for row in cursor.fetchall():
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
    Includes ledger history for providers that use ledger tracking.
    """
    balances = await balance_checker.check_balances(reader)

    # Attach ledger history for any provider that has one
    balance_cfg = CONFIG.get("balance", {})
    for provider_name, provider_cfg in balance_cfg.items():
        if isinstance(provider_cfg, dict) and provider_cfg.get("ledger"):
            balances.setdefault(provider_name, {})["ledger"] = provider_cfg["ledger"]

    return balances


@app.post("/api/balance/topup")
async def balance_topup(
    provider: str = Body(...),
    amount: float = Body(...),
    note: str = Body(""),
):
    """
    Add a top-up entry to a provider's ledger in config.yaml.
    Only works for providers that use ledger-based tracking (e.g. Anthropic).
    """
    global CONFIG

    balance_cfg = CONFIG.get("balance", {})
    provider_cfg = balance_cfg.get(provider)

    if provider_cfg is None:
        return {"error": f"Unknown provider: {provider}", "status": 400}

    if "ledger" not in provider_cfg:
        return {"error": f"Provider '{provider}' uses API-based balance (no manual ledger)", "status": 400}

    if amount <= 0:
        return {"error": "Amount must be positive", "status": 400}

    # Create new ledger entry
    entry = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "amount": round(amount, 2),
    }
    if note:
        entry["note"] = note

    # Append to in-memory config
    provider_cfg["ledger"].append(entry)

    # Write back to config.yaml
    try:
        with open(config_path, "w") as f:
            yaml.dump(CONFIG, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        # Roll back the in-memory change
        provider_cfg["ledger"].pop()
        logger.error(f"Failed to write config.yaml: {e}")
        return {"error": f"Failed to save: {e}", "status": 500}

    logger.info(f"Added top-up for {provider}: ${amount:.2f} ({note})")

    # Return updated balance
    updated = await balance_checker.check_balances(reader)
    # Attach ledger for the updated provider
    updated.setdefault(provider, {})["ledger"] = provider_cfg.get("ledger", [])
    return {"status": "ok", "entry": entry, "balances": updated}


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
):
    """
    Daily cost breakdown by provider/model. Supports provider/model filters.
    """
    where, params = _build_filter(provider, model)

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
        """, params)
        
        daily = []
        for row in cursor.fetchall():
            daily.append({
                "day": row[0],
                "provider": row[1],
                "model": row[2],
                "calls": row[3],
                "cost": round(row[4] or 0, 6),
            })
    
    return {"daily": daily}


@app.get("/api/cost/projection")
async def cost_projection(
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
):
    """
    Monthly projection from 7-day trailing average. Supports provider/model filters.
    """
    where, params = _build_filter(provider, model)
    # Add the 7-day window filter
    time_filter = " AND timestamp > (SELECT MAX(timestamp) FROM records) - 7 * 24 * 3600 * 1000"
    if where:
        full_where = where + time_filter
    else:
        full_where = " WHERE timestamp > (SELECT MAX(timestamp) FROM records) - 7 * 24 * 3600 * 1000"

    with sqlite3.connect(reader.db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                DATE(timestamp / 1000, 'unixepoch') as day,
                SUM(cost_total) as cost
            FROM records{full_where}
            GROUP BY day
        """, params)
        
        daily_costs = [row[1] for row in cursor.fetchall()]
        
        if daily_costs:
            avg_daily = sum(daily_costs) / len(daily_costs)
            projected_monthly = avg_daily * 30
        else:
            projected_monthly = 0.0
    
    return {
        "days_of_data": len(daily_costs),
        "avg_daily_cost": round(sum(daily_costs) / len(daily_costs), 6) if daily_costs else 0.0,
        "projected_monthly_cost": round(projected_monthly, 6),
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


@app.post("/api/refresh")
async def refresh():
    """
    Force full re-scan of session files.
    """
    logger.info("Manual refresh triggered")
    reader.scan()
    return {"status": "refreshed", "parse_errors": len(reader.parse_errors)}


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error(f"Unhandled exception: {exc}")
    return {
        "error": str(exc),
        "status": 500,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=CONFIG["server"]["host"],
        port=CONFIG["server"]["port"],
        log_level="info",
    )
