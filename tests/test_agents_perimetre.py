"""Agents et périmètre — catalogue filtré et garde d'entrée AG-UI.

Décision validée (03/10, complétée le 10/10) : un formateur n'ouvre que
``agent_assistant_formateur``, ``agent_generaliste_readonly``,
``agent_statistique`` et ``agent_personnel`` (l'agent **du compte**, ouvert à
tout compte actif) ; les autres agents supposent un accès complet au métier. La
garde AG-UI pose le **périmètre du compte** et son **contexte d'exécution**
(identité, rôle, profil, surface d'outils) dans le contexte du run — les tools
appellent les mêmes services que l'API, et le middleware de contexte refuse un
outil hors contrat du rôle.

Les catalogues (``/agui/`` et ``/agents/catalogue``) appliquent la **même**
règle : l'interface ne propose pas ce que le backend refuserait.
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.contexte import contexte_utilisateur_effectif
from app.api.deps import get_current_user
from app.api.permissions import agent_autorise, agents_autorises, preparer_acces_agent
from app.application.perimetre import (
    AGENTS_FORMATEUR,
    PERIMETRE_COMPLET,
    perimetre_effectif,
)
from app.application.trace import demande_par_effectif
from app.core.errors import PermissionDeniedError
from app.domain.enums import (
    RoleUtilisateur,
    StatutAffectation,
    StatutMission,
    StatutUtilisateur,
)
from app.domain.execution import AffectationEquipe, Equipe, Mission
from app.domain.identity import Utilisateur

PRÉFIXE = "/api/v1"


@pytest.fixture
def formateur(api_session_factory: sessionmaker[Session]) -> Iterator[SimpleNamespace]:
    """Formateur lié à une personne du vivier, affecté et approuvé sur une mission."""
    session = api_session_factory()
    try:
        personne = Equipe(nom="Formatrice", prenom="Fara", email="fara@carso.test")
        session.add(personne)
        session.flush()
        mission = Mission(
            organisation_id=uuid4(),
            reference="M-AGENT",
            titre="Mission de la formatrice",
            statut=StatutMission.EN_COURS.value,
        )
        session.add(mission)
        session.flush()
        session.add(
            AffectationEquipe(
                mission_id=mission.id,
                equipe_id=personne.id,
                role_dans_mission="formateur",
                statut=StatutAffectation.APPROUVEE.value,
            )
        )
        utilisateur = Utilisateur(
            email="fara@carso.test",
            nom="Formatrice",
            prenom="Fara",
            password_hash="x",
            role=RoleUtilisateur.FORMATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
            equipe_id=personne.id,
        )
        session.add(utilisateur)
        session.commit()
    finally:
        session.close()
    yield SimpleNamespace(utilisateur=utilisateur, mission=mission, personne=personne)


# --- Règle de rôle ---------------------------------------------------------------


def test_quatre_agents_sont_ouverts_aux_comptes_restreints() -> None:
    assert AGENTS_FORMATEUR == {
        "agent_assistant_formateur",
        "agent_generaliste_readonly",
        "agent_statistique",
        # L'agent personnel du compte : ouvert à tout compte actif, il ne lit
        # que le périmètre du compte (le filtrage est porté par les services).
        "agent_personnel",
    }
    for agent_id, definition in (
        (d.agent_id, d) for d in AGENT_DEFINITIONS
    ):
        assert agent_autorise(RoleUtilisateur.COLLABORATEUR.value, agent_id)
        assert agent_autorise(RoleUtilisateur.ADMINISTRATEUR.value, agent_id)
        attendu = agent_id in AGENTS_FORMATEUR
        assert agent_autorise(RoleUtilisateur.FORMATEUR.value, agent_id) is attendu
        assert definition.agent_id  # jamais une définition vide


def test_filtrage_du_catalogue_par_role() -> None:
    complet = agents_autorises(
        Utilisateur(
            email="c@carso.test", nom="C", prenom="C", password_hash="x",
            role=RoleUtilisateur.COLLABORATEUR.value,
        ),
        AGENT_DEFINITIONS,
    )
    assert tuple(d.agent_id for d in complet) == tuple(
        d.agent_id for d in AGENT_DEFINITIONS
    )

    restreint = agents_autorises(
        Utilisateur(
            email="f@carso.test", nom="F", prenom="F", password_hash="x",
            role=RoleUtilisateur.FORMATEUR.value,
        ),
        AGENT_DEFINITIONS,
    )
    assert {d.agent_id for d in restreint} == AGENTS_FORMATEUR


# --- Garde de l'endpoint AG-UI ---------------------------------------------------


def _poser_la_garde(
    agent_id: str,
    utilisateur: Utilisateur,
    session_factory: sessionmaker[Session],
) -> tuple[object, str | None]:
    """Exécute la garde dans une boucle isolée et rend ce qu'elle a posé."""

    async def _appel() -> tuple[object, str | None]:
        session = session_factory()
        try:
            await preparer_acces_agent(agent_id, utilisateur, session)
            return perimetre_effectif(), demande_par_effectif()
        finally:
            session.close()

    return asyncio.run(_appel())


