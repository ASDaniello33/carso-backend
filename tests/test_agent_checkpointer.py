"""Checkpointer persistant des agents (incrément 32, ADR 0009).

La persistance doit être **transverse et sûre** : un PostgresSaver partagé par
tous les graphes quand PostgreSQL est joignable, un fallback mémoire explicite
sinon — jamais une dégradation silencieuse (l'état réel est projetable), et la
suite de tests ne doit **jamais** toucher la base réelle (réglage forcé à 0).
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

import app.agents.checkpointer as module_checkpointer
from app.agents.checkpointer import (
    etat_checkpointer,
    get_checkpointer,
    liberer_checkpointer,
    reinitialiser_pour_tests,
    url_checkpoint_depuis_db,
)
from app.core.config import Settings
from tests.conftest import requires_db


@pytest.fixture(autouse=True)
def _etat_propre() -> object:
    """Chaque test part d'un cache process vide (indépendance totale)."""
    reinitialiser_pour_tests()
    yield
    reinitialiser_pour_tests()


def _settings(persistant: bool = True) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg] - isolement des .env locaux
        auth_secret="",  # requis par les tests (conftest)
        agent_checkpoint_persistant=persistant,
        database_url="postgresql+psycopg://u:p@localhost:5432/carso_dev",
    )


# --- URL de checkpointing -------------------------------------------------------


def test_url_checkpoint_derivee_de_lurl_sqlalchemy() -> None:
    """postgresql+psycopg://… → postgresql://… (même serveur, même base)."""
    assert url_checkpoint_depuis_db(
        "postgresql+psycopg://carso:secret@localhost:5432/carso_dev"
    ) == "postgresql://carso:secret@localhost:5432/carso_dev"


def test_url_checkpoint_refuse_un_schema_non_postgresql() -> None:
    """L'application est PostgreSQL-only (ADR 0001) : échec explicite."""
    with pytest.raises(ValueError, match="non PostgreSQL"):
        url_checkpoint_depuis_db("sqlite:///file.db")


# --- Fabrique : mode forcé, fallback, résolution unique --------------------------


def test_desactive_par_configuration_reste_en_memoire(monkeypatch) -> None:
    """AGENT_CHECKPOINT_PERSISTANT=0 → aucun saver, état explicite."""
    monkeypatch.setenv("AGENT_CHECKPOINT_PERSISTANT", "0")
    with patch.object(module_checkpointer, "_construire_saver") as fabrique:
        saver = get_checkpointer(_settings(persistant=False))
    assert saver is None
    fabrique.assert_not_called()
    etat = etat_checkpointer()
    assert etat["mode"] == "memoire"
    assert etat["erreur"] == "désactivé par configuration"


def test_postgres_joignable_renvoie_le_saver_partage(monkeypatch) -> None:
    """Base joignable : un PostgresSaver est construit UNE fois et mis en cache."""
    construit = object()
    appels: list[Settings] = []

    def _fabrique(settings: Settings) -> object:
        appels.append(settings)
        return construit

    monkeypatch.setattr(module_checkpointer, "_construire_saver", _fabrique)

    premier = get_checkpointer(_settings())
    second = get_checkpointer(_settings())
    assert premier is construit
    assert second is construit  # Cache process : une seule fabrique.
    assert len(appels) == 1
    assert etat_checkpointer()["mode"] == "postgres"


def test_echec_bascule_en_memoire_sans_masquer_la_cause(monkeypatch) -> None:
    """Base injoignable : fallback mémoire + erreur lisible (jamais silencieux).

    Le stub écrit dans ``_ETAT`` comme le fait la vraie fabrique (appelée sous
    verrou : elle ne doit JAMAIS reprendre le verrou via l'accesseur public —
    ``threading.Lock`` n'est pas réentrant, c'est le deadlock classique).
    """

    def _fabrique(settings: Settings) -> None:
        module_checkpointer._ETAT["erreur"] = "ConnectionRefusedError: refus"
        return None

    monkeypatch.setattr(module_checkpointer, "_construire_saver", _fabrique)
    assert get_checkpointer(_settings()) is None
    etat = etat_checkpointer()
    assert etat["mode"] == "memoire"
    assert "ConnectionRefusedError" in str(etat["erreur"])


