"""
SQLAlchemy implementation of FileIndexRepository.
Tracks parsed JSONL files for incremental processing.
"""
import hashlib
import logging
from typing import Optional

from dashboard.data.models import FileIndex
from dashboard.data.database import Database

logger = logging.getLogger(__name__)


def compute_file_checksum(filepath: str, chunk_size: int = 1024) -> str:
    """Fast checksum: MD5 of first 1KB + last 1KB."""
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            first = f.read(chunk_size)
            hasher.update(first)
            # Seek to end - chunk_size
            f.seek(0, 2)  # end
            size = f.tell()
            if size > chunk_size:
                f.seek(max(0, size - chunk_size))
                last = f.read(chunk_size)
                hasher.update(last)
    except (OSError, IOError):
        return ""
    return hasher.hexdigest()


class SQLAlchemyFileIndexRepo:
    """Concrete file index repository backed by SQLAlchemy."""

    def __init__(self, db: Database):
        self._db = db

    def get_file_entry(self, path: str) -> Optional[dict]:
        with self._db.session() as session:
            row = session.query(FileIndex).filter(FileIndex.path == path).first()
            return self._to_dict(row) if row else None

    def upsert_file_entry(self, entry: dict) -> None:
        with self._db.session() as session:
            existing = session.query(FileIndex).filter(
                FileIndex.path == entry["path"]
            ).first()
            if existing:
                for key, val in entry.items():
                    if key != "path":
                        setattr(existing, key, val)
            else:
                obj = FileIndex(**entry)
                session.add(obj)

    def get_all_entries(self) -> list[dict]:
        with self._db.session() as session:
            rows = session.query(FileIndex).all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(fi: FileIndex) -> dict:
        return {
            "path": fi.path,
            "mtime": fi.mtime,
            "record_count": fi.record_count,
            "parser_version": fi.parser_version,
            "size": fi.size,
            "checksum": fi.checksum,
            "last_line_processed": fi.last_line_processed,
        }
