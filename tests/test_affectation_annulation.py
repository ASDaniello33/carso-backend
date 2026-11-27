"""Annulation / retrait d'une affectation d'équipe.

Deux gestes passent par ``POST /affectations/{id}/annulation`` :

- une proposition ``proposee`` (demande 17/10) n'a jamais été décidée : elle est
  retirée de la liste, sans effet ;
- une affectation ``approuvee`` (demande 24/11) est **reprise** : la personne
  sort de l'équipe, redevient disponible et perd le périmètre ouvert. L'audit
  conserve l'état retiré (le contenu de la décision reprise).

Une affectation ``refusee`` a son propre geste de retrait (``DELETE``) : 409 ici.
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, StatutMission
from app.domain.execution import Mission
from app.domain.organization import Organisation
from tests.conftest import seed


def _mission(
    factory: sessionmaker[Session], *, reference: str = "M-ANNUL"
) -> Mission:
    org = Organisation(nom=f"Client annulation {reference}")
    seed(factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference=reference,
        titre=f"Mission annulation {reference}",
        date_debut=date(2026, 11, 2),
        date_fin=date(2026, 11, 30),
        statut=StatutMission.PLANIFIE.value,
    )
    seed(factory, mission)
    return mission


def _equipe(api_client: TestClient) -> str:
    reponse = api_client.post(
        "/api/v1/equipes",
        json={"nom": "Annulation", "prenom": "Test"},
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["id"]


def _proposer(
    api_client: TestClient, mission: Mission, equipe_id: str
) -> dict:
    reponse = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": equipe_id,
            "role_dans_mission": "formateur",
        },
    )
    assert reponse.status_code == 202, reponse.text
    return reponse.json()


def _approuver(api_client: TestClient, affectation_id: str) -> None:
    reponse = api_client.post(
        f"/api/v1/affectations/{affectation_id}/approbation",
        json={"decision": "approuve", "reason": "OK"},
    )
    assert reponse.status_code == 200, reponse.text


def test_annuler_une_proposition_en_attente(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Proposée → 204, la ligne disparaît et le retrait est tracé."""
    mission = _mission(api_session_factory)
    affectation = _proposer(api_client, mission, _equipe(api_client))

    reponse = api_client.post(f"/api/v1/affectations/{affectation['id']}/annulation")
    assert reponse.status_code == 204, reponse.text

    liste = api_client.get(f"/api/v1/missions/{mission.id}/affectations").json()
    ids = {item["id"] for item in liste["affectations"]}
    assert affectation["id"] not in ids

    with api_session_factory() as session:
        trace = session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.AFFECTATION_ANNULEE.value
            )
        ).one()
        assert trace.entity_id == affectation["id"]
        assert trace.actor_type == "humain"
        assert trace.event_metadata["retrait"] is True


def test_annuler_et_retirer_une_affectation_approuvee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Approuvée → 204 : la décision est reprise, la ligne disparaît, trace dédiée."""
    mission = _mission(api_session_factory)
    affectation = _proposer(api_client, mission, _equipe(api_client))
    _approuver(api_client, affectation["id"])

    reponse = api_client.post(f"/api/v1/affectations/{affectation['id']}/annulation")
    assert reponse.status_code == 204, reponse.text

    liste = api_client.get(f"/api/v1/missions/{mission.id}/affectations").json()
    assert affectation["id"] not in {item["id"] for item in liste["affectations"]}

    with api_session_factory() as session:
        trace = session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.AFFECTATION_RETIREE.value
            )
        ).one()
        assert trace.entity_id == affectation["id"]
        assert trace.actor_type == "humain"
        assert trace.event_metadata["retrait"] is True
        # Ce qui a été retiré reste lisible : le journal porte l'état d'avant.
        assert trace.before["statut"] == "approuvee"
        assert trace.before["equipe_id"] == affectation["equipe_id"]
        assert trace.before["approved_by"] is not None


def test_retrait_approuvee_libere_la_disponibilite(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """La personne redevient disponible : une autre mission qui chevauche l'accepte."""
    mission = _mission(api_session_factory)
    autre = _mission(api_session_factory, reference="M-CHEVAU")
    equipe_id = _equipe(api_client)
    affectation = _proposer(api_client, mission, equipe_id)
    _approuver(api_client, affectation["id"])

    corps = {
        "mission_id": str(autre.id),
        "equipe_id": equipe_id,
        "role_dans_mission": "formateur",
    }
    conflit = api_client.post(f"/api/v1/missions/{autre.id}/affectations", json=corps)
    assert conflit.status_code == 409, conflit.text

    reponse = api_client.post(f"/api/v1/affectations/{affectation['id']}/annulation")
    assert reponse.status_code == 204, reponse.text

    libere = api_client.post(f"/api/v1/missions/{autre.id}/affectations", json=corps)
    assert libere.status_code == 202, libere.text


def test_annulation_refusee_sur_affectation_refusee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un refus est une décision conservée : 409 (le retrait a son propre geste)."""
    mission = _mission(api_session_factory)
    affectation = _proposer(api_client, mission, _equipe(api_client))
    refus = api_client.post(
        f"/api/v1/affectations/{affectation['id']}/refus",
        json={"decision": "rejete", "reason": "Non disponible"},
    )
    assert refus.status_code == 200

    reponse = api_client.post(f"/api/v1/affectations/{affectation['id']}/annulation")
    assert reponse.status_code == 409


def test_annulation_affectation_inconnue_404(api_client: TestClient) -> None:
    from uuid import uuid4

    reponse = api_client.post(f"/api/v1/affectations/{uuid4()}/annulation")
    assert reponse.status_code == 404
