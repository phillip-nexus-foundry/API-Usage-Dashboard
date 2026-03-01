"""
OpenClaw session JSONL file parser.
Reads .jsonl and .jsonl.reset.* files, extracts telemetry, and stores in SQLite.
"""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import asdict
import logging

from .telemetry_schema import (
    TelemetryRecord,
    compute_cost_breakdown,
    is_anthropic_model,
    anthropic_billable_cache_write_delta,
    canonicalize_model_for_cost,
    COST_LOGIC_VERSION,
)


logger = logging.getLogger(__name__)


class ParseError:
    """Represents a parsing error encountered during scan."""
    def __init__(self, filepath: str, line_num: int, error: str):
        self.filepath = filepath
        self.line_num = line_num
        self.error = error
    
    def __repr__(self):
        return f"ParseError({self.filepath}:{self.line_num}: {self.error})"


class OpenClawReader:
    """
    Parses OpenClaw session JSONL files and stores records in SQLite.
    Handles incremental updates via mtime tracking.
    """
    
    def __init__(self, db_path: str = "dashboard.db", sessions_dir: Optional[str] = None):
        """
        Initialize reader.
        
        Args:
            db_path: Path to SQLite database file
            sessions_dir: Sessions directory (default: OpenClaw standard location)
        """
        self.db_path = db_path
        self.sessions_dir = sessions_dir or "C:/Users/AI-Agents/.openclaw/agents/main/sessions"
        self.parse_errors: List[ParseError] = []
        self._init_db()
    
    def _init_db(self):
        """Create database tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # records table: all 24 TelemetryRecord fields
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS records (
                    call_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    parent_id TEXT,
                    timestamp INTEGER NOT NULL,
                    timestamp_iso TEXT NOT NULL,
                    api TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    stop_reason TEXT NOT NULL,
                    tokens_input INTEGER NOT NULL,
                    tokens_output INTEGER NOT NULL,
                    tokens_cache_read INTEGER NOT NULL,
                    tokens_cache_write INTEGER NOT NULL,
                    tokens_total INTEGER NOT NULL,
                    cache_hit_ratio REAL NOT NULL,
                    cost_input REAL NOT NULL,
                    cost_output REAL NOT NULL,
                    cost_cache_read REAL NOT NULL,
                    cost_cache_write REAL NOT NULL,
                    cost_total REAL NOT NULL,
                    has_thinking INTEGER NOT NULL,
                    has_tool_calls INTEGER NOT NULL,
                    tool_names TEXT,
                    content_length INTEGER NOT NULL,
                    is_error INTEGER NOT NULL
                )
            """)
            cursor.execute("PRAGMA table_info(records)")
            record_cols = {row[1] for row in cursor.fetchall()}
            if "source_file" not in record_cols:
                cursor.execute("ALTER TABLE records ADD COLUMN source_file TEXT")
            
            # Create indexes
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_session_id ON records(session_id)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_timestamp ON records(timestamp)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_provider ON records(provider)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_model ON records(model)""")
            
            # file_index table: tracks mtime for incremental updates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_index (
                    filepath TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    record_count INTEGER NOT NULL
                )
            """)
            cursor.execute("PRAGMA table_info(file_index)")
            index_cols = {row[1] for row in cursor.fetchall()}
            if "parser_version" not in index_cols:
                cursor.execute("ALTER TABLE file_index ADD COLUMN parser_version TEXT")

            # resource_snapshots table: provider balance/rate-limit snapshots
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resource_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    balance_amount REAL,
                    balance_currency TEXT,
                    balance_source TEXT,
                    tier TEXT,
                    rpm_limit INTEGER,
                    rpm_used INTEGER,
                    rpm_remaining INTEGER,
                    computed_cost REAL,
                    drift_amount REAL,
                    drift_percentage REAL,
                    raw_response TEXT
                )
            """)
            cursor.execute("PRAGMA table_info(resource_snapshots)")
            snapshot_cols = {row[1] for row in cursor.fetchall()}
            snapshot_column_defs = {
                "provider": "TEXT NOT NULL DEFAULT ''",
                "snapshot_type": "TEXT NOT NULL DEFAULT ''",
                "timestamp": "INTEGER NOT NULL DEFAULT 0",
                "balance_amount": "REAL",
                "balance_currency": "TEXT",
                "balance_source": "TEXT",
                "tier": "TEXT",
                "rpm_limit": "INTEGER",
                "rpm_used": "INTEGER",
                "rpm_remaining": "INTEGER",
                "computed_cost": "REAL",
                "drift_amount": "REAL",
                "drift_percentage": "REAL",
                "raw_response": "TEXT",
            }
            for col, ddl in snapshot_column_defs.items():
                if col not in snapshot_cols:
                    cursor.execute(f"ALTER TABLE resource_snapshots ADD COLUMN {col} {ddl}")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_resource_snapshots_provider_time
                ON resource_snapshots(provider, timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_resource_snapshots_type_time
                ON resource_snapshots(snapshot_type, timestamp)
            """)
            
            conn.commit()
    
    def scan(self):
        """
        Scan sessions directory, parse changed files, update database.
        Handles file deletion (removes records for deleted files).
        """
        self.parse_errors = []
        sessions_path = Path(self.sessions_dir)
        
        if not sessions_path.exists():
            logger.warning(f"Sessions directory does not exist: {self.sessions_dir}")
            return
        
        # Get all JSONL files (including .jsonl.reset.* and .jsonl.deleted.*)
        jsonl_files = (list(sessions_path.glob("*.jsonl")) + list(sessions_path.glob("*.jsonl.reset.*")) + list(sessions_path.glob("*.jsonl.deleted.*")))
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Track files we see in this scan
            current_files = {str(f.absolute()): f for f in jsonl_files}
            
            # Get files we previously tracked
            cursor.execute("SELECT filepath FROM file_index")
            tracked_files = {row[0] for row in cursor.fetchall()}
            
            # Delete records for files that no longer exist
            for old_file in tracked_files - set(current_files.keys()):
                logger.info(f"Deleting records for removed file: {old_file}")
                cursor.execute("DELETE FROM records WHERE source_file = ?", (old_file,))
                cursor.execute("DELETE FROM file_index WHERE filepath = ?", (old_file,))
            
            # Process current files
            for filepath_str, filepath in current_files.items():
                try:
                    mtime = filepath.stat().st_mtime
                    
                    # Check if file was modified since last scan
                    cursor.execute(
                        "SELECT mtime, record_count, parser_version FROM file_index WHERE filepath = ?",
                        (filepath_str,),
                    )
                    existing = cursor.fetchone()
                    
                    if existing and existing[0] == mtime:
                        # File unchanged AND parser logic unchanged, skip
                        if len(existing) >= 3 and existing[2] == COST_LOGIC_VERSION:
                            continue
                    
                    # File is new or modified, re-parse
                    logger.info(f"Parsing {filepath}")
                    records = self._parse_file(filepath)

                    # Remove prior rows from this source file so removed lines do not linger.
                    cursor.execute("DELETE FROM records WHERE source_file = ?", (filepath_str,))
                    
                    # Records use INSERT OR REPLACE with call_id as PRIMARY KEY,
                    # so re-parsing a file naturally updates existing records.
                    # We do NOT delete by session_id here because .jsonl and
                    # .jsonl.reset.* files can share the same session_id.
                    
                    # Insert new records
                    for record in records:
                        self._insert_record(cursor, record, filepath_str)
                    
                    # Update file_index
                    cursor.execute(
                        "INSERT OR REPLACE INTO file_index (filepath, mtime, record_count, parser_version) VALUES (?, ?, ?, ?)",
                        (filepath_str, mtime, len(records), COST_LOGIC_VERSION)
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to process file {filepath}: {e}")
                    error = ParseError(str(filepath), 0, str(e))
                    self.parse_errors.append(error)

            # Cleanup legacy rows inserted before source_file tracking.
            # After parser_version invalidation runs once, valid rows are reinserted
            # with source_file populated, so NULL rows are stale/orphaned.
            cursor.execute("DELETE FROM records WHERE source_file IS NULL")
            
            conn.commit()
    
    def _parse_file(self, filepath: Path) -> List[TelemetryRecord]:
        """
        Parse a single JSONL file.
        Returns list of TelemetryRecord objects.
        Logs and skips malformed lines.
        """
        records = []
        session_id = None
        # Track last seen raw cacheWrite per (session_id, model) within this file parse.
        # OpenClaw can emit cumulative cacheWrite counters for Anthropic models.
        cache_write_state: Dict[tuple, int] = {}
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    line = line.strip()
                    if not line:
                        continue
                    
                    obj = json.loads(line)
                    
                    # Line 0: session header
                    if obj.get("type") == "session":
                        session_id = obj.get("id")
                        continue
                    
                    # Message line: extract usage data from assistant messages
                    if obj.get("type") == "message":
                        message = obj.get("message", {})
                        if message.get("role") == "assistant" and message.get("usage"):
                            record = self._message_to_record(session_id, obj, filepath.stem, cache_write_state)
                            if record:
                                records.append(record)
                
                except json.JSONDecodeError as e:
                    error = ParseError(str(filepath), line_num, f"JSON decode error: {e}")
                    self.parse_errors.append(error)
                    logger.warning(f"{error}")
                except Exception as e:
                    error = ParseError(str(filepath), line_num, str(e))
                    self.parse_errors.append(error)
                    logger.warning(f"{error}")
        
        return records
    
    def _message_to_record(
        self,
        session_id: Optional[str],
        msg_obj: Dict[str, Any],
        filename: str,
        cache_write_state: Optional[Dict[tuple, int]] = None,
    ) -> Optional[TelemetryRecord]:
        """
        Convert a message object to a TelemetryRecord.
        Returns None if required fields are missing.
        """
        try:
            message = msg_obj.get("message", {})
            msg_id = msg_obj.get("id", "unknown")
            parent_id = msg_obj.get("parentId")
            
            # Extract usage
            usage = message.get("usage", {})
            tokens_input = usage.get("input", 0)
            tokens_output = usage.get("output", 0)
            tokens_cache_read = usage.get("cacheRead", 0)
            tokens_cache_write = usage.get("cacheWrite", 0)
            tokens_total = usage.get("totalTokens", 0)
            
            # Extract cost (OpenClaw has a bug where stored costs are inflated by ~1M)
            # Always compute cost from token counts for accuracy
            cost_data = usage.get("cost", {})
            stored_cost_total = cost_data.get("total", 0.0)
            
            # Extract content details (needed for cost calc and record)
            content = message.get("content", [])
            has_thinking = any(c.get("type") == "thinking" for c in content)
            has_tool_calls = any(c.get("type") == "toolCall" for c in content)
            tool_names = [c.get("name", "") for c in content if c.get("type") == "toolCall"]

            raw_model = message.get("model", "unknown")
            model = canonicalize_model_for_cost(raw_model)
            session_key = session_id or "unknown"
            billable_cache_write = tokens_cache_write

            # Anthropic cacheWrite in OpenClaw can be cumulative; charge only deltas.
            if cache_write_state is not None and is_anthropic_model(model):
                key = (session_key, model)
                prev_highwater = cache_write_state.get(key)
                billable_cache_write = anthropic_billable_cache_write_delta(prev_highwater, tokens_cache_write)
                if prev_highwater is None:
                    cache_write_state[key] = tokens_cache_write
                else:
                    cache_write_state[key] = max(prev_highwater, tokens_cache_write)

            # Compute cost independently from token counts (reliable)
            # Includes Moonshot web surcharges based on tool_names
            breakdown = compute_cost_breakdown(
                model=model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_cache_read=tokens_cache_read,
                tokens_cache_write_billable=billable_cache_write,
                tool_names=tool_names,
            )
            cost_input = breakdown["cost_input"]
            cost_output = breakdown["cost_output"]
            cost_cache_read = breakdown["cost_cache_read"]
            cost_cache_write = breakdown["cost_cache_write"]
            cost_total = breakdown["cost_total"]
            
            # Sanity check: if stored cost is reasonable (< $10 per call), log a warning
            if 0 < stored_cost_total < 10 and abs(stored_cost_total - cost_total) > 0.01:
                logger.debug(f"Cost mismatch: stored=${stored_cost_total:.4f}, computed=${cost_total:.4f}")
            
            # Cache hit ratio
            cache_denominator = tokens_cache_read + tokens_input
            cache_hit_ratio = tokens_cache_read / cache_denominator if cache_denominator > 0 else 0.0
            
            # Extract timestamp
            ts_ms = message.get("timestamp", 0)
            ts_iso = datetime.utcfromtimestamp(ts_ms / 1000).isoformat() + "Z"
            
            # Calculate content length (sum of text lengths)
            content_length = sum(
                len(c.get("text", "")) + len(c.get("thinking", ""))
                for c in content
            )
            
            # Determine error status from stop_reason
            # Note: "end_turn" and "stop" are normal completions, not errors
            stop_reason = message.get("stopReason", "unknown")
            is_error = stop_reason == "error" or message.get("error") is not None
            
            return TelemetryRecord(
                call_id=msg_id,
                session_id=session_id or "unknown",
                parent_id=parent_id,
                timestamp=ts_ms,
                timestamp_iso=ts_iso,
                api=message.get("api", "unknown"),
                provider=message.get("provider", "unknown"),
                model=model,
                stop_reason=stop_reason,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_cache_read=tokens_cache_read,
                tokens_cache_write=tokens_cache_write,
                tokens_total=tokens_total,
                cache_hit_ratio=cache_hit_ratio,
                cost_input=cost_input,
                cost_output=cost_output,
                cost_cache_read=cost_cache_read,
                cost_cache_write=cost_cache_write,
                cost_total=cost_total,
                has_thinking=has_thinking,
                has_tool_calls=has_tool_calls,
                tool_names=tool_names,
                content_length=content_length,
                is_error=is_error,
            )
        except Exception as e:
            logger.error(f"Failed to convert message to record: {e}")
            return None
    
    def _insert_record(self, cursor: sqlite3.Cursor, record: TelemetryRecord, source_file: str):
        """Insert a TelemetryRecord into the database."""
        cursor.execute("""
            INSERT OR REPLACE INTO records (
                call_id, session_id, parent_id, timestamp, timestamp_iso, api, provider, model,
                stop_reason, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write,
                tokens_total, cache_hit_ratio, cost_input, cost_output, cost_cache_read,
                cost_cache_write, cost_total, has_thinking, has_tool_calls, tool_names,
                content_length, is_error, source_file
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            record.call_id,
            record.session_id,
            record.parent_id,
            record.timestamp,
            record.timestamp_iso,
            record.api,
            record.provider,
            record.model,
            record.stop_reason,
            record.tokens_input,
            record.tokens_output,
            record.tokens_cache_read,
            record.tokens_cache_write,
            record.tokens_total,
            record.cache_hit_ratio,
            record.cost_input,
            record.cost_output,
            record.cost_cache_read,
            record.cost_cache_write,
            record.cost_total,
            int(record.has_thinking),
            int(record.has_tool_calls),
            json.dumps(record.tool_names),
            record.content_length,
            int(record.is_error),
            source_file,
        ))
    
    def get_records(self, filters: Optional[Dict[str, Any]] = None) -> List[TelemetryRecord]:
        """Fetch records from database with optional filters."""
        query = "SELECT * FROM records WHERE 1=1"
        params = []
        
        if filters:
            if "session_id" in filters:
                query += " AND session_id = ?"
                params.append(filters["session_id"])
            if "provider" in filters:
                query += " AND provider = ?"
                params.append(filters["provider"])
            if "model" in filters:
                query += " AND model = ?"
                params.append(filters["model"])
            if "start_time" in filters:
                query += " AND timestamp >= ?"
                params.append(filters["start_time"])
            if "end_time" in filters:
                query += " AND timestamp <= ?"
                params.append(filters["end_time"])
        
        query += " ORDER BY timestamp DESC"
        
        records = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                record = self._row_to_record(row)
                records.append(record)
        
        return records
    
    def get_session_ids(self) -> List[str]:
        """Get all unique session IDs."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT session_id FROM records ORDER BY session_id")
            return [row[0] for row in cursor.fetchall()]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Total calls
            cursor.execute("SELECT COUNT(*) FROM records")
            total_calls = cursor.fetchone()[0]
            
            # Total cost
            cursor.execute("SELECT SUM(cost_total) FROM records")
            total_cost = cursor.fetchone()[0] or 0.0
            
            # Total tokens
            cursor.execute("SELECT SUM(tokens_total) FROM records")
            total_tokens = cursor.fetchone()[0] or 0
            
            # Error count
            cursor.execute("SELECT COUNT(*) FROM records WHERE is_error = 1")
            error_count = cursor.fetchone()[0]
            
            # Sessions count
            cursor.execute("SELECT COUNT(DISTINCT session_id) FROM records")
            session_count = cursor.fetchone()[0]
            
            return {
                "total_calls": total_calls,
                "total_cost": round(total_cost, 6),
                "total_tokens": total_tokens,
                "error_count": error_count,
                "session_count": session_count,
                "error_rate": round(error_count / total_calls, 4) if total_calls > 0 else 0.0,
            }
    
    def _row_to_record(self, row: sqlite3.Row) -> TelemetryRecord:
        """Convert a SQLite row to a TelemetryRecord."""
        return TelemetryRecord(
            call_id=row["call_id"],
            session_id=row["session_id"],
            parent_id=row["parent_id"],
            timestamp=row["timestamp"],
            timestamp_iso=row["timestamp_iso"],
            api=row["api"],
            provider=row["provider"],
            model=row["model"],
            stop_reason=row["stop_reason"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            tokens_cache_read=row["tokens_cache_read"],
            tokens_cache_write=row["tokens_cache_write"],
            tokens_total=row["tokens_total"],
            cache_hit_ratio=row["cache_hit_ratio"],
            cost_input=row["cost_input"],
            cost_output=row["cost_output"],
            cost_cache_read=row["cost_cache_read"],
            cost_cache_write=row["cost_cache_write"],
            cost_total=row["cost_total"],
            has_thinking=bool(row["has_thinking"]),
            has_tool_calls=bool(row["has_tool_calls"]),
            tool_names=json.loads(row["tool_names"] or "[]"),
            content_length=row["content_length"],
            is_error=bool(row["is_error"]),
        )
