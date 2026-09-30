"""SQLAlchemy 2.x engine and session factory (ADR 0001)."""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_db_engine() -> Engine:
    """Create the application engine from settings."""
    settings = get_settings()
    return create_engine(settings.database_url, echo=settings.db_echo, pool_pre_ping=True)


engine: Engine = create_db_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> dict[str, Any]:
    """Run a trivial read-only query; used by readiness checks and gated tests."""
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar_one()
    return {"database": "reachable", "server_version": str(version)}
