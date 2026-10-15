"""Repositories de traçabilité : AuditEvent (insert-only) et Approbation."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

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

    def lister(
        self,
        *,
        acteur_type: str | None = None,
        acteur_id: str | None = None,
        action: str | None = None,
        entity_type: str | Sequence[str] | None = None,
        entity_id: str | None = None,
        depuis: datetime | None = None,
        jusqu_a: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        """Journal d'audit filtré, du plus récent au plus ancien (lecture seule).

        L'audit n'est **jamais** modifié ni purgé par cette lecture : la
        rétention CARSO reste une décision ouverte, on ne détruit pas de trace.

        Args:
            acteur_type: ``humain`` | ``agent`` | ``systeme``.
            acteur_id: identifiant de l'acteur (compte ou nom d'agent).
            action: action du vocabulaire ``ActionAudit``.
            entity_type: type(s) d'entité touché(s). Une même ressource porte
                parfois plusieurs noms dans le journal (``offre`` et
                ``offre_formation``, ``mission`` et ``missions``) : passer la
                liste ramène l'histoire complète d'une entité, sans mentir sur
                ce que le journal contient.
            entity_id: identifiant de l'entité touchée.
            depuis: borne basse inclusive (horodatage).
            jusqu_a: borne haute inclusive (horodatage).
            limit: taille de page (déjà plafonnée par la route).
            offset: décalage de page.

        Returns:
            ``(événements, total)`` — le total compte **avant** pagination pour
            que l'interface affiche un vrai décompte.
        """
        conditions: list[Any] = []
        if acteur_type:
            conditions.append(AuditEvent.actor_type == acteur_type)
        if acteur_id:
            conditions.append(AuditEvent.actor_id == acteur_id)
        if action:
            conditions.append(AuditEvent.action == action)
        if entity_type:
            noms = [entity_type] if isinstance(entity_type, str) else list(entity_type)
            conditions.append(
                AuditEvent.entity_type == noms[0]
                if len(noms) == 1
                else AuditEvent.entity_type.in_(noms)
            )
        if entity_id:
            conditions.append(AuditEvent.entity_id == entity_id)
        if depuis is not None:
            conditions.append(AuditEvent.created_at >= depuis)
        if jusqu_a is not None:
            conditions.append(AuditEvent.created_at <= jusqu_a)

        total = int(
            self.session.scalar(
                select(func.count()).select_from(AuditEvent).where(*conditions)
            )
            or 0
        )
        stmt = (
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt)), total


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