def test_lechec_est_resolu_une_seule_fois_jusquau_redemarrage(monkeypatch) -> None:
    """Un échec initial ne martèle pas la base à chaque graphe (cache définitif)."""
    appels: list[int] = []

    def _fabrique(settings: Settings) -> None:
        appels.append(1)
        return None

    monkeypatch.setattr(module_checkpointer, "_construire_saver", _fabrique)
    get_checkpointer(_settings())
    get_checkpointer(_settings())
    assert len(appels) == 1


def test_liberer_reinitialise_le_cache(monkeypatch) -> None:
    """À l'arrêt du process, le saver est fermé et la résolution repart de zéro."""
    ferme: list[bool] = []

    class _Pool:
        def close(self) -> None:
            ferme.append(True)

    class _Saver:
        conn = _Pool()

    monkeypatch.setattr(
        module_checkpointer, "_construire_saver", lambda settings: _Saver()
    )
    saver = get_checkpointer(_settings())
    assert saver is not None
    liberer_checkpointer()
    assert ferme == [True]
    assert etat_checkpointer() == {"mode": "memoire", "erreur": None}
    # Nouvelle résolution possible (le cache a été vidé, pas seulement l'objet).


def test_resolution_concurrente_unique(monkeypatch) -> None:
    """Deux threads qui demandent le saver simultanément → une seule fabrique."""
    compteur = {"n": 0}
    verrou_compteur = threading.Lock()

    def _fabrique(settings: Settings) -> object:
        with verrou_compteur:
            compteur["n"] += 1
        return object()

    monkeypatch.setattr(module_checkpointer, "_construire_saver", _fabrique)
    resultats: list[object | None] = []

    def _demander() -> None:
        resultats.append(get_checkpointer(_settings()))

    threads = [threading.Thread(target=_demander) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(resultats) == 4
    assert all(s is resultats[0] for s in resultats)
    assert compteur["n"] == 1


# --- Intégration réelle (gated RUN_DB_TESTS=1) -----------------------------------


@requires_db
def test_integration_postgres_saver_reel() -> None:
    """Avec PostgreSQL réel : tables créées et une écriture relue intacte.

    Gated comme les autres tests base (conftest) : aucun impact sur la suite
    standard.
    """
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres import PostgresSaver

    # Persistance demandée explicitement : le conftest force le fallback
    # mémoire pour toute la suite (AGENT_CHECKPOINT_PERSISTANT=0) — seul ce
    # test gated, qui cible la base réelle, la réactive.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        auth_secret="",
        agent_checkpoint_persistant=True,
    )
    saver = get_checkpointer(settings)
    assert isinstance(saver, PostgresSaver)
    FIL = "test-checkpointer-32"
    try:
        # Contrat d'écriture PostgresSaver : thread_id **et** checkpoint_ns.
        config = {"configurable": {"thread_id": FIL, "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        # ``put`` renvoie la config complète (checkpoint_id inclus) : c'est
        # ELLE que ``put_writes`` exige — une config nue lève ``KeyError``.
        config_ecriture = saver.put(config, checkpoint, {"source": "test"}, {})
        relu = saver.get_tuple(config)
        assert relu is not None
        assert relu.checkpoint["id"] == checkpoint["id"]
        # Rond-trip strict : le sérialiseur rejette tout type hors allowlist.
        saver.put_writes(
            config_ecriture, [("messages", [HumanMessage(content="ok")])], "t1"
        )
        relu2 = saver.get_tuple(config)
        assert relu2 is not None
        # Forme d'un write en attente : (task_id, canal, valeur).
        assert relu2.pending_writes
        premiere = relu2.pending_writes[0]
        assert premiere[0] == "t1"
        assert premiere[1] == "messages"
        assert isinstance(premiere[2][0], HumanMessage)
    finally:
        # Le thread de test ne doit pas s'accumuler dans la base de dev.
        saver.delete_thread(FIL)