def test_garde_refuse_un_agent_hors_contrat(
    formateur: SimpleNamespace, api_session_factory: sessionmaker[Session]
) -> None:
    with pytest.raises(PermissionDeniedError) as erreur:
        _poser_la_garde("agent_rh", formateur.utilisateur, api_session_factory)
    assert erreur.value.details["agent_id"] == "agent_rh"
    assert erreur.value.details["role"] == RoleUtilisateur.FORMATEUR.value


def test_garde_pose_le_perimetre_et_le_demandeur(
    formateur: SimpleNamespace, api_session_factory: sessionmaker[Session]
) -> None:
    perimetre, demandeur = _poser_la_garde(
        "agent_assistant_formateur", formateur.utilisateur, api_session_factory
    )
    assert perimetre.est_restreint
    assert perimetre.missions_autorisees == frozenset({formateur.mission.id})
    assert demandeur == str(formateur.utilisateur.id)


def _poser_contextes(
    agent_id: str,
    utilisateur: Utilisateur,
    session_factory: sessionmaker[Session],
) -> tuple[object, object]:
    """Exécute la garde et rend (périmètre, contexte d'exécution) posés."""

    async def _appel() -> tuple[object, object]:
        session = session_factory()
        try:
            await preparer_acces_agent(agent_id, utilisateur, session)
            return perimetre_effectif(), contexte_utilisateur_effectif()
        finally:
            session.close()

    return asyncio.run(_appel())


def test_garde_pose_le_contexte_de_l_utilisateur_connecte(
    formateur: SimpleNamespace, api_session_factory: sessionmaker[Session]
) -> None:
    """Le run connaît l'utilisateur : identité, rôle, profil, surface d'outils.

    C'est le contrat de ``UserContext`` (context schema) : le modèle reçoit un
    bloc de prompt, et le middleware peut refuser un outil hors contrat.
    """
    session = api_session_factory()
    try:
        personne = session.get(Equipe, formateur.personne.id)
        personne.profil = "Formatrice en gestion budgétaire"
        session.commit()
    finally:
        session.close()

    _, contexte = _poser_contextes(
        "agent_statistique", formateur.utilisateur, api_session_factory
    )
    assert contexte is not None
    assert contexte.role == RoleUtilisateur.FORMATEUR.value
    assert contexte.est_restreint
    assert contexte.nom_complet == "Fara Formatrice"
    assert contexte.profil == "Formatrice en gestion budgétaire"
    assert contexte.missions_autorisees == (
        f"{formateur.mission.reference} · {formateur.mission.titre}",
    )
    # Surface d'outils : ceux de ses agents, et rien d'autre.
    assert contexte.peut_executer("query_analytics")
    assert contexte.peut_executer("generer_document")
    assert not contexte.peut_executer("proposer_affectation")
    assert not contexte.peut_executer("analyser_appel")
    assert "agent_rh" not in contexte.agents_ouverts
    # Le périmètre voyage avec le contexte : une seule autorité de données.
    assert contexte.perimetre is not None
    assert contexte.perimetre.missions_autorisees == frozenset({formateur.mission.id})


def test_la_garde_ne_laisse_pas_le_contexte_fuir_hors_du_run(
    formateur: SimpleNamespace, api_session_factory: sessionmaker[Session]
) -> None:
    """Un run = un contexte : rien ne reste posé après le retour de la garde."""
    _poser_contextes("agent_generaliste_readonly", formateur.utilisateur, api_session_factory)
    assert contexte_utilisateur_effectif() is None


