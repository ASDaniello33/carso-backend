"""Runtime des agents : persistance, changement de modèle à chaud, API admin.

Couvre l'exigence d'administration :

1. la configuration est **persistée et versionnée** (une seule active) ;
2. elle est **appliquée à chaud** : changer de modèle reconstruit les graphes
   sans redémarrer le backend ;
3. elle est **mémorisée** : au démarrage suivant, ``restore_from_db`` réapplique
   la dernière décision ;
4. elle est **réservée à l'administrateur** et n'expose aucun secret.

Aucun test n'appelle de LLM : les fabriques d'agents et le constructeur de modèle
sont remplacés par des doubles.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import AgentDefinition
from app.agents.manager import AgentRuntimeManager, AgentRuntimeValues
from app.application.services import AgentRuntimeService
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, RoleUtilisateur
from app.domain.identity import Utilisateur
from app.tools.permissions import PermissionPolicy

# --- Section 1 : service, persistance, audit ---------------------------------


def _session(factory: sessionmaker[Session]) -> Session:
    return factory()


def _audits(session: Session) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent)).all())


def test_enregistrer_persiste_et_normalise(api_session_factory: sessionmaker[Session]) -> None:
    session = _session(api_session_factory)
    try:
        ligne = AgentRuntimeService(session, actor_id="admin-1").enregistrer(
            provider="OpenAI_Compatible",
            model="  gemma-9b  ",
            base_url="https://exemple.ai/v1",
            temperature=0.2,
            max_iterations=12,
            modifie_par="admin-1",
        )
        session.commit()

        assert ligne.actif is True
        assert ligne.provider == "openai_compatible"  # normalisé en minuscules
        assert ligne.model == "gemma-9b"  # espaces retirés
        assert ligne.temperature == pytest.approx(0.2)
        assert ligne.max_iterations == 12
        assert ligne.modifie_par == "admin-1"
    finally:
        session.close()


def test_enregistrer_est_audite(api_session_factory: sessionmaker[Session]) -> None:
    session = _session(api_session_factory)
    try:
        AgentRuntimeService(session, actor_id="admin-1").enregistrer(
            provider="openai", model="gpt-x"
        )
        session.commit()

        evenements = _audits(session)
        assert [e.action for e in evenements] == [
            ActionAudit.CONFIGURATION_AGENT_MODIFIEE.value
        ]
        assert evenements[0].entity_type == "configuration_agent"
        assert evenements[0].actor_id == "admin-1"
    finally:
        session.close()


def test_une_seule_configuration_active(api_session_factory: sessionmaker[Session]) -> None:
    session = _session(api_session_factory)
    try:
        service = AgentRuntimeService(session)
        service.enregistrer(provider="openai", model="modele-1")
        service.enregistrer(provider="openai", model="modele-2")
        session.commit()

        active = service.obtenir_active()
        assert active is not None
        assert active.model == "modele-2"

        historique = service.historique(limit=10)
        assert len(historique) == 2
        assert sum(1 for ligne in historique if ligne.actif) == 1
    finally:
        session.close()


def test_desactiver_active_libere_le_runtime(api_session_factory: sessionmaker[Session]) -> None:
    session = _session(api_session_factory)
    try:
        service = AgentRuntimeService(session)
        service.enregistrer(provider="openai", model="modele-1")
        session.commit()

        assert service.desactiver_active() is True
        session.commit()
        assert service.obtenir_active() is None
        assert service.desactiver_active() is False  # idempotent
    finally:
        session.close()


def test_provider_inconnu_refuse_sans_ecrire(api_session_factory: sessionmaker[Session]) -> None:
    session = _session(api_session_factory)
    try:
        with pytest.raises(BusinessValidationError):
            AgentRuntimeService(session).enregistrer(provider="magique", model="m")
        assert AgentRuntimeService(session).obtenir_active() is None
        assert _audits(session) == []
    finally:
        session.close()


def test_endpoint_compatible_exige_base_url(api_session_factory: sessionmaker[Session]) -> None:
    session = _session(api_session_factory)
    try:
        with pytest.raises(BusinessValidationError):
            AgentRuntimeService(session).enregistrer(
                provider="openai_compatible", model="m", base_url=None
            )
    finally:
        session.close()


@pytest.mark.parametrize(
    ("temperature", "max_iterations"),
    [(3.0, 25), (-0.5, 25), (0.0, 0)],
)
def test_bornes_refusees(
    api_session_factory: sessionmaker[Session], temperature: float, max_iterations: int
) -> None:
    session = _session(api_session_factory)
    try:
        with pytest.raises(BusinessValidationError):
            AgentRuntimeService(session).enregistrer(
                provider="openai",
                model="m",
                temperature=temperature,
                max_iterations=max_iterations,
            )
        assert _audits(session) == []
    finally:
        session.close()


# --- Section 2 : manager rechargeable (doubles, sans LLM) ---------------------


class _FakeSession:
    """Session de construction factice : on vérifie seulement qu'elle est fermée."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeAgent:
    """Agent factice : mémorise le runtime reçu (donc le modèle appliqué)."""

    def __init__(self, agent_id: str, etat: dict[str, Any], casse: bool) -> None:
        self.agent_id = agent_id
        self._etat = etat
        self._casse = casse

    def build_agent(self, *, runtime: Any = None, system_prompt_extra: str | None = None) -> Any:
        if self._casse:
            raise RuntimeError(f"construction impossible: {self.agent_id}")
        self._etat["builds"] += 1
        self._etat["modeles"].append(getattr(runtime, "model", None))
        if system_prompt_extra:
            self._etat.setdefault("instructions", []).append(system_prompt_extra)
        self._etat["interactif"].append(getattr(runtime, "interactive", None))
        return {"agent": self.agent_id, "numero": self._etat["builds"]}


