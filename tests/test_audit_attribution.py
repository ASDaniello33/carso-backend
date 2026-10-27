"""Attribution des traces — « qui a fait quoi » (agent ⇒ demandeur humain).

Deux niveaux :

1. **Unité** : ``TraceContext`` enrichit les événements d'agent avec le
   demandeur posé dans le contexte (``demande_par``), jamais les événements
   humains, et n'écrase jamais une clé explicitement fournie.
2. **Balayage HTTP** : les gestes d'écriture des routes portent l'auteur
   authentifié — aucun événement anonyme ne doit sortir d'une requête.
"""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.trace import (
    TraceContext,
    demande_par,
    demande_par_effectif,
    poser_demande_par,
    retablir_demande_par,
)
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, ActorType

PRÉFIXE = "/api/v1"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_server_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


# --- Unité : demande_par ---------------------------------------------------------


def _dernier_evenement(session: Session) -> AuditEvent:
    return session.scalars(select(AuditEvent)).one()


def test_evenement_agent_porte_le_demandeur(session: Session) -> None:
    with demande_par("11111111-1111-1111-1111-111111111111"):
        TraceContext(session, actor_id="agent_rh").record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.AFFECTATION_PROPOSEE.value,
            entity_type="affectation",
            entity_id=None,
        )
    evenement = _dernier_evenement(session)
    assert evenement.actor_id == "agent_rh"
    assert evenement.event_metadata == {
        "demande_par": "11111111-1111-1111-1111-111111111111"
    }


def test_evenement_humain_n_ajoute_pas_de_demandeur(session: Session) -> None:
    """Quand l'acteur est l'humain, répéter son identifiant n'apprend rien."""
    with demande_par("11111111-1111-1111-1111-111111111111"):
        TraceContext(session, actor_id="22222222-2222-2222-2222-222222222222").record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_CREEE.value,
            entity_type="mission",
            entity_id=None,
        )
    assert _dernier_evenement(session).event_metadata is None


def test_sans_demandeur_pose_aucune_cle_inventee(session: Session) -> None:
    TraceContext(session, actor_id="agent_rh").record_event(
        actor_type=ActorType.AGENT.value,
        action=ActionAudit.EXTRACTION_PROPOSEE.value,
        entity_type="appel_a_proposition",
        entity_id=None,
    )
    assert _dernier_evenement(session).event_metadata is None
    assert demande_par_effectif() is None


def test_metadonnees_explicites_non_ecrasees(session: Session) -> None:
    with demande_par("11111111-1111-1111-1111-111111111111"):
        TraceContext(session, actor_id="agent_rh").record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.EXTRACTION_PROPOSEE.value,
            entity_type="appel_a_proposition",
            entity_id=None,
            event_metadata={"demande_par": "explicite", "proposed_by_agent": "agent_rh"},
        )
    assert _dernier_evenement(session).event_metadata == {
        "demande_par": "explicite",
        "proposed_by_agent": "agent_rh",
    }


def test_jeton_de_demandeur_restaure() -> None:
    jeton = poser_demande_par("11111111-1111-1111-1111-111111111111")
    assert demande_par_effectif() == "11111111-1111-1111-1111-111111111111"
    retablir_demande_par(jeton)
    assert demande_par_effectif() is None


# --- Balayage HTTP : chaque geste porte son auteur -------------------------------


def _acteur(api_client: TestClient) -> str:
    return api_client.get(f"{PRÉFIXE}/auth/me").json()["id"]


