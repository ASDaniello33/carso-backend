"""Repository des supports de formation (Formateur ↔ Mission ↔ Document)."""

import uuid

from sqlalchemy import select

from app.domain.execution import SupportFormation
from app.infrastructure.repositories.base import BaseRepository


class SupportFormationRepository(BaseRepository[SupportFormation]):
    model = SupportFormation

    def list_for_mission(self, mission_id: uuid.UUID) -> list[SupportFormation]:
        """Supports d'une mission, du plus récent au plus ancien."""
        stmt = (
            select(SupportFormation)
            .where(SupportFormation.mission_id == mission_id)
            .order_by(SupportFormation.created_at.desc())
        )
        return list(self.session.scalars(stmt))

    def list_for_equipe(
        self, mission_id: uuid.UUID, equipe_id: uuid.UUID
    ) -> list[SupportFormation]:
        """Supports portés par un formateur donné, dans une mission donnée."""
        stmt = select(SupportFormation).where(
            SupportFormation.mission_id == mission_id,
            SupportFormation.equipe_id == equipe_id,
        )
        return list(self.session.scalars(stmt))

    def list_for_document(self, document_id: uuid.UUID) -> list[SupportFormation]:
        """Supports qui portent ce document (toutes missions confondues).

        Sert à la purge d'une fiche (ADR 0006) : un support de formation se
        rattache à un document par une FK ``RESTRICT``. Une fiche encore portée
        par une mission ne se purge pas — la liste permet à l'humain de retirer
        le support d'abord, plutôt que de subir une erreur de contrainte.
        """
        stmt = select(SupportFormation).where(SupportFormation.document_id == document_id)
        return list(self.session.scalars(stmt))

    def get_for(
        self, mission_id: uuid.UUID, equipe_id: uuid.UUID, document_id: uuid.UUID
    ) -> SupportFormation | None:
        """Support existant pour ce triplet (0 ou 1 — unicité du triplet)."""
        stmt = select(SupportFormation).where(
            SupportFormation.mission_id == mission_id,
            SupportFormation.equipe_id == equipe_id,
            SupportFormation.document_id == document_id,
        )
        return self.session.scalars(stmt).first()


__all__ = ["SupportFormationRepository"]
