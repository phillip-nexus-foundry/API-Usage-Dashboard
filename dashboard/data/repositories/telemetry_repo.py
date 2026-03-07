"""
SQLAlchemy implementation of TelemetryRepository.
Handles CRUD for the records table with filtering, aggregation, and time-series.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, case, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from dashboard.data.models import Record
from dashboard.data.database import Database

logger = logging.getLogger(__name__)


def _model_to_provider(model: str) -> str:
    """Infer provider from model name."""
    model_lower = model.lower()
    if "claude" in model_lower:
        return "anthropic"
    if "kimi" in model_lower or "moonshot" in model_lower:
        return "moonshot"
    if "minimax" in model_lower or "m2.5" in model_lower:
        return "minimax"
    return "unknown"


class SQLAlchemyTelemetryRepo:
    """Concrete telemetry repository backed by SQLAlchemy."""

    def __init__(self, db: Database):
        self._db = db

    def insert_records(self, records: Sequence[dict]) -> int:
        """Bulk insert records, skipping duplicates by call_id."""
        if not records:
            return 0

        inserted = 0
        with self._db.session() as session:
            for rec in records:
                # Ensure tool_names is JSON string
                if isinstance(rec.get("tool_names"), list):
                    rec["tool_names"] = json.dumps(rec["tool_names"])
                try:
                    obj = Record(**rec)
                    session.add(obj)
                    session.flush()
                    inserted += 1
                except IntegrityError:
                    session.rollback()
                    # Duplicate call_id, skip
                    continue
        return inserted

    def get_records(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 1000,
        offset: int = 0,
        sort_desc: bool = True,
    ) -> list[dict]:
        with self._db.session() as session:
            q = session.query(Record)
            if provider:
                q = q.filter(Record.provider == provider)
            if model:
                q = q.filter(Record.model == model)
            if session_id:
                q = q.filter(Record.session_id == session_id)
            if since:
                since_ms = int(since.timestamp() * 1000)
                q = q.filter(Record.timestamp >= since_ms)
            if until:
                until_ms = int(until.timestamp() * 1000)
                q = q.filter(Record.timestamp <= until_ms)

            if sort_desc:
                q = q.order_by(Record.timestamp.desc())
            else:
                q = q.order_by(Record.timestamp.asc())

            q = q.offset(offset).limit(limit)
            return [self._to_dict(r) for r in q.all()]

    def get_summary(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> dict:
        with self._db.session() as session:
            q = session.query(
                Record.provider,
                Record.model,
                func.count(Record.id).label("calls"),
                func.sum(Record.cost_total).label("total_cost"),
                func.sum(Record.tokens_input).label("total_input"),
                func.sum(Record.tokens_output).label("total_output"),
                func.sum(Record.tokens_cache_read).label("total_cache_read"),
                func.sum(Record.tokens_cache_write).label("total_cache_write"),
                func.sum(Record.tokens_total).label("total_tokens"),
                func.sum(case((Record.is_error == True, 1), else_=0)).label("errors"),
                func.count(func.distinct(Record.session_id)).label("sessions"),
            )
            if since:
                q = q.filter(Record.timestamp >= int(since.timestamp() * 1000))
            if until:
                q = q.filter(Record.timestamp <= int(until.timestamp() * 1000))

            q = q.group_by(Record.provider, Record.model)
            rows = q.all()

            by_provider = {}
            by_model = {}
            totals = {"calls": 0, "cost": 0.0, "tokens": 0, "errors": 0, "sessions": set()}

            for row in rows:
                prov = row.provider or "unknown"
                mdl = row.model or "unknown"
                cost = float(row.total_cost or 0)
                calls = int(row.calls or 0)

                if prov not in by_provider:
                    by_provider[prov] = {"calls": 0, "cost": 0.0, "tokens": 0}
                by_provider[prov]["calls"] += calls
                by_provider[prov]["cost"] += cost
                by_provider[prov]["tokens"] += int(row.total_tokens or 0)

                by_model[mdl] = {
                    "calls": calls,
                    "cost": cost,
                    "input_tokens": int(row.total_input or 0),
                    "output_tokens": int(row.total_output or 0),
                    "cache_read_tokens": int(row.total_cache_read or 0),
                    "provider": prov,
                }

                totals["calls"] += calls
                totals["cost"] += cost
                totals["tokens"] += int(row.total_tokens or 0)
                totals["errors"] += int(row.errors or 0)

            # Get unique session count
            sq = session.query(func.count(func.distinct(Record.session_id)))
            if since:
                sq = sq.filter(Record.timestamp >= int(since.timestamp() * 1000))
            if until:
                sq = sq.filter(Record.timestamp <= int(until.timestamp() * 1000))
            totals["sessions"] = sq.scalar() or 0

            if totals["calls"] > 0:
                totals["error_rate"] = totals["errors"] / totals["calls"]
            else:
                totals["error_rate"] = 0.0

            return {
                "totals": totals,
                "by_provider": by_provider,
                "by_model": by_model,
            }

    def get_timeseries(
        self,
        interval: str = "hour",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        provider: Optional[str] = None,
    ) -> list[dict]:
        """Time-bucketed aggregations. Interval: minute, hour, day, week, month."""
        with self._db.session() as session:
            # Use database-appropriate date truncation
            if self._db.is_sqlite:
                bucket_expr = self._sqlite_bucket(interval)
            else:
                bucket_expr = self._pg_bucket(interval)

            q = session.query(
                bucket_expr.label("bucket"),
                Record.provider,
                func.count(Record.id).label("calls"),
                func.sum(Record.cost_total).label("cost"),
                func.sum(Record.tokens_input).label("input_tokens"),
                func.sum(Record.tokens_output).label("output_tokens"),
                func.sum(Record.tokens_total).label("total_tokens"),
            )

            if since:
                q = q.filter(Record.timestamp >= int(since.timestamp() * 1000))
            if until:
                q = q.filter(Record.timestamp <= int(until.timestamp() * 1000))
            if provider:
                q = q.filter(Record.provider == provider)

            q = q.group_by("bucket", Record.provider).order_by(text("bucket"))
            results = []
            for row in q.all():
                bucket_str = str(row.bucket)
                # Convert bucket to epoch ms for frontend compatibility
                try:
                    from datetime import datetime as _dt
                    if self._db.is_sqlite:
                        # SQLite buckets are formatted strings like "2026-03-01 18:00"
                        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:00", "%Y-%m-%d", "%Y-%m"):
                            try:
                                ts = int(_dt.strptime(bucket_str, fmt).replace(
                                    tzinfo=__import__('datetime').timezone.utc
                                ).timestamp() * 1000)
                                break
                            except ValueError:
                                continue
                        else:
                            ts = 0
                    else:
                        # PostgreSQL buckets are timestamp strings like "2026-03-01 18:00:00+00:00"
                        from datetime import datetime as _dt2, timezone as _tz
                        bucket_dt = _dt2.fromisoformat(bucket_str)
                        if bucket_dt.tzinfo is None:
                            bucket_dt = bucket_dt.replace(tzinfo=_tz.utc)
                        ts = int(bucket_dt.timestamp() * 1000)
                except Exception:
                    ts = 0
                input_tok = int(row.input_tokens or 0)
                output_tok = int(row.output_tokens or 0)
                total_tokens = int(row.total_tokens or 0) or (input_tok + output_tok)
                results.append({
                    "bucket": bucket_str,
                    "timestamp": ts,
                    "provider": row.provider,
                    "calls": int(row.calls or 0),
                    "cost": float(row.cost or 0),
                    "input_tokens": input_tok,
                    "output_tokens": output_tok,
                    "total_tokens": total_tokens,
                    "tokens": total_tokens,
                })
            return results

    def get_sessions(self) -> list[dict]:
        with self._db.session() as session:
            q = session.query(
                Record.session_id,
                func.count(Record.id).label("calls"),
                func.sum(Record.cost_total).label("cost"),
                func.min(Record.timestamp).label("first_ts"),
                func.max(Record.timestamp).label("last_ts"),
            ).group_by(Record.session_id).order_by(func.max(Record.timestamp).desc())

            return [
                {
                    "session_id": row.session_id,
                    "calls": int(row.calls),
                    "cost": float(row.cost or 0),
                    "first_timestamp": int(row.first_ts),
                    "last_timestamp": int(row.last_ts),
                }
                for row in q.all()
            ]

    def get_session_detail(self, session_id: str) -> list[dict]:
        with self._db.session() as session:
            q = session.query(Record).filter(
                Record.session_id == session_id
            ).order_by(Record.timestamp.asc())
            return [self._to_dict(r) for r in q.all()]

    def get_model_stats(self) -> list[dict]:
        with self._db.session() as session:
            q = session.query(
                Record.model,
                Record.provider,
                func.count(Record.id).label("calls"),
                func.sum(Record.cost_total).label("cost"),
                func.sum(Record.tokens_total).label("tokens"),
                func.avg(Record.cache_hit_ratio).label("avg_cache_hit_ratio"),
                func.avg(case((Record.is_error == True, 1), else_=0)).label("error_rate"),
            ).group_by(Record.model, Record.provider)
            return [
                {
                    "model": row.model,
                    "provider": row.provider,
                    "calls": int(row.calls),
                    "cost": float(row.cost or 0),
                    "tokens": int(row.tokens or 0),
                    "avg_cache_hit_ratio": float(row.avg_cache_hit_ratio or 0),
                    "error_rate": float(row.error_rate or 0),
                }
                for row in q.all()
            ]

    def get_tool_stats(self) -> list[dict]:
        """Aggregate tool usage from tool_names JSON arrays."""
        with self._db.session() as session:
            records = session.query(Record.tool_names).filter(
                Record.has_tool_calls == True
            ).all()

        tool_counts = {}
        for (names_json,) in records:
            try:
                names = json.loads(names_json) if names_json else []
            except (json.JSONDecodeError, TypeError):
                continue
            for name in names:
                tool_counts[name] = tool_counts.get(name, 0) + 1

        return [
            {"tool": name, "count": count}
            for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])
        ]

    def get_total_cost_by_provider(self, provider: str) -> float:
        with self._db.session() as session:
            result = session.query(
                func.sum(Record.cost_total)
            ).filter(Record.provider == provider).scalar()
            return float(result or 0.0)

    def get_total_cost_by_models(self, models: list[str]) -> float:
        """Sum cost_total for records matching any of the given model names."""
        if not models:
            return 0.0
        with self._db.session() as session:
            result = session.query(
                func.sum(Record.cost_total)
            ).filter(Record.model.in_(models)).scalar()
            return float(result or 0.0)

    def get_cost_since(self, provider: str, since_ms: int) -> float:
        """Sum cost_total for a provider since a given epoch-ms timestamp."""
        with self._db.session() as session:
            result = session.query(
                func.sum(Record.cost_total)
            ).filter(
                Record.provider == provider,
                Record.timestamp >= since_ms,
            ).scalar()
            return float(result or 0.0)

    def get_cost_since_by_models(self, models: list[str], since_ms: int) -> float:
        """Sum cost_total for a set of models since a given epoch-ms timestamp."""
        if not models:
            return 0.0
        with self._db.session() as session:
            result = session.query(
                func.sum(Record.cost_total)
            ).filter(
                Record.model.in_(models),
                Record.timestamp >= since_ms,
            ).scalar()
            return float(result or 0.0)

    def _to_dict(self, record: Record) -> dict:
        """Convert Record ORM object to dict."""
        return {
            "call_id": record.call_id,
            "session_id": record.session_id,
            "parent_id": record.parent_id,
            "timestamp": record.timestamp,
            "timestamp_iso": record.timestamp_iso,
            "api": record.api,
            "provider": record.provider,
            "model": record.model,
            "stop_reason": record.stop_reason,
            "tokens_input": record.tokens_input,
            "tokens_output": record.tokens_output,
            "tokens_cache_read": record.tokens_cache_read,
            "tokens_cache_write": record.tokens_cache_write,
            "tokens_total": record.tokens_total,
            "cache_hit_ratio": record.cache_hit_ratio,
            "cost_input": record.cost_input,
            "cost_output": record.cost_output,
            "cost_cache_read": record.cost_cache_read,
            "cost_cache_write": record.cost_cache_write,
            "cost_total": record.cost_total,
            "cost_api_reported": record.cost_api_reported,
            "has_thinking": record.has_thinking,
            "has_tool_calls": record.has_tool_calls,
            "tool_names": record.tool_names,
            "content_length": record.content_length,
            "is_error": record.is_error,
            "duration_ms": record.duration_ms,
            "source_file": record.source_file,
        }

    @staticmethod
    def _sqlite_bucket(interval: str):
        """SQLite date truncation using strftime on epoch ms."""
        formats = {
            "minute": "%Y-%m-%d %H:%M",
            "hour": "%Y-%m-%d %H:00",
            "day": "%Y-%m-%d",
            "week": "%Y-W%W",
            "month": "%Y-%m",
        }
        fmt = formats.get(interval, "%Y-%m-%d %H:00")
        return func.strftime(fmt, Record.timestamp / 1000.0, "unixepoch")

    @staticmethod
    def _pg_bucket(interval: str):
        """PostgreSQL date truncation using date_trunc."""
        pg_intervals = {
            "minute": "minute",
            "hour": "hour",
            "day": "day",
            "week": "week",
            "month": "month",
        }
        pg_int = pg_intervals.get(interval, "hour")
        return func.date_trunc(
            pg_int,
            func.to_timestamp(Record.timestamp / 1000.0)
        )
