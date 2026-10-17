"""Annulation d'une proposition d'affectation **en attente** (demande 17/10).

Règle : la carte de l'AgentRH doit pouvoir revenir sur une proposition qu'elle
vient de créer. Une proposition ``proposee`` n'a jamais été décidée et n'a aucun
effet : ``POST /affectations/{id}/annulation`` la retire et trace le retrait.
Une affectation approuvée ou refusée porte une décision : 409.
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


def _mission(factory: sessionmaker[Session]) -> Mission:
    org = Organisation(nom="Client annulation")
    seed(factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-ANNUL",
        titre="Mission annulation",
        date_debut=date(2026, 11, 2),
        date_fin=date(2026, 11, 30),
        statut=StatutMission.PLANIFIEE.value,
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


def _proposer(api_client: TestClient, mission: Mission, equipe_id: str) -> dict:
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


def test_annulation_refusee_sur_affectation_approuvee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Une décision favorable est figée : 409, la ligne reste."""
    mission = _mission(api_session_factory)
    affectation = _proposer(api_client, mission, _equipe(api_client))
    approbation = api_client.post(
        f"/api/v1/affectations/{affectation['id']}/approbation",
        json={"decision": "approuve", "reason": "OK"},
    )
    assert approbation.status_code == 200

    reponse = api_client.post(f"/api/v1/affectations/{affectation['id']}/annulation")
    assert reponse.status_code == 409
    assert reponse.json()["error"]["details"]["statut"] == "approuvee"


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
