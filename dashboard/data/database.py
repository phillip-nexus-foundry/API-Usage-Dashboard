"""
Database connection factory.
Supports SQLite (default) and PostgreSQL via SQLAlchemy.

Configure via config.yaml:
  database_url: postgresql://user:pass@localhost/api_usage_dashboard
  # or omit for SQLite default: sqlite:///dashboard.db
"""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session


def get_database_url(config: dict) -> str:
    """Resolve database URL from config or environment."""
    url = os.environ.get("DATABASE_URL") or config.get("database_url")
    if url:
        return url
    # Default: SQLite in project root
    db_path = config.get("database_path", "dashboard.db")
    return f"sqlite:///{db_path}"


def create_db_engine(config: dict):
    """Create SQLAlchemy engine from config."""
    url = get_database_url(config)
    kwargs = {}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, echo=False, **kwargs)

        # Enable WAL mode for better concurrent reads
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    else:
        # PostgreSQL or other
        kwargs["pool_size"] = config.get("db_pool_size", 5)
        kwargs["max_overflow"] = config.get("db_max_overflow", 10)
        kwargs["pool_pre_ping"] = True
        engine = create_engine(url, echo=False, **kwargs)

    return engine


class Database:
    """Database session manager."""

    def __init__(self, config: dict):
        self.engine = create_db_engine(config)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Session:
        """Provide a transactional session scope."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tables(self):
        """Create all tables from models. Used for initial setup."""
        from dashboard.data.models import Base
        Base.metadata.create_all(self.engine)

    @property
    def url(self) -> str:
        return str(self.engine.url)

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in str(self.engine.url)

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in str(self.engine.url)
