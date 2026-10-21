"""Tests for configuration loading."""

import logging
from pathlib import Path

from app.core.config import RACINE_BACKEND, Settings


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)  # ignore any local .env for determinism

    assert settings.environment == "development"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.run_db_tests is False
    assert settings.agent_max_iterations == 1000
    assert settings.agent_attachment_excerpt_chars == 4000


def test_env_file_ancre_sur_le_paquet() -> None:
    """Le ``.env`` est lu quel que soit le répertoire de lancement.

    Un ``env_file=".env"`` relatif n'est résolu que dans le répertoire courant :
    lancé depuis la racine (``uvicorn app.main:app --app-dir backend``), le
    fichier était ignoré — ``CORS_ORIGINS`` retombait sur les origines locales
    (préflight 400 pour le frontend déployé), comme ``DATABASE_URL`` ou
    ``AUTH_SECRET``. Constat de production du 20/10/2026.
    """
    fichier = Path(Settings.model_config["env_file"])

    assert fichier.is_absolute()
    assert fichier == RACINE_BACKEND / ".env"


def test_env_file_ne_suit_pas_le_repertoire_courant(
    tmp_path: Path, monkeypatch
) -> None:
    """Un ``.env`` posé dans le répertoire courant ne change rien."""
    (tmp_path / ".env").write_text(
        "CORS_ORIGINS=https://marqueur.invalid\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert "https://marqueur.invalid" not in Settings().cors_origins


def test_settings_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test_db")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/test_db"


def test_agent_max_iterations_env(monkeypatch) -> None:
    """AGENT_MAX_ITERATIONS pilote le recursion_limit LangGraph / AG-UI."""
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "2500")

    settings = Settings(_env_file=None)

    assert settings.agent_max_iterations == 2500


def test_agent_attachment_excerpt_chars_env(monkeypatch) -> None:
    """AGENT_ATTACHMENT_EXCERPT_CHARS borne l'extrait injecté au modèle."""
    monkeypatch.setenv("AGENT_ATTACHMENT_EXCERPT_CHARS", "2500")

    settings = Settings(_env_file=None)

    assert settings.agent_attachment_excerpt_chars == 2500


def test_logs_bruiteuses_bornes_en_mode_debug() -> None:
    """En ``debug=True`` (racine DEBUG), les transports tiers restent à INFO.

    Incrément 13 : chaque appel LLM produisait des dizaines de lignes DEBUG
    (httpcore, openai, langsmith, asyncio, fpdf) et deepagents journalisait la
    sonde des middlewares optionnels (langchain_fireworks / langchain_aws)
    avec sa traceback — un constat bénin lu comme une erreur.

    ``configure_logging`` **remplace** les handlers de la racine : les sauver et
    les rendre ici est obligatoire, sinon le capteur de logs de pytest disparaît
    pour tous les tests suivants — un ``caplog`` vide plus loin dans la suite
    (constat 04/10 : deux tests de la mention échouaient seulement en suite
    complète, pas isolés).
    """
    from app.core.logging import configure_logging

    racine = logging.getLogger()
    handlers_avant = list(racine.handlers)
    niveaux_avant = {
        nom: logging.getLogger(nom).level
        for nom in ("httpcore", "httpx", "openai", "langsmith", "asyncio", "fpdf", "deepagents")
    }
    try:
        configure_logging(level=logging.DEBUG)
        for nom in niveaux_avant:
            assert logging.getLogger(nom).level == logging.INFO, nom
    finally:
        configure_logging(level=logging.INFO)
        racine.handlers = handlers_avant  # le capteur de pytest reprend sa place
        for nom, niveau in niveaux_avant.items():
            logging.getLogger(nom).setLevel(niveau)
