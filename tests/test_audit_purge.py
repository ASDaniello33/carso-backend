"""Purge des journaux d'audit — administrateur, geste tracé, trace protégée.

Décision CARSO (10/11) : l'administrateur peut **supprimer** des journaux
d'audit, à la main (sélection) ou par période. Ces tests verrouillent les trois
règles qui rendent ce geste acceptable :

1. **réservé à l'administrateur** — un collaborateur reçoit 403, même s'il voit
   le journal en lecture ;
2. **la trace du geste survit** — purger ne détruit jamais les lignes
   ``audit.purge``, sinon vider le journal effacerait la preuve du vidage ;
3. **la sélection est explicite** — identifiants ou plage de dates, jamais les
   deux, jamais rien ; un motif est obligatoire.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, ActorType, RoleUtilisateur, StatutUtilisateur
from app.domain.identity import Utilisateur

PRÉFIXE = "/api/v1"
JOUR = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def _utilisateur(role: str) -> Utilisateur:
    return Utilisateur(
        email=f"{role}-{uuid4().hex[:6]}@carso.test",
        nom="Audit",
        prenom="Test",
        password_hash="x",
        role=role,
        statut=StatutUtilisateur.ACTIF.value,
    )


def _evenement(action: str, quand: datetime, entite: str = "offre") -> AuditEvent:
    return AuditEvent(
        actor_type=ActorType.HUMAIN.value,
        actor_id=str(uuid4()),
        action=action,
        entity_type=entite,
        entity_id=str(uuid4()),
        created_at=quand,
    )


@pytest.fixture
def contenu(api_session_factory: sessionmaker[Session]) -> Iterator[SimpleNamespace]:
    """Trois événements ordinaires sur deux jours + une purge antérieure."""
    session = api_session_factory()
    try:
        ancien = _evenement(ActionAudit.OFFRE_PUBLIEE.value, datetime(2026, 10, 1, 9, tzinfo=UTC))
        recent = _evenement(ActionAudit.MISSION_CREEE.value, JOUR, entite="mission")
        autre = _evenement(ActionAudit.DOCUMENT_APPROUVE.value, JOUR, entite="document")
        trace = _evenement(ActionAudit.AUDIT_PURGE.value, datetime(2026, 9, 1, tzinfo=UTC), entite="audit_event")
        session.add_all([ancien, recent, autre, trace])
        session.commit()
        yield SimpleNamespace(
            ancien=str(ancien.id),
            recent=str(recent.id),
            autre=str(autre.id),
            trace=str(trace.id),
        )
    finally:
        session.close()


def _client(api_client: TestClient, role: str) -> TestClient:
    api_client.app.dependency_overrides[get_current_user] = lambda: _utilisateur(role)
    return api_client


def _compter(session_factory: sessionmaker[Session], action: str | None = None) -> int:
    session = session_factory()
    try:
        stmt = select(func.count()).select_from(AuditEvent)
        if action is not None:
            stmt = stmt.where(AuditEvent.action == action)
        return int(session.scalar(stmt) or 0)
    finally:
        session.close()


def test_collaborateur_ne_peut_pas_purger(api_client, contenu, api_session_factory) -> None:
    """La lecture est ouverte à l'équipe interne, la **purge** ne l'est pas."""
    client = _client(api_client, RoleUtilisateur.COLLABORATEUR.value)
    avant = _compter(api_session_factory)
    reponse = client.post(
        f"{PRÉFIXE}/audit/purge",
        json={"motif": "nettoyage", "ids": [contenu.ancien]},
    )
    assert reponse.status_code == 403
    assert _compter(api_session_factory) == avant


def test_administrateur_purge_une_selection(api_client, contenu, api_session_factory) -> None:
    """Sélection manuelle : seules les lignes cochées partent, la trace reste."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    reponse = client.post(
        f"{PRÉFIXE}/audit/purge",
        json={"motif": "bruit de test", "ids": [contenu.ancien, contenu.recent]},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["purges"] == 2
    assert corps["conserves"] == 0
    assert _compter(api_session_factory) == 3  # autre + trace + la trace du geste
    assert _compter(api_session_factory, ActionAudit.AUDIT_PURGE.value) == 2


def test_trace_de_purge_jamais_supprimable(api_client, contenu, api_session_factory) -> None:
    """Cocher une ligne ``audit.purge`` ne la supprime pas : elle est protégée."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    reponse = client.post(
        f"{PRÉFIXE}/audit/purge",
        json={"motif": "tentative", "ids": [contenu.trace]},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["purges"] == 0
    assert corps["conserves"] == 1
    assert _compter(api_session_factory, ActionAudit.AUDIT_PURGE.value) == 2  # ancienne + nouvelle


def test_purge_par_plage_de_dates(api_client, contenu, api_session_factory) -> None:
    """Purge par période : tout ce qui est dans la plage part, le reste demeure."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    reponse = client.post(
        f"{PRÉFIXE}/audit/purge",
        json={
            "motif": "rétention décidée",
            "date_debut": "2026-11-03T00:00:00Z",
            "date_fin": "2026-11-03T23:59:59Z",
        },
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["purges"] == 2
    restants = _compter(api_session_factory)
    assert restants == 3  # l'ancien d'octobre, l'ancienne purge, la nouvelle trace


def test_motif_obligatoire(api_client, contenu, api_session_factory) -> None:
    """Sans motif, la purge est refusée : on ne détruit pas une trace sans raison."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    avant = _compter(api_session_factory)
    reponse = client.post(
        f"{PRÉFIXE}/audit/purge",
        json={"motif": "   ", "ids": [contenu.ancien]},
    )
    assert reponse.status_code in (400, 422)
    assert _compter(api_session_factory) == avant


def test_selection_vide_refusee(api_client, contenu) -> None:
    """Ni identifiants ni dates : refus explicite, pas une purge silencieuse."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    reponse = client.post(f"{PRÉFIXE}/audit/purge", json={"motif": "rien"})
    assert reponse.status_code in (400, 422)


def test_selection_et_plage_melangees_refusees(api_client, contenu) -> None:
    """Deux sélections à la fois : refus, plutôt qu'un ordre de priorité deviné."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    reponse = client.post(
        f"{PRÉFIXE}/audit/purge",
        json={
            "motif": "ambigu",
            "ids": [contenu.ancien],
            "date_debut": "2026-11-03T00:00:00Z",
        },
    )
    assert reponse.status_code in (400, 422)


def test_purge_tracee_avec_periode_et_nombre(api_client, contenu, api_session_factory) -> None:
    """La trace du geste dit combien, sur quelle période et pourquoi."""
    client = _client(api_client, RoleUtilisateur.ADMINISTRATEUR.value)
    client.post(
        f"{PRÉFIXE}/audit/purge",
        json={
            "motif": "rétention 30 jours",
            "date_debut": "2026-10-01T00:00:00Z",
            "date_fin": "2026-10-31T23:59:59Z",
        },
    )
    session = api_session_factory()
    try:
        trace = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == ActionAudit.AUDIT_PURGE.value)
            .order_by(AuditEvent.created_at.desc())
        ).first()
        assert trace is not None
        metadonnees = trace.event_metadata or {}
        assert metadonnees["reason"] == "rétention 30 jours"
        assert metadonnees["periode"][0].startswith("2026-10-01")
        assert trace.after == {"evenements_purges": 1}
    finally:
        session.close()