def test_garde_acces_complet_pour_l_equipe_interne(
    api_session_factory: sessionmaker[Session],
) -> None:
    collaborateurs = Utilisateur(
        email="col@carso.test",
        nom="Col",
        prenom="Laborateur",
        password_hash="x",
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session = api_session_factory()
    try:
        session.add(collaborateurs)
        session.commit()
    finally:
        session.close()
    perimetre, demandeur = _poser_la_garde(
        "agent_statistique", collaborateurs, api_session_factory
    )
    assert not perimetre.est_restreint
    assert demandeur == str(collaborateurs.id)


def test_le_contexte_de_la_garde_ne_fuit_pas_hors_du_run(
    formateur: SimpleNamespace, api_session_factory: sessionmaker[Session]
) -> None:
    """Un run = un contexte : la garde n'altère pas l'état du processus appelant."""
    _poser_la_garde("agent_generaliste_readonly", formateur.utilisateur, api_session_factory)
    assert perimetre_effectif() is PERIMETRE_COMPLET
    assert demande_par_effectif() is None


# --- Catalogues HTTP -------------------------------------------------------------


@pytest.fixture
def client_formateur(
    api_client: TestClient, formateur: SimpleNamespace
) -> Iterator[TestClient]:
    api_client.app.dependency_overrides[get_current_user] = lambda: formateur.utilisateur
    yield api_client
    api_client.app.dependency_overrides.pop(get_current_user, None)


def test_catalogue_agents_filtre_pour_un_formateur(client_formateur: TestClient) -> None:
    reponse = client_formateur.get(f"{PRÉFIXE}/agents/catalogue")
    assert reponse.status_code == 200, reponse.text
    assert {agent["agent_id"] for agent in reponse.json()} == AGENTS_FORMATEUR


def test_catalogue_agui_filtre_pour_un_formateur(client_formateur: TestClient) -> None:
    reponse = client_formateur.get(f"{PRÉFIXE}/agui/")
    assert reponse.status_code == 200, reponse.text
    assert {agent["agent_id"] for agent in reponse.json()} == AGENTS_FORMATEUR


def test_catalogues_complets_pour_l_equipe_interne(api_client: TestClient) -> None:
    """Le compte lab (collaborateur) voit tout le catalogue — référence."""
    attendus = {definition.agent_id for definition in AGENT_DEFINITIONS}
    assert {a["agent_id"] for a in api_client.get(f"{PRÉFIXE}/agents/catalogue").json()} == attendus
    assert {a["agent_id"] for a in api_client.get(f"{PRÉFIXE}/agui/").json()} == attendus


def test_endpoints_agui_portent_la_garde(api_client: TestClient) -> None:
    """Chaque agent monté est protégé par la même garde que les catalogues."""
    trouves = {
        route.path: route
        for route in api_client.app.routes
        if isinstance(route, APIRoute) and route.path.startswith(f"{PRÉFIXE}/agui/")
    }
    agents = {p: r for p, r in trouves.items() if not p.endswith("/health")}
    if not agents:  # pragma: no cover - extra ag-ui-langgraph absent
        pytest.skip("ag-ui-langgraph non installé : aucun endpoint monté")
    for chemin, route in agents.items():
        if "POST" not in (route.methods or set()):
            continue
        gardes = {
            getattr(d.call, "__name__", "") for d in route.dependant.dependencies if d.call
        }
        assert any(nom.startswith("garde_agui_") for nom in gardes), chemin


# --- Non-régression : le path AG-UI est un littéral -------------------------------
#
# Les endpoints sont montés sur ``/agui/agent_rh`` — **pas** ``/agui/{agent_id}``.
# Une garde qui déclare ``agent_id`` comme paramètre est donc interprétée par
# FastAPI comme un **paramètre de requête obligatoire** : le chat échoue en 422
# (« Field required : query.agent_id ») avant même d'atteindre le run. Ces
# tests rejouent la requête HTTP réelle ; ils s'arrêtent à la validation, donc
# **aucun modèle n'est appelé**.


def test_run_agui_ne_demande_pas_de_parametre_de_requete(api_client: TestClient) -> None:
    """Un corps vide doit échouer sur le **corps**, jamais sur un paramètre d'URL."""
    reponse = api_client.post(f"{PRÉFIXE}/agui/agent_generaliste_readonly", json={})
    if reponse.status_code == 422:
        manquants = reponse.json().get("detail", [])
        assert manquants, "422 sans détail : garde malformée"
        for constat in manquants:
            assert "query" not in constat["loc"], (
                "la garde AG-UI demande un paramètre de requête alors que le "
                f"path est un littéral : {constat['loc']}"
            )
    else:
        # 200/403/500 : la garde s'exécute, la validation du corps est franchie.
        assert reponse.status_code != 404


def test_run_agui_refuse_un_agent_hors_contrat_du_role(
    client_formateur: TestClient,
) -> None:
    """Formateur + agent interdit : 403 **avant** toute validation du corps."""
    reponse = client_formateur.post(f"{PRÉFIXE}/agui/agent_rh", json={})
    assert reponse.status_code == 403, reponse.text
    assert reponse.json()["error"]["details"]["agent_id"] == "agent_rh"
