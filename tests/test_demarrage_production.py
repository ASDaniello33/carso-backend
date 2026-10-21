"""Démarrage en production : l'API ne dépend plus de PostgreSQL pour servir.

Deux incidents réels sont verrouillés ici :

1. ``LangGraphAGUIAgent`` lit ``graph.nodes`` à sa construction. Avec le proxy
   ``GrapheParUtilisateur``, cela résolvait le graphe au **montage**, donc une
   connexion base pendant l'import de l'application — un préflight OPTIONS
   répondait 500 « application initialization timed out » quand la base était
   lente ou injoignable.
2. La restauration de la configuration d'agents et le premier administrateur
   s'exécutaient avant ``yield`` du lifespan : le serveur n'acceptait aucune
   requête tant que PostgreSQL n'avait pas répondu.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings


class _FauxPaquetAgui:
    """Double fidèle du paquet réel : le wrapper lit ``graph.nodes``."""

    derniere_creation: Any = None

    class LangGraphAGUIAgent:
        def __init__(
            self,
            *,
            name: str,
            graph: Any,
            description: str | None = None,
            config: Any = None,
            **_: Any,
        ) -> None:
            self.name = name
            self.graph = graph
            # Lecture identique à la ligne qui déclenchait la connexion base.
            self.noeuds = list(graph.nodes)

    @classmethod
    def add_langgraph_fastapi_endpoint(
        cls, app: FastAPI, agent: Any, path: str, dependencies: Any = None
    ) -> None:
        cls.derniere_creation = agent
        app.add_api_route(path, lambda: {"ok": True}, methods=["POST"])


class _ManagerSansBase:
    """Manager qui refuse toute résolution de graphe (donc toute base)."""

    def __init__(self) -> None:
        self.resolutions = 0

    def is_configured(self) -> bool:
        return True

    def effective_settings(self) -> Settings:
        return Settings(agent_max_iterations=10)

    def add_listener(self, listener: Any) -> None:
        self.listener = listener

    def graph(self, *args: Any, **kwargs: Any) -> Any:
        self.resolutions += 1
        raise AssertionError("le montage AG-UI a touché la base")


def test_montage_agui_ne_resout_pas_le_graphe(monkeypatch) -> None:
    """Le montage (fait à l'import) monte les routes sans appeler ``graph()``."""
    from app.agents.catalogue import AGENT_DEFINITIONS
    from app.api import agui as module_agui

    manager = _ManagerSansBase()
    monkeypatch.setattr(module_agui, "_importer_agui", lambda: _FauxPaquetAgui)
    monkeypatch.setattr(module_agui, "get_runtime_manager", lambda: manager)

    app = FastAPI()
    module_agui.monter_endpoints_agui(app, Settings(agent_max_iterations=10))

    assert manager.resolutions == 0
    assert len(app.state.agui_routes) == len(AGENT_DEFINITIONS)
    # Le proxy est bien rendu au wrapper : les accès réels passeront par lui.
    from app.agents.graphe_par_utilisateur import GrapheParUtilisateur

    assert isinstance(_FauxPaquetAgui.derniere_creation.graph, GrapheParUtilisateur)


def test_api_repond_pendant_l_initialisation(monkeypatch) -> None:
    """Un préflight et le liveness répondent même si l'init base est bloquée."""
    import app.main as module_main

    demarre = threading.Event()
    libere = threading.Event()

    def initialisation_bloquante() -> None:
        demarre.set()
        libere.wait(10)

    monkeypatch.setattr(module_main, "initialiser_runtime", initialisation_bloquante)
    monkeypatch.setattr(
        module_main,
        "get_settings",
        lambda: Settings(demarrage_initialise_runtime=True),
    )

    with TestClient(module_main.app) as client:
        assert demarre.wait(5), "l'initialisation de fond n'a pas démarré"
        preflight = client.options(
            "/api/v1/social/non-lus",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert client.get("/api/v1/health").status_code == 200
        # Le corps du test ne doit pas attendre le ``join(timeout=5)``.
        libere.set()


def test_initialisation_desactivee_ne_lance_aucune_tache(monkeypatch) -> None:
    """Le drapeau de configuration coupe l'initialisation (suite de tests)."""
    import app.main as module_main

    appels: list[int] = []
    monkeypatch.setattr(module_main, "initialiser_runtime", lambda: appels.append(1))
    monkeypatch.setattr(
        module_main,
        "get_settings",
        lambda: Settings(demarrage_initialise_runtime=False),
    )

    with TestClient(module_main.app) as client:
        assert client.get("/api/v1/health").status_code == 200
    assert appels == []


def test_settings_par_defaut_initialisent_en_tache() -> None:
    """Sans surcharge (production), l'initialisation reste active."""
    from app.core.config import Settings as Modele

    assert Modele.model_fields["demarrage_initialise_runtime"].default is True
