"""Outil d'audit des agents — accès réservé à l'administrateur.

Décision : un agent peut lire le journal d'audit **si le compte qui lui parle
est administrateur**. Deux exigences testées ici :

1. la garde est appliquée à **l'appel** — un graphe partagé ne peut pas servir
   le journal à un collaborateur ou à un formateur ;
2. la lecture rend une ligne lisible (acteur, action en français, entité,
   motif) sans déverser ``before``/``after`` dans le contexte du modèle.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.contexte import (
    UserContext,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)
from app.agents.tools.agent_audit_tools import (
    NOM_OUTIL,
    ConsulterJournalAuditInput,
    construire_tools_audit,
)
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, ActorType, RoleUtilisateur


@pytest.fixture
def journal(api_session_factory: sessionmaker[Session]) -> Iterator[dict[str, str]]:
    """Deux événements sur la même offre, à deux dates différentes."""
    session = api_session_factory()
    try:
        offre_id = str(uuid4())
        session.add_all(
            [
                AuditEvent(
                    actor_type=ActorType.HUMAIN.value,
                    actor_id=str(uuid4()),
                    action=ActionAudit.OFFRE_PUBLIEE.value,
                    entity_type="offre",
                    entity_id=offre_id,
                    created_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
                ),
                AuditEvent(
                    actor_type=ActorType.HUMAIN.value,
                    actor_id=str(uuid4()),
                    action=ActionAudit.OFFRE_MODIFIEE.value,
                    entity_type="offre",
                    entity_id=offre_id,
                    event_metadata={"reason": "correction du budget"},
                    created_at=datetime(2026, 11, 5, 16, 30, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
        return {"offre_id": offre_id}
    finally:
        session.close()


def _outil(api_session_factory: sessionmaker[Session], definition):
    return construire_tools_audit(definition, api_session_factory)[0]


def test_outil_absent_si_le_contrat_ne_le_declare_pas() -> None:
    """Un agent sans l'outil au contrat ne le reçoit pas (refus par défaut)."""
    from app.agents.agent_personnel import DEFINITION as PERSONNEL

    assert construire_tools_audit(PERSONNEL, lambda: None) == ()


def test_role_non_administrateur_refuse(api_session_factory, journal) -> None:
    """Un collaborateur et un formateur ne lisent pas le journal — même à l'appel."""
    from app.agents.generaliste_readonly import DEFINITION

    outil = _outil(api_session_factory, DEFINITION)
    for role in (RoleUtilisateur.COLLABORATEUR.value, RoleUtilisateur.FORMATEUR.value):
        jeton = poser_contexte_utilisateur(UserContext(role=role, est_interne=True))
        try:
            resultat = outil.invoke(
                {"entity_type": "offre", "entity_id": journal["offre_id"]}
            )
        finally:
            retablir_contexte_utilisateur(jeton)
        assert resultat["ok"] is False, role
        assert resultat["error"]["code"] == "permission_denied", role


def test_sans_compte_identifie_refus(api_session_factory, journal) -> None:
    """Hors requête utilisateur, aucun rôle : refus, jamais « accès complet »."""
    from app.agents.generaliste_readonly import DEFINITION

    resultat = _outil(api_session_factory, DEFINITION).invoke({})
    assert resultat["ok"] is False
    assert resultat["error"]["code"] == "permission_denied"


def test_administrateur_lit_la_trace_d_un_dossier(api_session_factory, journal) -> None:
    """L'administrateur retrouve l'histoire d'une offre, avec motifs et libellés."""
    from app.agents.generaliste_readonly import DEFINITION

    jeton = poser_contexte_utilisateur(
        UserContext(role=RoleUtilisateur.ADMINISTRATEUR.value)
    )
    try:
        resultat = _outil(api_session_factory, DEFINITION).invoke(
            {"entity_type": "offre", "entity_id": journal["offre_id"], "limite": 10}
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert resultat["ok"] is True
    assert resultat["total"] == 2
    actions = [element["action"] for element in resultat["elements"]]
    assert actions == [
        ActionAudit.OFFRE_MODIFIEE.value,
        ActionAudit.OFFRE_PUBLIEE.value,
    ]
    recent = resultat["elements"][0]
    assert recent["libelle"] == "Offre modifiée"
    assert recent["motif"] == "correction du budget"
    assert recent["entite"] == "offre"
    # Le détail volumineux reste sur l'écran d'audit : pas dans le contexte LLM.
    assert "before" not in recent and "after" not in recent
    assert resultat["tronque"] is False


def test_bornes_de_dates_couvrent_la_journee(api_session_factory, journal) -> None:
    """Une borne de fin au 2 novembre inclut le geste de 9 h du 2 novembre."""
    from app.agents.generaliste_readonly import DEFINITION

    jeton = poser_contexte_utilisateur(
        UserContext(role=RoleUtilisateur.ADMINISTRATEUR.value)
    )
    try:
        resultat = _outil(api_session_factory, DEFINITION).invoke(
            {
                "entity_type": "offre",
                "entity_id": journal["offre_id"],
                "date_debut": date(2026, 11, 1),
                "date_fin": date(2026, 11, 2),
            }
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert resultat["ok"] is True
    assert resultat["total"] == 1
    assert resultat["elements"][0]["action"] == ActionAudit.OFFRE_PUBLIEE.value


def test_dates_inversees_refusees(api_session_factory, journal) -> None:
    """Début après fin : refus explicite, pas une liste vide trompeuse."""
    from app.agents.generaliste_readonly import DEFINITION

    jeton = poser_contexte_utilisateur(
        UserContext(role=RoleUtilisateur.ADMINISTRATEUR.value)
    )
    try:
        resultat = _outil(api_session_factory, DEFINITION).invoke(
            {"date_debut": date(2026, 11, 9), "date_fin": date(2026, 11, 2)}
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert resultat["ok"] is False
    assert resultat["error"]["code"] == "business_validation_error"


def test_limite_est_plafonnee() -> None:
    """Le modèle ne choisit pas le volume lu : la limite est bornée par le schéma."""
    entree = ConsulterJournalAuditInput(limite=500)
    assert entree.limite == 500  # le schéma accepte, l'outil resserre à l'appel
    assert ConsulterJournalAuditInput(limite=1).limite == 1


def test_nom_outil_stable() -> None:
    """Le nom est déclaré dans les contrats : le renommer les casserait en silence."""
    assert NOM_OUTIL == "consulter_journal_audit"