def _manager_de_test(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agents: tuple[str, ...] = ("agent_a", "agent_b"),
    casses: tuple[str, ...] = (),
) -> tuple[AgentRuntimeManager, dict[str, Any]]:
    """Manager avec fabriques factices, modèle factice et session factice."""
    etat: dict[str, Any] = {"builds": 0, "modeles": [], "interactif": [], "sessions": []}

    def _fabrique(agent_id: str):
        def _builder(session: Any) -> _FakeAgent:
            etat["sessions"].append(session)
            return _FakeAgent(agent_id, etat, agent_id in casses)

        return _builder

    monkeypatch.setattr(
        "app.agents.manager.is_agent_configured", lambda settings=None: True
    )
    monkeypatch.setattr(
        "app.agents.manager.build_chat_model",
        lambda settings=None, **kw: {"modele": settings.agent_model},
    )
    monkeypatch.setattr("app.infrastructure.database.SessionLocal", _FakeSession)

    manager = AgentRuntimeManager({a: _fabrique(a) for a in agents})
    return manager, etat


def test_reload_construit_puis_remplace_les_graphes(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, etat = _manager_de_test(monkeypatch)

    assert manager.reload() == ("agent_a", "agent_b")
    premier = manager.graph("agent_a")
    sessions_1 = list(etat["sessions"])

    # « Redémarrer le runtime » : reconstruction, sans redémarrer le backend.
    assert manager.reload() == ("agent_a", "agent_b")
    second = manager.graph("agent_a")

    assert premier is not second  # nouveau graphe, même objet manager
    assert etat["builds"] == 4  # 2 agents × 2 rechargements
    assert all(session.closed for session in sessions_1)  # anciens libérés


def test_graph_est_memoise(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))
    assert manager.graph("agent_a") is manager.graph("agent_a")
    assert etat["builds"] == 1


def test_agent_inconnu_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a",))
    with pytest.raises(BusinessValidationError):
        manager.graph("agent_fantome")


def test_graphes_construits_en_mode_interactif(monkeypatch: pytest.MonkeyPatch) -> None:
    """HITL : les graphes exposés doivent permettre les interruptions."""
    manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))
    manager.reload()
    assert etat["interactif"] == [True]


def test_changement_de_modele_a_chaud_atteint_les_graphes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))
    manager.apply_values(
        AgentRuntimeValues(
            provider="openai_compatible",
            model="modele-chaud",
            base_url="https://logfare.ai/v1",
            temperature=0.3,
            max_iterations=7,
            source="base",
        )
    )

    assert etat["modeles"] == [{"modele": "modele-chaud"}]
    assert manager.effective_settings().agent_model == "modele-chaud"
    assert manager.effective_settings().agent_max_iterations == 7
    valeurs = manager.values()
    assert valeurs is not None
    assert valeurs.source == "base"


