"""DB connectivity test — gated by RUN_DB_TESTS=1 (see conftest.py)."""

from app.infrastructure.database import check_database_connection
from tests.conftest import requires_db


@requires_db
def test_database_is_reachable() -> None:
    result = check_database_connection()

    assert result["database"] == "reachable"
    assert "PostgreSQL" in result["server_version"]
