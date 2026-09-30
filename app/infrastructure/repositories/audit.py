"""Repositories de traçabilité : AuditEvent (insert-only) et Approbation."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.domain.document import Approbation, AuditEvent
from app.infrastructure.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditEvent]):
    """Journal d'audit : ajout seul. Jamais de modification ni de suppression."""

    model = AuditEvent

    def add_event(
        self,
        *,
        actor_type: str,
        actor_id: str | None,
        action: str,
        entity_type: str,
        entity_id: str | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        event_metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Ajoute un événement d'audit à la session (commit par le service)."""
        event = AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            event_metadata=event_metadata,
        )
        return self.add(event)


class ApprobationRepository(BaseRepository[Approbation]):
    """Traces de décisions humaines (human-in-the-loop)."""

    model = Approbation

    def add_approbation(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        proposal_type: str,
        proposed_by_agent: str | None,
        decision: str,
        decided_by: str | None,
        decision_reason: str | None = None,
    ) -> Approbation:
        """Enregistre une décision avec horodatage côté application."""
        approbation = Approbation(
            entity_type=entity_type,
            entity_id=entity_id,
            proposal_type=proposal_type,
            proposed_by_agent=proposed_by_agent,
            decision=decision,
            decided_by=decided_by,
            decision_reason=decision_reason,
            decided_at=datetime.now().astimezone(),
        )
        return self.add(approbation)

    def list_for_entity(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> list[Approbation]:
        """Historique des décisions portant sur une entité."""
        stmt = (
            select(Approbation)
            .where(
                Approbation.entity_type == entity_type,
                Approbation.entity_id == entity_id,
            )
            .order_by(Approbation.created_at)
        )
        return list(self.session.scalars(stmt))
