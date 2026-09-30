"""Traçabilité applicative : paire Approbation + AuditEvent dans LA MÊME transaction.

Le service applicatif est responsable de l'atomicité (instruction/04 §5) :

    BEGIN
      mutation officielle
      approbation (décision humaine)
      audit (événement)
    COMMIT

TraceContext n'ouvre ni ne clôt la transaction : il écrit dans la session du
service appelant, qui fait le commit. Aucun secret ni contenu documentaire
sensible dans les événements (instruction/09 §9).
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.enums import ActorType
from app.infrastructure.repositories import ApprobationRepository, AuditRepository


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
        """
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


__all__ = ["Decision", "TraceContext"]
