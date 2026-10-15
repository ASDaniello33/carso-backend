"""Traçabilité applicative—paire Approbation + AuditEvent dans LA MÊME transaction.

Le service applicatif est responsable de l'atomicité (instruction/04 §5) :

    BEGIN
      mutation officielle
      approbation (décision humaine)
      audit (événement)
    COMMIT

TraceContext n'ouvre ni ne clôt la transaction—il écrit dans la session du
service appelant, qui fait le commit. Aucun secret ni contenu documentaire
sensible dans les événements (instruction/09 §9).

**Qui fait quoi**—un run agentique écrit ses événements avec
``actor_type=agent`` et ``actor_id=<nom de l'agent>``. Sans plus, la trace
dirait *qu'un* agent a agi, jamais *pour qui*. Le demandeur (l'utilisateur qui a
ouvert la conversation, ou l'auteur d'une mention dans le chat social) est donc
mémorisé dans un ``ContextVar``—posé par la garde AG-UI et par la tâche de
fond des mentions—puis recopié dans ``event_metadata['demande_par']``. Le
demandeur est **aussi** le périmètre qui a filtré les tools—un agent RH
lancé par un formateur ne voit que les missions de ce formateur.

Le demandeur n'est jamais deviné—sans contexte posé, aucune clé n'est
ajoutée (un script ou un test n'invente pas d'utilisateur à l'origine du
geste).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.enums import ActorType
from app.infrastructure.repositories import ApprobationRepository, AuditRepository

#: Utilisateur à l'origine du run agentique courant (jamais un secret).
_DEMANDE_PAR: ContextVar[str | None] = ContextVar("demande_par", default=None)


def poser_demande_par(utilisateur_id: str | UUID | None) -> Token[str | None]:
    """Mémorise le demandeur du contexte agentique (retourne le jeton)."""
    return _DEMANDE_PAR.set(str(utilisateur_id) if utilisateur_id else None)


def retablir_demande_par(jeton: Token[str | None]) -> None:
    """Restaure le demandeur précédent (fin de run : un contexte = un run)."""
    _DEMANDE_PAR.reset(jeton)


def demande_par_effectif() -> str | None:
    """Demandeur posé dans le contexte courant, sinon ``None``."""
    return _DEMANDE_PAR.get()


@contextmanager
def demande_par(utilisateur_id: str | UUID | None) -> Iterator[None]:
    """Pose le demandeur le temps d'un bloc, puis restaure l'état précédent.

    Sert aux rares routes HTTP qui déclenchent un geste **d'agent** (ex. :
    soumission d'une extraction) : l'événement reste celui de l'agent, mais le
    journal nomme l'utilisateur à l'origine de la demande.
    """
    jeton = poser_demande_par(utilisateur_id)
    try:
        yield
    finally:
        retablir_demande_par(jeton)


@dataclass(frozen=True, slots=True)
class Decision:
    """Décision humaine traçable."""

    decided_by: str
    reason: str | None = None


class TraceContext:
    """Enregistre décisions et événements dans la transaction courante."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._audit = AuditRepository(session)
        self._approbation = ApprobationRepository(session)
        self.actor_id = actor_id

    def record_decision(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        proposal_type: str,
        decision: str,
        proposed_by_agent: str | None,
        decided_by: str,
        reason: str | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        """Decision humaine + événement d'audit associé (une écriture par table)."""
        self._approbation.add_approbation(
            entity_type=entity_type,
            entity_id=entity_id,
            proposal_type=proposal_type,
            proposed_by_agent=proposed_by_agent,
            decision=decision,
            decided_by=decided_by,
            decision_reason=reason,
        )
        self._audit.add_event(
            actor_type=ActorType.HUMAIN.value,
            actor_id=decided_by,
            action=proposal_type,
            entity_type=entity_type,
            entity_id=str(entity_id),
            after=after,
            event_metadata={"reason": reason} if reason else None,
        )

    def record_event(
        self,
        *,
        actor_type: str,
        action: str,
        entity_type: str,
        entity_id: str | UUID | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        event_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Événement d'audit seul (action sans décision humaine).

        ``before`` n'est utile que là où la mutation efface ce qu'elle remplace :
        la purge d'une fiche documentaire (ADR 0006) écrit ce qu'elle a effacé
        (nom, version, motif de suppression), sinon la trace d'un geste
        irréversible ne décrirait rien.

        Quand un agent agit, le demandeur du run est ajouté aux métadonnées
        (``demande_par``) : le journal répond alors à « qui a fait quoi » et
        pas seulement « quel agent a agi ». Un appelant peut toujours fixer
        la clé lui-même — la valeur explicite n'est jamais écrasée.
        """
        if actor_type != ActorType.HUMAIN.value:
            demandeur = _DEMANDE_PAR.get()
            if demandeur:
                metadonnees = dict(event_metadata or {})
                metadonnees.setdefault("demande_par", demandeur)
                event_metadata = metadonnees
        self._audit.add_event(
            actor_type=actor_type,
            actor_id=self.actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            before=before,
            after=after,
            event_metadata=event_metadata,
        )


__all__ = [
    "Decision",
    "TraceContext",
    "demande_par",
    "demande_par_effectif",
    "poser_demande_par",
    "retablir_demande_par",
]