def test_la_surcharge_ne_mute_pas_le_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    avant = get_settings().agent_model
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a",))
    manager.apply_values(
        AgentRuntimeValues("openai", "modele-teste", None, 0.0, 25, "base")
    )

    assert get_settings().agent_model == avant  # singleton intact
    assert get_settings() is not manager.effective_settings()


def test_erreur_d_un_agent_est_consignee_sans_bloquer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _ = _manager_de_test(
        monkeypatch, agents=("agent_a", "agent_ko"), casses=("agent_ko",)
    )
    construits = manager.reload()

    assert construits == ("agent_a",)  # l'autre agent reste disponible
    dernier = manager.state()["last_error"]
    assert dernier is not None
    assert "agent_ko" in str(dernier)


def test_reload_sans_agent_configure_n_echoue_pas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans modèle configuré, les graphes sont construits sans LLM (harnais
    résolvant le modèle à l'invocation) : le montage reste opérationnel."""
    manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))
    monkeypatch.setattr(
        "app.agents.manager.is_agent_configured", lambda settings=None: False
    )

    assert manager.reload() == ("agent_a",)
    assert etat["builds"] == 1
    assert etat["modeles"] == [None]  # aucun modèle résolu au montage
    assert manager.state()["configured"] is False


def test_ecouteur_notifie_a_chaque_rechargement(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a",))
    appels: list[int] = []
    ecouteur = lambda: appels.append(1)  # noqa: E731
    manager.add_listener(ecouteur)
    manager.add_listener(ecouteur)  # pas de doublon

    manager.reload()
    manager.reload()
    assert len(appels) == 2


def test_reset_revient_a_la_configuration_env(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a",))
    manager.apply_values(
        AgentRuntimeValues("openai", "modele-base", None, 0.0, 25, "base")
    )
    manager.reset_to_env()

    valeurs = manager.values()
    assert valeurs is None or valeurs.source == "env"


def test_state_n_expose_aucun_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a",))
    manager.apply_values(AgentRuntimeValues("openai", "m", None, 0.0, 25, "base"))
    etat = manager.state()

    assert set(etat) == {"configured", "values", "agents", "built_agents", "last_error"}
    assert set(etat["values"]) == {
        "provider",
        "model",
        "base_url",
        "temperature",
        "max_iterations",
        "source",
        # Un booléen : « une clé administrée existe ». La valeur, elle, ne sort
        # jamais du backend (chiffrée au repos, jamais relue).
        "cle_administree",
    }
    assert "api_key" not in repr(etat)
    assert "secret" not in repr(etat).lower()


def test_restore_from_db_reapplique_la_derniere_decision(
    monkeypatch: pytest.MonkeyPatch, api_session_factory: sessionmaker[Session]
) -> None:
    """Le système mémorise la configuration et la recharge au démarrage."""
    session = _session(api_session_factory)
    try:
        AgentRuntimeService(session, actor_id="admin-1").enregistrer(
            provider="openai",
            model="modele-memorise",
            temperature=0.1,
            max_iterations=9,
        )
        session.commit()

        manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))
        assert manager.restore_from_db(session) is True

        valeurs = manager.values()
        assert valeurs is not None
        assert valeurs.model == "modele-memorise"
        assert valeurs.source == "base"
        assert etat["modeles"] == [{"modele": "modele-memorise"}]
    finally:
        session.close()


def test_restore_from_db_sans_configuration(
    monkeypatch: pytest.MonkeyPatch, api_session_factory: sessionmaker[Session]
) -> None:
    session = _session(api_session_factory)
    try:
        manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))
        assert manager.restore_from_db(session) is False
        assert etat["builds"] == 0
    finally:
        session.close()


# --- Section 3 : exposition AG-UI remontée à chaud ---------------------------


class _FauxAgui:
    """Double d'``ag-ui-langgraph`` : ajoute un routeur comme le vrai paquet.

    Le vrai ``add_langgraph_fastapi_endpoint`` monte des routes dans l'app ; on
    reproduit ce comportement via ``include_router`` — ce qui produit, avec
    FastAPI 0.14x, un ``_IncludedRouter`` **sans** attribut ``path``. Le test
    prouve donc que le démontage fonctionne indépendamment du chemin.
    """

    @staticmethod
    def add_langgraph_fastapi_endpoint(app: FastAPI, graph: Any, path: str) -> None:
        sous = APIRouter()
        sous.post(path)(lambda: {"ok": True})
        app.include_router(sous)


