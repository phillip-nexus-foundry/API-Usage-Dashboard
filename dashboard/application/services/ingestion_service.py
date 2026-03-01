"""
Ingestion service.
Manages JSONL file watching, parsing, and record insertion.
Supports incremental processing with checksum-based change detection.
"""
import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from dashboard.data.repositories.telemetry_repo import SQLAlchemyTelemetryRepo
from dashboard.data.repositories.file_index_repo import (
    SQLAlchemyFileIndexRepo, compute_file_checksum,
)
from dashboard.application.services.cost_service import CostService
from dashboard.application.events import EventBus

logger = logging.getLogger(__name__)

# Parser version: bump when cost logic changes to invalidate cache
PARSER_VERSION = "2026-02-28-3tier-v1"


@dataclass
class IngestionResult:
    """Result of processing a set of files."""
    files_scanned: int = 0
    files_updated: int = 0
    records_inserted: int = 0
    errors: int = 0


class IngestionService:
    """Orchestrates JSONL file watching and record ingestion."""

    def __init__(
        self,
        telemetry_repo: SQLAlchemyTelemetryRepo,
        file_index_repo: SQLAlchemyFileIndexRepo,
        cost_service: CostService,
        event_bus: Optional[EventBus] = None,
        sessions_dir: str = "",
    ):
        self._telemetry = telemetry_repo
        self._file_index = file_index_repo
        self._cost_service = cost_service
        self._event_bus = event_bus
        self._sessions_dir = sessions_dir
        self._scan_lock = threading.Lock()
        self._last_scan_time = 0
        self._min_scan_interval = 5.0

    def scan_all(self) -> IngestionResult:
        """Scan all JSONL files in sessions_dir."""
        if not self._scan_lock.acquire(blocking=False):
            logger.debug("Scan already in progress, skipping")
            return IngestionResult()

        try:
            now = time.time()
            if now - self._last_scan_time < self._min_scan_interval:
                return IngestionResult()

            result = IngestionResult()
            sessions_path = Path(self._sessions_dir)

            if not sessions_path.exists():
                logger.warning(f"Sessions directory not found: {self._sessions_dir}")
                return result

            for jsonl_file in sessions_path.glob("*.jsonl"):
                file_result = self._process_file(str(jsonl_file))
                result.files_scanned += 1
                if file_result > 0:
                    result.files_updated += 1
                    result.records_inserted += file_result
                elif file_result < 0:
                    result.errors += 1

            self._last_scan_time = time.time()

            if result.records_inserted > 0:
                logger.info(
                    f"Ingestion: {result.files_updated}/{result.files_scanned} files updated, "
                    f"{result.records_inserted} records inserted"
                )
                # Fire event
                if self._event_bus:
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(
                                self._event_bus.publish(EventBus.RECORDS_INGESTED, {
                                    "records_inserted": result.records_inserted,
                                })
                            )
                    except RuntimeError:
                        pass

            return result

        finally:
            self._scan_lock.release()

    def _process_file(self, filepath: str) -> int:
        """
        Process a single JSONL file incrementally.
        Returns count of records inserted, or -1 on error.
        """
        try:
            stat = os.stat(filepath)
            current_mtime = stat.st_mtime
            current_size = stat.st_size
            current_checksum = compute_file_checksum(filepath)

            # Check file index
            entry = self._file_index.get_file_entry(filepath)

            if entry:
                # Skip if unchanged
                if (
                    entry["mtime"] == current_mtime
                    and entry.get("size") == current_size
                    and entry.get("checksum") == current_checksum
                    and entry.get("parser_version") == PARSER_VERSION
                ):
                    return 0

                # If checksum changed but size decreased, file was rewritten
                if entry.get("checksum") and entry["checksum"] != current_checksum:
                    if current_size < (entry.get("size") or 0):
                        # File was rewritten, process from start
                        start_line = 0
                    else:
                        # File was appended, resume from last position
                        start_line = entry.get("last_line_processed", 0)
                else:
                    start_line = entry.get("last_line_processed", 0)
            else:
                start_line = 0

            # Parse the file using the existing OpenClaw reader
            # Import here to avoid circular imports
            from parsers.openclaw_reader import OpenClawReader

            records = OpenClawReader.parse_file(filepath, start_line=start_line)
            if not records:
                # Update index even if no new records (mtime changed)
                self._file_index.upsert_file_entry({
                    "path": filepath,
                    "mtime": current_mtime,
                    "size": current_size,
                    "checksum": current_checksum,
                    "record_count": entry.get("record_count", 0) if entry else 0,
                    "parser_version": PARSER_VERSION,
                    "last_line_processed": start_line,
                })
                return 0

            # Convert to dicts for repository insertion
            record_dicts = []
            for rec in records:
                rec_dict = rec if isinstance(rec, dict) else rec.__dict__
                record_dicts.append(rec_dict)

            # Insert into database
            inserted = self._telemetry.insert_records(record_dicts)

            # Update file index
            total_lines = start_line + len(records)
            prev_count = entry.get("record_count", 0) if entry else 0
            self._file_index.upsert_file_entry({
                "path": filepath,
                "mtime": current_mtime,
                "size": current_size,
                "checksum": current_checksum,
                "record_count": prev_count + inserted,
                "parser_version": PARSER_VERSION,
                "last_line_processed": total_lines,
            })

            return inserted

        except Exception as e:
            logger.error(f"Failed to process {filepath}: {e}")
            return -1

    def setup_file_watcher(self):
        """Set up watchdog file watcher for the sessions directory."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            service = self

            class SessionFileHandler(FileSystemEventHandler):
                def __init__(self):
                    self._timer = None

                def _debounced_scan(self):
                    if self._timer:
                        self._timer.cancel()
                    self._timer = threading.Timer(1.0, service.scan_all)
                    self._timer.start()

                def on_modified(self, event):
                    if event.src_path.endswith(".jsonl"):
                        self._debounced_scan()

                def on_created(self, event):
                    if event.src_path.endswith(".jsonl"):
                        self._debounced_scan()

            observer = Observer()
            observer.schedule(SessionFileHandler(), self._sessions_dir, recursive=False)
            observer.daemon = True
            observer.start()
            logger.info(f"Watching {self._sessions_dir} for JSONL changes")
            return observer

        except ImportError:
            logger.warning("watchdog not installed; file watching disabled")
            return None
