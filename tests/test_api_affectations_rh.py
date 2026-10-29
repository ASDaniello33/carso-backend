"""Tests API — proposition AgentRH via route HITL (incrément 4).

Le classement est déterministe, calculé sur les données enregistrées
uniquement ; toute proposition naît en ``proposee`` et n'entre en vigueur
qu'après approbation humaine explicite (règle 6 [C]).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from datetime import date

from app.domain.enums import SourceAffectation, StatutAffectation, StatutMission
from app.domain.execution import AffectationEquipe, Equipe, Mission
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


def test_proposition_agent_rh_ecarte_une_personne_en_conflit(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Le classement applique la règle réelle : le n°1 indisponible est écarté.

    Sans cette règle, la route proposait le meilleur score même déjà approuvé
    ailleurs sur des dates qui chevauchent — proposition refusée à coup sûr par
    le service (audit AgentRH du 28/10).
    """
    org = Organisation(nom="Client RH conflit")
    seed(api_session_factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-RH-API-CONFLIT",
        titre="Mission à staffer",
        statut=StatutMission.PLANIFIE.value,
        date_debut=date(2026, 11, 2),
        date_fin=date(2026, 11, 6),
    )
    occupante = Mission(
        organisation_id=org.id,
        reference="M-RH-API-OCCUPEE",
        titre="Mission qui bloque",
        statut=StatutMission.PLANIFIE.value,
        date_debut=date(2026, 11, 4),
        date_fin=date(2026, 11, 9),
    )
    formateur = Equipe(nom="Amrani", prenom="Sara", profil="formateur senior")
    logisticien = Equipe(nom="Benali", prenom="Karim", profil="logistique")
    seed(api_session_factory, mission, occupante, formateur, logisticien)
    seed(
        api_session_factory,
        AffectationEquipe(
            mission_id=occupante.id,
            equipe_id=formateur.id,
            role_dans_mission="formateur",
            statut=StatutAffectation.APPROUVEE.value,
            source_affectation=SourceAffectation.MANUAL.value,
        ),
    )

    corps = _test_route(api_client, mission)

    # Le meilleur score (formateur, rôle dans le profil) est rétrogradé :
    # le classement place d'abord les personnes disponibles.
    assert [c["nom"] for c in corps["candidats"]] == ["Benali", "Amrani"]
    disponible, ecartee = corps["candidats"]
    assert disponible["disponible"] is True and disponible["conflits"] == []
    assert ecartee["disponible"] is False
    assert "M-RH-API-OCCUPEE" in ecartee["conflits"][0]
    assert ecartee["score"] > disponible["score"]  # meilleur score, mais bloquée

    # La proposition va au meilleur candidat **disponible**.
    assert len(corps["propositions"]) == 1
    assert corps["propositions"][0]["equipe_id"] == str(logisticien.id)


def test_proposition_agent_rh_aucun_disponible_ne_propose_rien(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Vivier entièrement en conflit : aucune proposition, et le message le dit."""
    org = Organisation(nom="Client RH complet")
    seed(api_session_factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-RH-API-COMPLET",
        titre="Mission à staffer",
        statut=StatutMission.PLANIFIE.value,
        date_debut=date(2026, 11, 2),
        date_fin=date(2026, 11, 6),
    )
    occupante = Mission(
        organisation_id=org.id,
        reference="M-RH-API-BLOQUANTE",
        titre="Mission qui bloque",
        statut=StatutMission.PLANIFIE.value,
        date_debut=date(2026, 11, 5),
        date_fin=date(2026, 11, 7),
    )
    seule = Equipe(nom="Solo", prenom="Sam", profil="formateur")
    seed(api_session_factory, mission, occupante, seule)
    seed(
        api_session_factory,
        AffectationEquipe(
            mission_id=occupante.id,
            equipe_id=seule.id,
            role_dans_mission="formateur",
            statut=StatutAffectation.APPROUVEE.value,
            source_affectation=SourceAffectation.MANUAL.value,
        ),
    )

    corps = _test_route(api_client, mission)

    assert corps["propositions"] == []
    assert corps["candidats"][0]["disponible"] is False
    assert "Aucun membre du vivier n'est disponible" in corps["message"]


def test_disponibilites_lues_par_la_carte(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """La carte lit la règle au serveur : plus besoin de croire le modèle."""
    org = Organisation(nom="Client RH dispo")
    seed(api_session_factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-RH-API-DISPO",
        titre="Mission à staffer",
        statut=StatutMission.PLANIFIE.value,
        date_debut=date(2026, 11, 2),
        date_fin=date(2026, 11, 6),
    )
    occupante = Mission(
        organisation_id=org.id,
        reference="M-RH-API-BLOC",
        titre="Mission qui bloque",
        statut=StatutMission.PLANIFIE.value,
        date_debut=date(2026, 11, 4),
        date_fin=date(2026, 11, 9),
    )
    prise = Equipe(nom="Amrani", prenom="Sara", profil="formateur")
    libre = Equipe(nom="Benali", prenom="Karim", profil="formateur")
    seed(api_session_factory, mission, occupante, prise, libre)
    seed(
        api_session_factory,
        AffectationEquipe(
            mission_id=occupante.id,
            equipe_id=prise.id,
            role_dans_mission="formateur",
            statut=StatutAffectation.APPROUVEE.value,
            source_affectation=SourceAffectation.MANUAL.value,
        ),
    )

    reponse = api_client.get(
        f"{PRÉFIXE}/missions/{mission.id}/disponibilites",
        params={"equipe_ids": f"{prise.id},{libre.id}"},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["evaluable"] is True
    par_personne = {p["equipe_id"]: p for p in corps["personnes"]}
    assert par_personne[str(prise.id)]["disponible"] is False
    conflit = par_personne[str(prise.id)]["conflits"][0]
    assert conflit["reference"] == "M-RH-API-BLOC"
    assert conflit["titre"] == "Mission qui bloque"
    assert conflit["date_debut"] == "2026-11-04"
    assert conflit["date_fin"] == "2026-11-09"
    assert par_personne[str(libre.id)]["disponible"] is True
    assert par_personne[str(libre.id)]["conflits"] == []


def test_disponibilites_mission_sans_dates_non_evaluable(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Sans dates, la règle ne s'applique pas : rien n'est déclaré indisponible."""
    mission = _seed_mission(api_session_factory, reference="M-RH-API-SANS-DATES")
    personne = Equipe(nom="Solo", prenom="Sam", profil="formateur")
    seed(api_session_factory, personne)

    reponse = api_client.get(
        f"{PRÉFIXE}/missions/{mission.id}/disponibilites",
        params={"equipe_ids": str(personne.id)},
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["evaluable"] is False
    assert corps["personnes"] == []


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