def test_gestes_http_portent_toujours_un_acteur(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Les routes qui ont été équipées de ``actor_id`` le prouvent en base.

    Parcours volontairement varié (référentiel, dossier, missions, affectations,
    documents) : chaque événement écrit doit nommer le compte authentifié ; le
    seul événement d'agent (extraction) doit nommer son **demandeur**.
    """
    moi = _acteur(api_client)

    lieu = api_client.post(f"{PRÉFIXE}/lieux/", json={"nom": "Salle A"})
    assert lieu.status_code == 201, lieu.text
    assert api_client.patch(
        f"{PRÉFIXE}/lieux/{lieu.json()['id']}", json={"ville": "Antananarivo"}
    ).status_code == 200

    organisation = api_client.post(f"{PRÉFIXE}/organisations/", json={"nom": "Client X"})
    assert organisation.status_code == 201, organisation.text
    organisation_id = organisation.json()["id"]

    appel = api_client.post(
        f"{PRÉFIXE}/appels/",
        json={
            "organisation_id": organisation_id,
            "type": "appel_a_proposition",
            "reference": "AO-ACT-1",
            "titre": "Appel tracé",
        },
    )
    assert appel.status_code == 201, appel.text
    extraction = api_client.post(
        f"{PRÉFIXE}/appels/{appel.json()['id']}/extraction",
        json={
            "donnees_extraites": {"titre": "Extrait"},
            "proposed_by_agent": "agent_generateur_offre",
        },
    )
    assert extraction.status_code == 202, extraction.text

    mission = api_client.post(
        f"{PRÉFIXE}/missions/directe",
        json={
            "organisation_id": organisation_id,
            "reference": "M-ACT-1",
            "titre": "Mission tracée",
        },
    )
    assert mission.status_code == 201, mission.text
    mission_id = mission.json()["id"]

    equipe = api_client.post(
        f"{PRÉFIXE}/equipes/",
        json={"nom": "Membre", "prenom": "Test", "email": "membre@carso.test"},
    )
    assert equipe.status_code == 201, equipe.text
    affectation = api_client.post(
        f"{PRÉFIXE}/missions/{mission_id}/affectations",
        json={
            "mission_id": mission_id,
            "equipe_id": equipe.json()["id"],
            "role_dans_mission": "formateur",
        },
    )
    assert affectation.status_code == 202, affectation.text
    decision = api_client.post(
        f"{PRÉFIXE}/affectations/{affectation.json()['id']}/approbation",
        json={"decision": "approuve", "reason": "Profil conforme"},
    )
    assert decision.status_code == 200, decision.text

    document = api_client.post(
        f"{PRÉFIXE}/documents",
        files={"file": ("trace.pdf", b"%PDF-1.4 contenu", "application/pdf")},
        data={
            "type_document": "rapport",
            "nom": "Rapport tracé.pdf",
            "mission_id": mission_id,
        },
    )
    assert document.status_code == 201, document.text
    assert api_client.post(
        f"{PRÉFIXE}/documents/{document.json()['id']}/soumission"
    ).status_code == 200
    assert api_client.post(
        f"{PRÉFIXE}/documents/{document.json()['id']}/approbation",
        json={"reason": "Relu"},
    ).status_code == 200

    session = api_session_factory()
    try:
        evenements = list(session.scalars(select(AuditEvent)))
    finally:
        session.close()

    assert len(evenements) >= 8
    anonymes: list[str] = []
    for evenement in evenements:
        if evenement.actor_type == ActorType.AGENT.value:
            demandeur = (evenement.event_metadata or {}).get("demande_par")
            if demandeur != moi:
                anonymes.append(f"{evenement.action} (agent sans demandeur)")
        elif evenement.actor_id != moi:
            anonymes.append(f"{evenement.action} (acteur {evenement.actor_id})")
    assert anonymes == []
    actions = {evenement.action for evenement in evenements}
    assert ActionAudit.LIEU_ENREGISTRE.value in actions
    assert ActionAudit.LIEU_MIS_A_JOUR.value in actions
    assert ActionAudit.EXTRACTION_PROPOSEE.value in actions
    # L'événement de décision porte le **type de proposition** (conception de
    # ``TraceContext.record_decision``) : ``affectation_equipe`` et
    # ``document_officiel``, avec ``actor_type=humain`` et ``decided_by`` —
    # jamais anonymes.
    assert "affectation_equipe" in actions
    assert "document_officiel" in actions
    assert ActionAudit.AFFECTATION_PROPOSEE.value in actions
    assert ActionAudit.DOCUMENT_SOUMIS.value in actions


def test_le_demandeur_est_le_compte_authentifie_pas_un_identifiant_de_corps(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """L'attribution ne vient jamais du client (AGENTS.md §9)."""
    moi = _acteur(api_client)
    faux = "99999999-9999-9999-9999-999999999999"
    organisation = api_client.post(
        f"{PRÉFIXE}/organisations/", json={"nom": "Client Y"}
    ).json()
    appel = api_client.post(
        f"{PRÉFIXE}/appels/",
        json={
            "organisation_id": organisation["id"],
            "type": "appel_a_proposition",
            "reference": "AO-ACT-2",
            "titre": "Appel",
        },
    ).json()
    api_client.post(
        f"{PRÉFIXE}/appels/{appel['id']}/extraction",
        json={
            "donnees_extraites": {"titre": "Extrait"},
            "proposed_by_agent": "agent_generateur_offre",
            "demande_par": faux,
        },
    )
    session = api_session_factory()
    try:
        evenement = session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.EXTRACTION_PROPOSEE.value
            )
        ).one()
        assert evenement.event_metadata["demande_par"] == moi
        assert evenement.event_metadata["demande_par"] != faux
    finally:
        session.close()

