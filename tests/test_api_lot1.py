"""Lot C1 — tests API des nouveaux endpoints (session override SQLite).

CRUD organisations/équipes/bénéficiaires, lots sous appel, missions, sessions
et participations. Vérifie les codes HTTP (201/404/409) et la persistance.
"""

from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import StatutMission
from app.domain.execution import Mission
from app.domain.organization import Appel, Organisation
from tests.conftest import seed


def test_organisation_crud(api_client: TestClient, api_session_factory) -> None:
    cree = api_client.post("/api/v1/organisations/", json={"nom": "Acme"})
    assert cree.status_code == 201, cree.text
    org_id = cree.json()["id"]
    assert cree.json()["nom"] == "Acme"

    lu = api_client.get(f"/api/v1/organisations/{org_id}")
    assert lu.status_code == 200
    modifie = api_client.patch(f"/api/v1/organisations/{org_id}", json={"telephone": "0612345678"})
    assert modifie.status_code == 200
    assert modifie.json()["telephone"] == "0612345678"

    absente = api_client.get(f"/api/v1/organisations/{uuid4()}")
    assert absente.status_code == 404

    liste = api_client.get("/api/v1/organisations/")
    assert liste.status_code == 200
    assert len(liste.json()) == 1


def test_organisation_sans_nom_422(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/organisations/", json={"nom": ""})
    assert resp.status_code == 422


def test_equipe_crud(api_client: TestClient, api_session_factory) -> None:
    cree = api_client.post(
        "/api/v1/equipes/",
        json={"nom": "Ben", "prenom": "Ali", "profil": "Formateur"},
    )
    assert cree.status_code == 201, cree.text
    equipe_id = cree.json()["id"]

    assert api_client.get(f"/api/v1/equipes/{equipe_id}").status_code == 200
    # Une personne créée sans statut est active (StatutEquipe, 20/09) ; le
    # service refuse toute modification sans effet, le PATCH porte donc un
    # champ qui change réellement.
    assert cree.json()["statut"] == "actif"
    modifie = api_client.patch(
        f"/api/v1/equipes/{equipe_id}", json={"profil": "Formateur principal"}
    )
    assert modifie.json()["profil"] == "Formateur principal"
    assert api_client.get(f"/api/v1/equipes/{uuid4()}").status_code == 404


def _seed_appel(factory: sessionmaker[Session]) -> Appel:
    org = Organisation(nom="Client Lots")
    seed(factory, org)
    appel = Appel(organisation_id=org.id, reference="AO-LOT1-API", titre="Appel lots")
    seed(factory, appel)
    return appel


def test_lots_crud(api_client: TestClient, api_session_factory) -> None:
    appel = _seed_appel(api_session_factory)

    cree = api_client.post(
        f"/api/v1/appels/{appel.id}/lots/",
        json={"numero": "1", "titre": "Lot 1"},
    )
    assert cree.status_code == 201, cree.text
    lot_id = cree.json()["id"]

    doublon = api_client.post(
        f"/api/v1/appels/{appel.id}/lots/",
        json={"numero": "1", "titre": "Doublon"},
    )
    assert doublon.status_code == 409

    liste = api_client.get(f"/api/v1/appels/{appel.id}/lots/")
    assert liste.status_code == 200
    assert len(liste.json()) == 1

    modifie = api_client.patch(
        f"/api/v1/appels/{appel.id}/lots/{lot_id}",
        json={"titre": "Lot 1 bis"},
    )
    assert modifie.status_code == 200
    assert modifie.json()["titre"] == "Lot 1 bis"

    lot_absent = api_client.get(f"/api/v1/appels/{appel.id}/lots/{uuid4()}")
    appel_absent = api_client.get(f"/api/v1/appels/{uuid4()}/lots/")
    assert lot_absent.status_code == 404
    assert appel_absent.status_code == 404


def test_beneficiaire_crud(api_client: TestClient, api_session_factory) -> None:
    cree = api_client.post(
        "/api/v1/beneficiaires/",
        json={"nom": "Tazi", "prenom": "Sara", "contact": "0612345678"},
    )
    assert cree.status_code == 201, cree.text
    ben_id = cree.json()["id"]

    assert api_client.get(f"/api/v1/beneficiaires/{ben_id}").status_code == 200
    modifie = api_client.patch(
        f"/api/v1/beneficiaires/{ben_id}",
        json={"organisation_origine": "Coopérative X"},
    )
    assert modifie.json()["organisation_origine"] == "Coopérative X"
    assert api_client.get(f"/api/v1/beneficiaires/{uuid4()}").status_code == 404


def _seed_mission(factory: sessionmaker[Session]) -> Mission:
    org = Organisation(nom="Client Mission")
    seed(factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-LOT1-API",
        titre="Mission",
        date_debut=date(2026, 9, 1),
        date_fin=date(2026, 9, 5),
        statut=StatutMission.PLANIFIE.value,
    )
    seed(factory, mission)
    return mission


def test_sessions_et_participations(api_client: TestClient, api_session_factory) -> None:
    mission = _seed_mission(api_session_factory)

    # Création manuelle (chemin exceptionnel, ADR 0012) : session « en
    # planification », à confirmer ensuite.
    cree = api_client.post(
        f"/api/v1/missions/{mission.id}/sessions",
        json={"theme": "Atelier", "date_debut": "2026-09-01", "date_fin": "2026-09-05"},
    )
    assert cree.status_code == 201, cree.text
    session_id = cree.json()["id"]
    assert cree.json()["statut"] == "en_planification"

    liste = api_client.get(f"/api/v1/missions/{mission.id}/sessions")
    assert len(liste.json()) == 1

    confirme = api_client.post(
        f"/api/v1/sessions/{session_id}/statut", json={"statut": "planifie"}
    )
    assert confirme.status_code == 200
    assert confirme.json()["statut"] == "planifie"

    # 1 mission → 1 session : une seconde création est refusée.
    seconde = api_client.post(
        f"/api/v1/missions/{mission.id}/sessions", json={"theme": "Bis"}
    )
    assert seconde.status_code == 409

    # Re-confirmer une session déjà planifiée : interdit par la machine.
    illegal = api_client.post(f"/api/v1/sessions/{session_id}/statut", json={"statut": "planifie"})
    assert illegal.status_code == 409

    # Les statuts automatiques ne se saisissent pas (ADR 0012).
    automatique = api_client.post(
        f"/api/v1/sessions/{session_id}/statut", json={"statut": "en_cours"}
    )
    assert automatique.status_code == 422

    ben = api_client.post("/api/v1/beneficiaires/", json={"nom": "Tazi", "prenom": "Sara"}).json()
    inscrite = api_client.post(
        f"/api/v1/sessions/{session_id}/participations",
        json={"beneficiaire_id": ben["id"]},
    )
    assert inscrite.status_code == 201, inscrite.text
    part_id = inscrite.json()["id"]

    double = api_client.post(
        f"/api/v1/sessions/{session_id}/participations",
        json={"beneficiaire_id": ben["id"]},
    )
    assert double.status_code == 409

    # Le pointage porte la date du jour pointé (règle confirmée 24/09).
    pointage = api_client.post(
        f"/api/v1/participations/{part_id}/presence",
        json={"date": "2026-09-03", "presence": "present"},
    )
    assert (pointage.json()["date"], pointage.json()["presence"]) == (
        "2026-09-03",
        "present",
    )

    complete = api_client.patch(f"/api/v1/participations/{part_id}", json={"evaluation": "Réussi"})
    assert complete.json()["evaluation"] == "Réussi"

    liste_parts = api_client.get(f"/api/v1/sessions/{session_id}/participations")
    assert len(liste_parts.json()) == 1

    assert api_client.get(f"/api/v1/participations/{uuid4()}").status_code == 404
    assert (
        api_client.post(f"/api/v1/missions/{uuid4()}/sessions", json={"theme": "X"}).status_code
        == 404
    )


def test_missions_routes(api_client: TestClient, api_session_factory) -> None:
    org = Organisation(nom="Client Mission Directe")
    seed(api_session_factory, org)

    cree = api_client.post(
        "/api/v1/missions/directe",
        json={
            "organisation_id": str(org.id),
            "reference": "M-DIR-01",
            "titre": "Prestation directe",
        },
    )
    assert cree.status_code == 201, cree.text
    mission_id = cree.json()["id"]

    assert api_client.get(f"/api/v1/missions/{mission_id}").status_code == 200
    assert cree.json()["lot_id"] is None  # prestation directe, identifiée comme telle

    # « En cours » et « Clôturé » sont automatiques : les saisir est refusé.
    automatique = api_client.post(
        f"/api/v1/missions/{mission_id}/statut", json={"statut": "en_cours"}
    )
    assert automatique.status_code == 422

    # La planification exige des dates (ADR 0012) : sans elles, 422.
    sans_dates = api_client.post(f"/api/v1/missions/{mission_id}/planification")
    assert sans_dates.status_code == 422

    # Annulation manuelle : la mission passe à « annulé ».
    annule = api_client.post(
        f"/api/v1/missions/{mission_id}/annulation", json={"motif": "Annulé test"}
    )
    assert annule.status_code == 200
    assert annule.json()["statut"] == "annule"

    assert api_client.get(f"/api/v1/missions/{uuid4()}").status_code == 404
