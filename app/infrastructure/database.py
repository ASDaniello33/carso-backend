"""SQLAlchemy 2.x engine and session factory (ADR 0001)."""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

#: Délai de connexion PostgreSQL. Sans borne, psycopg attend indéfiniment une
#: adresse qui ne répond pas (pare-feu, pooler indisponible) : une simple
#: requête lisait alors un blocage de plusieurs minutes au lieu d'un échec
#: rapide. Constaté en production sur un démarrage complet (montage AG-UI).
DB_CONNECT_TIMEOUT_S = 5


def create_db_engine() -> Engine:
    """Create the application engine from settings.

    La borne de connexion est propre au pilote : ``connect_timeout`` n'est
    accepté que par PostgreSQL (les tests SQLite passent sans option).
    """
    settings = get_settings()
    options: dict[str, Any] = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
    }
    if settings.database_url.startswith("postgresql"):
        options["connect_args"] = {"connect_timeout": DB_CONNECT_TIMEOUT_S}
    return create_engine(settings.database_url, **options)


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
