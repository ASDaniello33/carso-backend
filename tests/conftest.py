"""Pytest configuration and shared fixtures."""

import os
import re
from collections.abc import Iterator

# Mode lab explicite pour toute la suite : un .env local peut définir
# AUTH_SECRET (dev/production) — les tests doivent rester indépendants de
# l'environnement et tourner sans JWT (utilisateur technique anonyme).
os.environ["AUTH_SECRET"] = ""
# Persistance LangGraph (incrément 32) : la suite ne touche jamais la base
# réelle via le manager — fallback mémoire, sauf les tests gated qui passent
# des Settings explicites (RUN_DB_TESTS).
os.environ["AGENT_CHECKPOINT_PERSISTANT"] = "0"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.ddl import CreateColumn

from app.domain.base import Base
from app.infrastructure.database import get_session
from app.main import app

# --- Hooks SQLite (tests sans PostgreSQL) --------------------------------------


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    """JSONB → JSON sur SQLite pour les tests de services/API."""
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_server_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    """Retire gen_random_uuid() du DDL SQLite (défaut ORM uuid4 côté client)."""
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    """HTTP client bound to the FastAPI app (real dependency graph)."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api_session_factory() -> Iterator[sessionmaker[Session]]:
    """SQLite memory engine + factory, tables créées, pour tests API.

    ``StaticPool`` + ``check_same_thread=False`` : une SEULE connexion partagée
    (TestClient exécute la requête dans un autre thread qu'une mémoire SQLite
    par thread serait vide) ; ``expire_on_commit=False`` : les instances
    seedées restent utilisables après commit.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine, expire_on_commit=False
    )
    yield factory
    engine.dispose()


@pytest.fixture
def api_client(
    api_session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    """HTTP client avec la dépendance de session redirigée vers SQLite mémoire.

    La session suit le même contrat que ``get_session`` : commit en fin de
    requête, rollback sur exception — la transaction reste côté requête.
    """

    def _override() -> Iterator[Session]:
        session = api_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed(api_session_factory: sessionmaker[Session], *entities: object) -> None:
    """Insère des entités de test puis ferme la session (commit inclus)."""
    session = api_session_factory()
    try:
        session.add_all(list(entities))
        session.commit()
    finally:
        session.close()


def _db_tests_enabled() -> bool:
    """DB tests run only when explicitly enabled (suite stays green without a DB).

    Deux sources acceptées, pour rester cohérent avec le message de skip :
    - la variable d'environnement ``RUN_DB_TESTS=1`` ;
    - le réglage ``run_db_tests`` de ``backend/.env`` (configuration unique de
      l'application, lue via ``Settings``).
    """
    if os.environ.get("RUN_DB_TESTS", "0") == "1":
        return True

    from app.core.config import get_settings

    return get_settings().run_db_tests


requires_db = pytest.mark.skipif(
    not _db_tests_enabled(),
    reason="RUN_DB_TESTS=1 not set (configure backend/.env first)",
)


# --- Secrets et transactions courtes (incréments 47) ---------------------------
#
# Deux mécanismes partagés par les tests qui manipulent des secrets chiffrés :
# la clé de chiffrement (avec purge des caches mémoïsés) et la redirection des
# sessions courtes ouvertes **hors requête HTTP** — le flux OAuth vit sur le fil
# MCP, il ouvre donc ses propres transactions.


@pytest.fixture
def cle_chiffrement(monkeypatch: pytest.MonkeyPatch) -> str:
    """Clé Fernet valide pour la durée du test (le chiffrement est disponible).

    ``get_settings`` est mémoïsé : sans purge du cache, l'environnement posé ici
    ne serait pas relu et le service refuserait le secret pour « absence de clé ».
    """
    from cryptography.fernet import Fernet

    from app.core import chiffrement
    from app.core.config import get_settings

    cle = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("PARAMETRES_CHIFFREMENT_KEY", cle)
    monkeypatch.setenv("AUTH_SECRET", "secret-de-test")
    get_settings.cache_clear()
    chiffrement.effacer_cache()
    yield cle
    get_settings.cache_clear()
    chiffrement.effacer_cache()


@pytest.fixture
def sans_chiffrement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucune clé exploitable : enregistrer un secret doit être refusé."""
    from app.core import chiffrement
    from app.core.config import get_settings

    monkeypatch.delenv("PARAMETRES_CHIFFREMENT_KEY", raising=False)
    monkeypatch.setenv("AUTH_SECRET", "")
    get_settings.cache_clear()
    chiffrement.effacer_cache()
    yield
    get_settings.cache_clear()
    chiffrement.effacer_cache()


@pytest.fixture
def session_locale(monkeypatch: pytest.MonkeyPatch, api_session_factory):
    """Redirige les transactions **courtes** vers SQLite de test.

    Les services qui travaillent hors requête (stockage des jetons OAuth, état
    publié par le fil MCP) ouvrent leur propre session : les patcher est ce qui
    permet de les tester sans toucher la base réelle.
    """
    import app.infrastructure.database as base

    monkeypatch.setattr(base, "SessionLocal", api_session_factory)
    return api_session_factory


# --- Aucun appel réel aux moteurs publics dans la suite -----------------------
# La recherche web passe par ``curl_cffi`` (empreinte de navigateur), qui gère son
# propre HTTP : le transport httpx injecté dans les tools ne le contourne pas, et
# un test qui n'installe pas de faux moteur ferait un vrai appel réseau — cinq
# moteurs, 12 s chacun, plus YouTube. Seuls ``test_moteurs_web.py`` et
# ``test_tools_recherche_video.py`` exercent cette couche, avec une session
# factice ou ``pages``/``videos`` remplacées ; partout ailleurs, un appel réel est
# une fuite réseau qui doit échouer fort.


def _session_interdite() -> object:
    raise AssertionError("session curl_cffi réelle dans la suite : installer un faux moteur")


@pytest.fixture(autouse=True)
def sans_moteurs_reels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.tools.moteurs_web._session", _session_interdite)
