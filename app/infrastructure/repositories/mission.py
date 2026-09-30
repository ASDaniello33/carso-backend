"""Repositories des missions, du vivier et des affectations."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select

from app.domain.execution import AffectationEquipe, Equipe, Mission, MissionOffre
from app.infrastructure.repositories.base import BaseRepository


class MissionRepository(BaseRepository[Mission]):
    model = Mission

    def get_by_reference(self, reference: str) -> Mission | None:
        stmt = select(Mission).where(Mission.reference == reference)
        return self.session.scalars(stmt).first()

    def list_by_statut(self, statut: str) -> list[Mission]:
        stmt = select(Mission).where(Mission.statut == statut)
        return list(self.session.scalars(stmt))

    def get_for_offre(self, offre_id: uuid.UUID) -> Mission | None:
        """Mission à laquelle cette offre est rattachée (via ``mission_offres``).

        Une offre appartient à une seule mission : ce contrôle empêche de la
        rattacher deux fois (la relation N-N porte plusieurs offres **par**
        mission, pas l'inverse).
        """
        stmt = (
            select(Mission)
            .join(MissionOffre, MissionOffre.mission_id == Mission.id)
            .where(MissionOffre.offre_id == offre_id)
        )
        return self.session.scalars(stmt).first()

    def list_for_offres(self, offre_ids: Iterable[uuid.UUID]) -> list[Mission]:
        """Missions déjà rattachées à l'une de ces offres (contrôle d'unicité)."""
        identifiants = list(offre_ids)
        if not identifiants:
            return []
        stmt = (
            select(Mission)
            .distinct()
            .join(MissionOffre, MissionOffre.mission_id == Mission.id)
            .where(MissionOffre.offre_id.in_(identifiants))
        )
        return list(self.session.scalars(stmt))


class EquipeRepository(BaseRepository[Equipe]):
    model = Equipe

    def get_by_email(self, email: str) -> Equipe | None:
        stmt = select(Equipe).where(Equipe.email == email)
        return self.session.scalars(stmt).first()

    def list_by_statut(self, statut: str) -> list[Equipe]:
        stmt = select(Equipe).where(Equipe.statut == statut)
        return list(self.session.scalars(stmt))


class AffectationEquipeRepository(BaseRepository[AffectationEquipe]):
    model = AffectationEquipe

    def list_for_mission(self, mission_id: uuid.UUID) -> list[AffectationEquipe]:
        stmt = select(AffectationEquipe).where(AffectationEquipe.mission_id == mission_id)
        return list(self.session.scalars(stmt))

    def list_active_for_equipe(self, equipe_id: uuid.UUID) -> list[AffectationEquipe]:
        """Affectations non refusées d'une personne — indicateur de charge, pas une
        règle de disponibilité (Q6 [P] : le modèle de disponibilité n'existe pas)."""
        stmt = select(AffectationEquipe).where(
            AffectationEquipe.equipe_id == equipe_id,
            AffectationEquipe.statut != "refusee",
        )
        return list(self.session.scalars(stmt))
