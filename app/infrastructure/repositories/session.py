"""Repositories des sessions de formation (entité SessionFormation)."""

import uuid

from sqlalchemy import select

from app.domain.execution import SessionFormation
from app.infrastructure.repositories.base import BaseRepository


class SessionRepository(BaseRepository[SessionFormation]):
    model = SessionFormation

    def list_for_mission(self, mission_id: uuid.UUID) -> list[SessionFormation]:
        stmt = select(SessionFormation).where(SessionFormation.mission_id == mission_id)
        return list(self.session.scalars(stmt))

    def list_by_statut(self, statut: str) -> list[SessionFormation]:
        stmt = select(SessionFormation).where(SessionFormation.statut == statut)
        return list(self.session.scalars(stmt))

    def list_filtered(
        self, *, mission_id: uuid.UUID | None = None, statut: str | None = None
    ) -> list[SessionFormation]:
        """Sessions toutes missions confondues, filtres facultatifs (page UI).

        Tri par date de début décroissante puis création : l'ordre d'affichage
        reste stable même sans dates renseignées.
        """
        stmt = select(SessionFormation).order_by(
            SessionFormation.date_debut.desc().nullslast(),
            SessionFormation.created_at.desc(),
        )
        if mission_id is not None:
            stmt = stmt.where(SessionFormation.mission_id == mission_id)
        if statut is not None:
            stmt = stmt.where(SessionFormation.statut == statut)
        return list(self.session.scalars(stmt))