def _app_de_test() -> FastAPI:
    from app.api import agui as module_agui
    from app.api.deps import get_current_user

    app = FastAPI()
    app.include_router(module_agui.router, prefix="/api/v1")
    # Catalogue exposé sans session réelle : aucune dépendance DB ici.
    app.dependency_overrides[get_current_user] = _admin
    return app


def _ids_catalogue() -> tuple[str, ...]:
    from app.agents.catalogue import AGENT_DEFINITIONS

    return tuple(d.agent_id for d in AGENT_DEFINITIONS)


def _brancher_agui(monkeypatch: pytest.MonkeyPatch, app: FastAPI, manager: Any) -> Any:
    from app.api import agui as module_agui

    monkeypatch.setattr(module_agui, "_importer_agui", lambda: _FauxAgui)
    monkeypatch.setattr(module_agui, "get_runtime_manager", lambda: manager)
    return module_agui


def test_montage_puis_demontage_agui(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    app = _app_de_test()
    avant = len(app.router.routes)
    ids = _ids_catalogue()
    manager, _ = _manager_de_test(monkeypatch, agents=ids)
    module_agui = _brancher_agui(monkeypatch, app, manager)

    module_agui.monter_endpoints_agui(app, get_settings())

    assert len(app.state.agui_routes) == len(ids)
    assert len(app.router.routes) > avant

    retires = module_agui.demonter_endpoints_agui(app, "/api/v1")

    assert retires >= len(ids)
    assert len(app.router.routes) == avant  # aucun résidu laissé derrière
    assert app.state.agui_routes == []


def test_remontage_ne_duplique_pas_les_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    app = _app_de_test()
    manager, _ = _manager_de_test(monkeypatch, agents=_ids_catalogue())
    module_agui = _brancher_agui(monkeypatch, app, manager)

    module_agui.monter_endpoints_agui(app, get_settings())
    apres_premier = len(app.router.routes)

    # Simule un rechargement du runtime : le manager notifie, les endpoints
    # doivent être remplacés, pas empilés.
    manager.reload()

    assert len(app.router.routes) == apres_premier


def test_catalogue_survit_au_remontage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    app = _app_de_test()
    ids = _ids_catalogue()
    manager, _ = _manager_de_test(monkeypatch, agents=ids)
    module_agui = _brancher_agui(monkeypatch, app, manager)

    module_agui.monter_endpoints_agui(app, get_settings())
    module_agui.demonter_endpoints_agui(app, "/api/v1")

    client = TestClient(app)
    resp = client.get("/api/v1/agui/")
    assert resp.status_code == 200  # le catalogue n'est jamais démonté
    assert len(resp.json()) == len(ids)


def test_montage_meme_sans_modele_configure(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'absence de modèle ne bloque ni le lifespan ni le montage AG-UI."""
    from app.core.config import get_settings

    app = _app_de_test()
    avant = len(app.router.routes)
    ids = _ids_catalogue()
    manager, _ = _manager_de_test(monkeypatch, agents=ids)
    monkeypatch.setattr(
        "app.agents.manager.is_agent_configured", lambda settings=None: False
    )
    module_agui = _brancher_agui(monkeypatch, app, manager)

    module_agui.monter_endpoints_agui(app, get_settings())

    assert len(app.state.agui_routes) == len(ids)
    assert len(app.router.routes) > avant


# --- Section 4 : API d'administration ----------------------------------------


def _admin() -> Utilisateur:
    from app.domain.enums import StatutUtilisateur

    return Utilisateur(
        id=uuid4(),
        email="admin@carso.local",
        nom="Admin",
        prenom="Carso",
        password_hash="!",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )


def _brancher_admin(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, manager: Any
) -> Utilisateur:
    """Force un administrateur et branche le manager de test sur les routes."""
    from app.api.deps import require_admin
    from app.api.routes import agents as routes_agents

    admin = _admin()
    api_client.app.dependency_overrides[require_admin] = lambda: admin
    monkeypatch.setattr(routes_agents, "get_runtime_manager", lambda: manager)
    return admin


def test_api_catalogue_agents(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/agents/catalogue")
    assert resp.status_code == 200, resp.text

    par_id = {item["agent_id"]: item for item in resp.json()}
    assert len(par_id) == 6
    assert "agent_analyseur_appel_proposition" not in par_id
    assert "get_lot" in par_id["agent_generateur_offre"]["tools"]
    assert par_id["agent_generateur_offre"]["collaboration"] == ["agent_rh"]
    assert par_id["agent_generaliste_readonly"]["approval_required"] == []

    # L'agent personnel est exposé : il ne reçoit aucun outil de mutation.
    assert par_id["agent_personnel"]["collaboration"] == []
    for outil in par_id["agent_personnel"]["tools"]:
        assert not outil.startswith(("create_", "update_", "delete_", "approve_"))

    # Un agent en lecture seule ne doit exposer aucun tool de mutation.
    for outil in par_id["agent_generaliste_readonly"]["tools"]:
        assert not outil.startswith(("create_", "update_", "delete_", "approve_"))


def test_catalogue_expose_scenarios_hitl(api_client: TestClient) -> None:
    """Contrat HITL : scénarios et réponses prédéfinies exposés au catalogue.

    L'interface construit le questionnaire à partir de ces données — aucune
    réponse prédéfinie n'est inventée côté client. La réponse personnalisée
    (texte libre) s'ajoute toujours et n'est pas déclarée.
    """
    resp = api_client.get("/api/v1/agents/catalogue")
    assert resp.status_code == 200, resp.text

    par_id = {item["agent_id"]: item for item in resp.json()}

    generateur = par_id["agent_generateur_offre"]
    scenarios = generateur["hitl_scenarios"]
    assert {s["id"] for s in scenarios} == {
        "reception_appel",
        "validation_extraction",
        "creation_brouillon_offre",
        "creation_mission",
    }
    extraction = next(s for s in scenarios if s["id"] == "validation_extraction")
    assert extraction["titre"]
    assert extraction["contexte"]
    assert extraction["action"]
    valeurs = [r["valeur"] for r in extraction["reponses"]]
    assert "annuler" in valeurs  # tout scénario propose une sortie sans effet
    for reponse in extraction["reponses"]:
        assert reponse["libelle"] and reponse["description"]

    # Chaque agent exposé déclare au moins un scénario avec des réponses.
    for agent_id, item in par_id.items():
        assert item["hitl_scenarios"], f"{agent_id} sans scénario HITL"
        for scenario in item["hitl_scenarios"]:
            assert scenario["reponses"], f"{agent_id}/{scenario['id']} sans réponses"

    # Plus aucun sous-agent interne : le catalogue ne liste que des agents UI.
    assert "analyseur" not in "".join(par_id)


def test_api_runtime_reserve_a_l_administrateur(api_client: TestClient) -> None:
    """La configuration des agents n'est jamais accessible à un collaborateur."""
    assert api_client.get("/api/v1/agents/runtime").status_code == 403
    assert api_client.get("/api/v1/agents/runtime/historique").status_code == 403
    assert api_client.post("/api/v1/agents/runtime/reload").status_code == 403
    assert api_client.post("/api/v1/agents/runtime/reset").status_code == 403
    refus = api_client.put(
        "/api/v1/agents/runtime", json={"provider": "openai", "model": "m"}
    )
    assert refus.status_code == 403


def test_api_put_runtime_persiste_et_applique(
    api_client: TestClient,
    api_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, etat = _manager_de_test(monkeypatch, agents=("agent_rh",))
    admin = _brancher_admin(api_client, monkeypatch, manager)

    resp = api_client.put(
        "/api/v1/agents/runtime",
        json={
            "provider": "openai_compatible",
            "model": "modele-test",
            "base_url": "https://logfare.ai/v1",
            "temperature": 0.4,
            "max_iterations": 11,
        },
    )
    assert resp.status_code == 200, resp.text
    corps = resp.json()
    assert corps["configured"] is True
    assert corps["values"]["model"] == "modele-test"
    assert corps["values"]["source"] == "base"
    assert corps["built_agents"] == ["agent_rh"]

    # « à chaud » : le runtime a reconstruit ses graphes avec le nouveau modèle.
    assert etat["modeles"] == [{"modele": "modele-test"}]

    session = _session(api_session_factory)
    try:
        active = AgentRuntimeService(session).obtenir_active()
        assert active is not None
        assert active.model == "modele-test"
        assert active.modifie_par == str(admin.id)
        assert active.actif is True
    finally:
        session.close()


def test_api_put_runtime_configuration_invalide_422(
    api_client: TestClient,
    api_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une valeur fournie par l'admin est une erreur de requête, pas une panne."""
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_rh",))
    _brancher_admin(api_client, monkeypatch, manager)

    resp = api_client.put(
        "/api/v1/agents/runtime",
        json={"provider": "openai_compatible", "model": "m"},  # base_url manquante
    )
    assert resp.status_code == 422, resp.text

    session = _session(api_session_factory)
    try:
        assert AgentRuntimeService(session).obtenir_active() is None  # rien écrit
    finally:
        session.close()


def test_api_reload_est_audite(
    api_client: TestClient,
    api_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, etat = _manager_de_test(monkeypatch, agents=("agent_rh",))
    _brancher_admin(api_client, monkeypatch, manager)

    resp = api_client.post("/api/v1/agents/runtime/reload")
    assert resp.status_code == 200, resp.text
    assert etat["builds"] == 1  # runtime redémarré sans redémarrer le backend

    session = _session(api_session_factory)
    try:
        actions = [e.action for e in session.scalars(select(AuditEvent)).all()]
        assert ActionAudit.CONFIGURATION_AGENT_RECHARGEE.value in actions
    finally:
        session.close()


def test_api_reset_revient_a_la_configuration_env(
    api_client: TestClient,
    api_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_rh",))
    _brancher_admin(api_client, monkeypatch, manager)

    api_client.put(
        "/api/v1/agents/runtime",
        json={"provider": "openai", "model": "modele-jetable"},
    )
    resp = api_client.post("/api/v1/agents/runtime/reset")
    assert resp.status_code == 200, resp.text
    assert resp.json()["values"]["source"] == "env"

    session = _session(api_session_factory)
    try:
        assert AgentRuntimeService(session).obtenir_active() is None
    finally:
        session.close()


def test_api_historique_expose_les_versions(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_rh",))
    _brancher_admin(api_client, monkeypatch, manager)

    api_client.put("/api/v1/agents/runtime", json={"provider": "openai", "model": "v1"})
    api_client.put("/api/v1/agents/runtime", json={"provider": "openai", "model": "v2"})

    resp = api_client.get("/api/v1/agents/runtime/historique")
    assert resp.status_code == 200, resp.text
    modeles = [item["model"] for item in resp.json()]
    assert sorted(modeles) == ["v1", "v2"]
    assert sum(1 for item in resp.json() if item["actif"]) == 1


def test_api_etat_runtime_expose_les_agents(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a", "agent_b"))
    _brancher_admin(api_client, monkeypatch, manager)

    resp = api_client.get("/api/v1/agents/runtime")
    assert resp.status_code == 200, resp.text
    corps = resp.json()
    assert corps["configured"] is True
    assert corps["agents"] == ["agent_a", "agent_b"]
    assert corps["built_agents"] == []  # rien n'est construit tant qu'on ne recharge pas
    assert set(corps["values"]) == {
        "provider",
        "model",
        "base_url",
        "temperature",
        "max_iterations",
        "source",
        "cle_administree",
    }


def test_les_instructions_administrees_atteignent_le_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une consigne posée dans Paramètres est appliquée au prompt système.

    Régression (incrément 11) : le manager résolvait la surcharge APRÈS avoir
    construit l'agent et ne lui transmettait jamais les
    ``instructions_supplementaires`` — le réglage restait sans effet.
    ``_effectif_pour`` est désormais résolu avant le builder et ses instructions
    partent au harnais via ``system_prompt_extra``.
    """
    from app.domain.agent_runtime import SurchargeAgent

    manager, etat = _manager_de_test(monkeypatch, agents=("agent_a",))

    surcharge = SurchargeAgent(
        agent_id="agent_a",
        outils_desactives=[],
        instructions_supplementaires="Toujours répondre en une phrase.",
        skills_ajoutes=[],
        actif=True,
    )

    class _RepoSurcharge:
        def __init__(self, session: Any) -> None:
            _ = session

        def get_par_agent(self, agent_id: str) -> SurchargeAgent | None:
            return surcharge if agent_id == "agent_a" else None

    class _RepoMcp:
        def __init__(self, session: Any) -> None:
            _ = session

        def lister_pour_agent(
            self, agent_id: str, *, utilisateur_id: object = None
        ) -> list[Any]:
            _ = agent_id
            return []

    import app.infrastructure.repositories as repos

    monkeypatch.setattr(repos, "SurchargeAgentRepository", _RepoSurcharge)
    monkeypatch.setattr(repos, "ServeurMcpRepository", _RepoMcp)

    def _contrat_factice(agent_id: str) -> Any:
        if agent_id != "agent_a":
            raise BusinessValidationError(f"Agent inconnu : {agent_id!r}")
        return AgentDefinition(
            agent_id="agent_a",
            display_name="Agent A",
            description="Agent de test",
            prompt="Tu es un agent de test.",
            tools=(),
            approval_required=(),
            collaboration=(),
            policy=PermissionPolicy(allowed_capabilities=frozenset()),
            skills=(),
        )

    import app.application.services.parametres_service as parametres_service

    monkeypatch.setattr(
        parametres_service, "definition_du_contrat", _contrat_factice
    )

    manager.reload()
    _ = manager.graph("agent_a")

    instructions = etat.get("instructions") or []
    assert instructions == ["Toujours répondre en une phrase."]


def test_outil_desactive_le_agent_monte_quand_meme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Désactiver un outil dans Paramètres ne doit pas empêcher l'agent de monter.

    Régression (scénario réel) : ``generer_document`` désactivé dans la surcharge
    → le harnais levait « Tool 'generer_document' non déclaré dans le contrat »
    car le builder fournit toujours tous les tools construits. Le tool retiré
    par la surcharge est désormais **écarté** au montage, pas une erreur.
    """
    from dataclasses import replace

    from app.agents.base import AgentDefinition
    from app.domain.agent_runtime import SurchargeAgent
    from app.tools.permissions import PermissionPolicy

    manager, _ = _manager_de_test(monkeypatch, agents=("agent_a",))

    contrat = AgentDefinition(
        agent_id="agent_a",
        display_name="Agent A",
        description="Agent de test",
        prompt="Tu es un agent de test.",
        tools=("outil_conserve", "outil_desactive"),
        approval_required=(),
        collaboration=(),
        policy=PermissionPolicy(allowed_capabilities=frozenset()),
        skills=(),
    )
    surcharge = SurchargeAgent(
        agent_id="agent_a",
        outils_desactives=["outil_desactive"],
        instructions_supplementaires="",
        skills_ajoutes=[],
        actif=True,
    )

    class _RepoSurcharge:
        def __init__(self, session: Any) -> None:
            _ = session

        def get_par_agent(self, agent_id: str) -> SurchargeAgent | None:
            return surcharge if agent_id == "agent_a" else None

    class _RepoMcp:
        def __init__(self, session: Any) -> None:
            _ = session

        def lister_pour_agent(
            self, agent_id: str, *, utilisateur_id: object = None
        ) -> list[Any]:
            _ = agent_id
            return []

    import app.infrastructure.repositories as repos

    monkeypatch.setattr(repos, "SurchargeAgentRepository", _RepoSurcharge)
    monkeypatch.setattr(repos, "ServeurMcpRepository", _RepoMcp)

    def _contrat_factice(agent_id: str) -> Any:
        if agent_id != "agent_a":
            raise BusinessValidationError(f"Agent inconnu : {agent_id!r}")
        return contrat

    import app.application.services.parametres_service as parametres_service

    monkeypatch.setattr(parametres_service, "definition_du_contrat", _contrat_factice)

    # Avant correction : ``manager.graph("agent_a")`` levait une
    # BusinessValidationError côté harnais. Le montage doit réussir.
    manager.reload()
    _ = manager.graph("agent_a")

    # Le contrat effectif porte bien l'outil retiré et l'agent est construit.
    from app.agents.surcharges import definition_effective

    assert "outil_desactive" not in definition_effective(contrat, surcharge).tools
    assert replace(contrat, tools=("outil_conserve",)).tools == ("outil_conserve",)
