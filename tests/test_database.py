"""DB connectivity test — gated by RUN_DB_TESTS=1 (see conftest.py)."""

from typing import Any

from app.core.config import Settings
from app.infrastructure import database
from app.infrastructure.database import check_database_connection
from tests.conftest import requires_db


@requires_db
def test_database_is_reachable() -> None:
    result = check_database_connection()

    assert result["database"] == "reachable"
    assert "PostgreSQL" in result["server_version"]


def test_moteur_postgres_borne_le_delai_de_connexion(monkeypatch) -> None:
    """Sans borne, une base injoignable bloque le démarrage indéfiniment.

    Constaté en production : le montage AG-UI (à l'import) ouvrait une session
    et psycopg attendait la fin des tentatives TCP, jusqu'au délai
    d'initialisation de la plateforme.
    """
    saisis: dict[str, Any] = {}
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@db.invalid:5432/carso",
        ),
    )
    monkeypatch.setattr(
        database,
        "create_engine",
        lambda url, **kw: saisis.update(url=url, **kw) or object(),
    )

    database.create_db_engine()

    assert saisis["connect_args"] == {"connect_timeout": database.DB_CONNECT_TIMEOUT_S}
    assert database.DB_CONNECT_TIMEOUT_S > 0


def test_moteur_sqlite_ne_pose_pas_d_option_postgres(monkeypatch) -> None:
    """Les tests SQLite partagent la même fabrique sans option de pilote."""
    saisis: dict[str, Any] = {}
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: Settings(_env_file=None, database_url="sqlite://"),
    )
    monkeypatch.setattr(
        database,
        "create_engine",
        lambda url, **kw: saisis.update(url=url, **kw) or object(),
    )

    database.create_db_engine()

    assert "connect_args" not in saisis
