"""Tests API — proposition AgentRH via route HITL (incrément 4).

Le classement est déterministe, calculé sur les données enregistrées
uniquement ; toute proposition naît en ``proposee`` et n'entre en vigueur
qu'après approbation humaine explicite (règle 6 [C]).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import StatutAffectation, StatutMission
from app.domain.execution import Equipe, Mission
from app.domain.organization import Organisation
from tests.conftest import seed

PRÉFIXE = "/api/v1"


def _seed_mission(factory: sessionmaker[Session], reference: str = "M-RH-API-1") -> Mission:
    org = Organisation(nom="Client RH API")
    seed(factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference=reference,
        titre="Mission RH API",
        statut=StatutMission.PLANIFIE.value,
    )
    seed(factory, mission)
    return mission


def _test_route(
    api_client: TestClient, mission: Mission, role: str = "formateur"
) -> dict:
    reponse = api_client.post(
        f"{PRÉFIXE}/missions/{mission.id}/proposition-agent-rh",
        json={"role_dans_mission": role},
    )
    assert reponse.status_code == 202, reponse.text
    return reponse.json()


def test_proposition_agent_rh_classe_le_vivier(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    mission = _seed_mission(api_session_factory)
    formateur = Equipe(nom="Amrani", prenom="Sara", profil="formateur senior")
    logisticien = Equipe(nom="Benali", prenom="Karim", profil="logistique")
    seed(api_session_factory, formateur, logisticien)

    corps = _test_route(api_client, mission)

    # Classement : le formateur (rôle dans profil) devant le logisticien.
    assert corps["candidats"][0]["nom"] == "Amrani"
    assert corps["candidats"][0]["criteres"]["role_dans_profil"] is True
    assert corps["candidats"][1]["criteres"]["role_dans_profil"] is False
    assert corps["candidats"][0]["score"] > corps["candidats"][1]["score"]

    # Le meilleur candidat reçoit une proposition en attente de décision.
    assert len(corps["propositions"]) == 1
    proposition = corps["propositions"][0]
    assert proposition["equipe_id"] == str(formateur.id)
    assert proposition["statut"] == StatutAffectation.PROPOSEE.value
    assert "attente" in corps["message"]


def test_proposition_agent_rh_pas_de_doublon_en_attente(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    mission = _seed_mission(api_session_factory)
    seed(api_session_factory, Equipe(nom="Haddad", prenom="Omar", profil="formateur"))

    premier = _test_route(api_client, mission)
    second = _test_route(api_client, mission)

    assert len(premier["propositions"]) == 1
    assert second["propositions"] == []
    assert "déjà" in second["message"]


def test_proposition_agent_rh_vivier_vide(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    mission = _seed_mission(api_session_factory)

    corps = _test_route(api_client, mission)

    assert corps["candidats"] == []
    assert corps["propositions"] == []
    assert "Aucun membre" in corps["message"]


def test_proposition_agent_rh_ciblage_equipe(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    mission = _seed_mission(api_session_factory)
    cible = Equipe(nom="Ndiaye", prenom="Fatou", profil="chargée de mission")
    autre = Equipe(nom="Traoré", prenom="Awa", profil="formateur")
    seed(api_session_factory, cible, autre)

    reponse = api_client.post(
        f"{PRÉFIXE}/missions/{mission.id}/proposition-agent-rh",
        json={"role_dans_mission": "chargée de mission", "equipe_id": str(cible.id)},
    )
    assert reponse.status_code == 202, reponse.text
    corps = reponse.json()

    assert [c["nom"] for c in corps["candidats"]] == ["Ndiaye"]
    assert corps["propositions"][0]["equipe_id"] == str(cible.id)


def test_proposition_agent_rh_mission_cloturee_409(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    org = Organisation(nom="Client RH cloture")
    seed(api_session_factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-RH-API-2",
        titre="Mission close",
        statut=StatutMission.CLOTURE.value,
    )
    seed(api_session_factory, mission)
    seed(api_session_factory, Equipe(nom="Diallo", prenom="Ali", profil="formateur"))

    reponse = api_client.post(
        f"{PRÉFIXE}/missions/{mission.id}/proposition-agent-rh",
        json={"role_dans_mission": "formateur"},
    )

    assert reponse.status_code == 409


def test_proposition_puis_approbation_hitl_complet(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    mission = _seed_mission(api_session_factory)
    seed(api_session_factory, Equipe(nom="Fofana", prenom="Léa", profil="formateur"))

    corps = _test_route(api_client, mission)
    affectation_id = corps["propositions"][0]["id"]

    approbation = api_client.post(
        f"{PRÉFIXE}/affectations/{affectation_id}/approbation",
        json={"decision": "approuve", "reason": "Profil conforme, validé par le chef."},
    )
    assert approbation.status_code == 200, approbation.text
    assert approbation.json()["statut"] == StatutAffectation.APPROUVEE.value

    # Le parcours de bout en bout est visible dans la liste de la mission.
    liste = api_client.get(f"{PRÉFIXE}/missions/{mission.id}/affectations")
    assert liste.status_code == 200
    statuts = {a["statut"] for a in liste.json()["affectations"]}
    assert statuts == {StatutAffectation.APPROUVEE.value}
