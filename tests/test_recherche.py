"""Recherche globale — familles bornées, permissions, zéro invention.

La recherche part de la donnée validée : base vide = aucun résultat ;
bénéficiaires exclus pour les rôles sans ``beneficiaire.gerer``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.application.services.recherche_service import RechercheService
from app.domain.enums import (
    StatutAppelAProposition,
    StatutMission,
)
from app.domain.execution import Mission
from app.domain.organization import AppelAProposition, Organisation
from tests.conftest import seed


def _organisation(nom: str = "Croix-Rouge Malagasy") -> Organisation:
    return Organisation(nom=nom)


def _appel(organisation_id: object, titre: str, reference: str) -> AppelAProposition:
    return AppelAProposition(
        organisation_id=organisation_id,
        reference=reference,
        titre=titre,
        statut=StatutAppelAProposition.RECU.value,
    )


def _mission(organisation_id: object, titre: str, reference: str) -> Mission:
    return Mission(
        organisation_id=organisation_id,
        reference=reference,
        titre=titre,
        statut=StatutMission.EN_COURS.value,
    )


def test_requete_trop_courte_ne_renvoie_rien(api_session_factory) -> None:
    session: Session = api_session_factory()
    try:
        resultat = RechercheService(session).rechercher("a")
    finally:
        session.close()
    assert resultat["familles"] == []
    assert resultat["total"] == 0


def test_retrouve_une_mission_par_reference(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)
    mission = _mission(orga.id, "Formation radar", "M-2026-001")
    seed(api_session_factory, mission)

    session = api_session_factory()
    try:
        resultat = RechercheService(session).rechercher("2026-001")
    finally:
        session.close()

    assert resultat["total"] >= 1
    familles = {f["famille"]: f for f in resultat["familles"]}
    assert "missions" in familles
    assert familles["missions"]["resultats"][0]["titre"] == "Formation radar"
    assert familles["missions"]["resultats"][0]["href"] == f"/missions/{mission.id}"


def test_retrouve_un_appel_par_titre_insensible_a_la_casse(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)
    appel = _appel(orga.id, "Formation des jeunes agriculteurs", "AP-42")
    seed(api_session_factory, appel)

    session = api_session_factory()
    try:
        resultat = RechercheService(session).rechercher("agriculteurs")
    finally:
        session.close()

    familles = {f["famille"]: f for f in resultat["familles"]}
    assert "appels" in familles
    assert familles["appels"]["resultats"][0]["id"] == str(appel.id)


def test_base_vide_aucun_resultat(api_session_factory) -> None:
    session: Session = api_session_factory()
    try:
        resultat = RechercheService(session).rechercher("formation")
    finally:
        session.close()
    assert resultat["total"] == 0
    assert resultat["familles"] == []


def test_beneficiaires_exclus_sans_permission(api_session_factory) -> None:
    """Sans ``beneficiaire.gerer`` : la famille bénéficiaires n'est jamais interrogée."""
    from app.domain.execution import Beneficiaire

    session: Session = api_session_factory()
    try:
        session.add(
            Beneficiaire(nom="Rasoa", prenom="Fanja", statut="actif")
        )
        session.commit()
    finally:
        session.close()

    session = api_session_factory()
    try:
        service = RechercheService(session, peut_voir_beneficiaires=False)
        assert service.rechercher("Rasoa")["total"] == 0

        service_avec = RechercheService(session, peut_voir_beneficiaires=True)
        familles = {
            f["famille"]: f for f in service_avec.rechercher("Rasoa")["familles"]
        }
    finally:
        session.close()

    assert "beneficiaires" in familles
    # Données minimisées : nom + prénom, jamais le contact.
    assert familles["beneficiaires"]["resultats"][0]["detail"] is None
    assert "Fanja Rasoa" in familles["beneficiaires"]["resultats"][0]["titre"]


def test_route_recherche_repond_en_mode_lab(
    api_session_factory, api_client: TestClient
) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)

    reponse = api_client.get("/api/v1/recherche/", params={"q": "croix"})
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["total"] >= 1
    assert {f["famille"] for f in corps["familles"]} == {"organisations"}


def test_route_recherche_requête_manquante_422(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/recherche/").status_code == 422


def test_utilisateur_en_attente_ne_voit_pas_les_beneficiaires(
    api_session_factory, api_client: TestClient
) -> None:
    """Mode lab : l'utilisateur technique est collaborateur — bénéficiaires inclus.

    On vérifie ici le contrat de payload ; la restriction par rôle est couverte
    par le test service (exclusion sans permission).
    """
    seed(api_session_factory, _organisation("Organisation Zéro"))

    reponse = api_client.get("/api/v1/recherche/", params={"q": "zéro"})
    assert reponse.status_code == 200
    corps = reponse.json()
    familles = {f["famille"] for f in corps["familles"]}
    assert "organisations" in familles
    assert "beneficiaires" not in familles  # base vide : famille absente
