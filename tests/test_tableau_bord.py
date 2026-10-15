"""Tableau de bord — agrégations déterministes, lecture seule.

Le dashboard n'invente rien : base vide = zéros honnêtes ; données présentes =
compteurs, alertes et activité calculés en SQL par le service.

Note auth : en mode lab (AUTH_SECRET vide, contrat de la suite), la dépendance
``get_current_user`` renvoie un utilisateur technique actif — la route répond
donc 200 ici. La protection réelle (401 sans JWT) est couverte par
``get_current_user`` elle-même (test_auth.py) et active dès qu'AUTH_SECRET est
configuré.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.application.services.tableau_bord_service import TableauBordService
from app.domain.enums import (
    StatutAppelAProposition,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
    StatutUtilisateur,
)
from app.domain.execution import Mission, SessionFormation
from app.domain.identity import Utilisateur
from app.domain.organization import AppelAProposition, Lot, Offre, Organisation
from tests.conftest import seed


def _organisation(**surcharge: object) -> Organisation:
    champs: dict = {"nom": "Organisation Test"}
    champs.update(surcharge)
    return Organisation(**champs)


def _appel(organisation_id: UUID, statut: str) -> AppelAProposition:
    return AppelAProposition(
        organisation_id=organisation_id,
        reference=f"AP-{uuid4().hex[:6].upper()}",
        titre="Appel de test",
        statut=statut,
    )


def _lot(appel_id: UUID) -> Lot:
    return Lot(appel_a_proposition_id=appel_id, numero="1", titre="Lot 1")


def _offre(
    organisation_id: UUID, appel_id: UUID, lot_id: UUID, statut: str
) -> Offre:
    return Offre(
        organisation_id=organisation_id,
        appel_a_proposition_id=appel_id,
        lot_id=lot_id,
        type="offre_technique",
        reference=f"OFF-{uuid4().hex[:6].upper()}",
        titre="Offre de test",
        statut=statut,
    )


def _mission(organisation_id: UUID, statut: str) -> Mission:
    return Mission(
        organisation_id=organisation_id,
        reference=f"M-{uuid4().hex[:6].upper()}",
        titre="Mission de test",
        statut=statut,
    )


def _session_formation(mission_id: UUID, jours: int, statut: str) -> SessionFormation:
    return SessionFormation(
        mission_id=mission_id,
        theme="Session test",
        date_debut=datetime.now(UTC).date() + timedelta(days=jours),
        statut=statut,
    )


# --- Tests service --------------------------------------------------------------


def test_base_vide_renvoie_des_zeros(api_session_factory) -> None:
    """Base vide : compteurs à 0, alertes et activité vides (aucun inventaire)."""
    session: Session = api_session_factory()
    try:
        apercu = TableauBordService(session).apercu()
    finally:
        session.close()

    assert apercu["compteurs"]["appels"] == {s.value: 0 for s in StatutAppelAProposition}
    assert apercu["compteurs"]["offres"]["brouillon"] == 0
    assert apercu["compteurs"]["missions"]["planifiee"] == 0
    assert apercu["compteurs"]["organisations"] == {"total": 0}
    assert apercu["alertes"] == []
    assert apercu["activite_recente"] == []


def test_compteurs_refletent_les_donnees(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)

    appel_recu = _appel(orga.id, StatutAppelAProposition.RECU.value)
    appel_valide = _appel(orga.id, StatutAppelAProposition.VALIDE.value)
    seed(api_session_factory, appel_recu, appel_valide)

    mission = _mission(orga.id, StatutMission.EN_COURS.value)
    seed(api_session_factory, mission)

    session_formation = _session_formation(
        mission.id, 3, StatutSessionFormation.CONFIRMEE.value
    )
    seed(api_session_factory, session_formation)

    session = api_session_factory()
    try:
        apercu = TableauBordService(session).apercu()
    finally:
        session.close()

    assert apercu["compteurs"]["appels"]["recu"] == 1
    assert apercu["compteurs"]["appels"]["valide"] == 1
    assert apercu["compteurs"]["missions"]["en_cours"] == 1
    assert apercu["compteurs"]["sessions"]["confirmee"] == 1
    assert apercu["compteurs"]["organisations"] == {"total": 1}


def test_alertes_offres_et_inscriptions(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)
    appel = _appel(orga.id, StatutAppelAProposition.RECU.value)
    seed(api_session_factory, appel)
    lot = _lot(appel.id)
    seed(api_session_factory, lot)

    offre = _offre(orga.id, appel.id, lot.id, StatutOffre.EN_REVUE.value)
    utilisateur = Utilisateur(
        email=f"u{uuid4().hex[:6]}@carso.mg",
        nom="Test",
        prenom="User",
        password_hash="x",
        statut=StatutUtilisateur.PENDING.value,
    )
    seed(api_session_factory, offre, utilisateur)

    session = api_session_factory()
    try:
        alertes = TableauBordService(session).alertes()
    finally:
        session.close()

    types = {a["type"]: a for a in alertes}
    assert types["offres_a_valider"]["nb"] == 1
    assert types["inscriptions_en_attente"]["nb"] == 1
    assert types["offres_a_valider"]["href"] == "/offres"
    assert types["inscriptions_en_attente"]["href"] == "/utilisateurs"


def test_alerte_session_a_venir_dans_7_jours(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)
    mission = _mission(orga.id, StatutMission.EN_COURS.value)
    seed(api_session_factory, mission)
    session_formation = _session_formation(
        mission.id, 3, StatutSessionFormation.PLANIFIEE.value
    )
    seed(api_session_factory, session_formation)

    session = api_session_factory()
    try:
        alertes = TableauBordService(session).alertes()
    finally:
        session.close()

    proches = [a for a in alertes if a["type"] == "session_a_venir"]
    assert len(proches) == 1
    assert "Session test" in proches[0]["texte"]


def test_session_loin_na_pas_dalerte(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)
    mission = _mission(orga.id, StatutMission.EN_COURS.value)
    seed(api_session_factory, mission)
    loin = _session_formation(mission.id, 30, StatutSessionFormation.PLANIFIEE.value)
    seed(api_session_factory, loin)

    session = api_session_factory()
    try:
        alertes = TableauBordService(session).alertes()
    finally:
        session.close()

    assert [a for a in alertes if a["type"] == "session_a_venir"] == []


def test_activite_recente_triee_et_plafonnee(api_session_factory) -> None:
    orga = _organisation()
    seed(api_session_factory, orga)
    missions = [_mission(orga.id, StatutMission.PLANIFIEE.value) for _ in range(8)]
    seed(api_session_factory, *missions)

    session = api_session_factory()
    try:
        activite = TableauBordService(session).activite_recente()
    finally:
        session.close()

    missions_actives = [e for e in activite if e["type"] == "mission"]
    assert len(missions_actives) <= 6
    dates = [e["date"] for e in missions_actives]
    assert dates == sorted(dates, reverse=True)
    assert all(e["id"] and e["titre"] for e in missions_actives)


# --- Tests API -------------------------------------------------------------------


def test_route_tableau_bord_repond_en_mode_lab(
    api_session_factory, api_client: TestClient
) -> None:
    """Mode lab (AUTH_SECRET vide) : utilisateur technique actif → 200."""
    seed(api_session_factory, _organisation())

    reponse = api_client.get("/api/v1/tableau-bord/")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert {"compteurs", "alertes", "activite_recente"} <= set(corps)
    assert corps["compteurs"]["organisations"] == {"total": 1}
