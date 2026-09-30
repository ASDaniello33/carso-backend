"""Repositories des bénéficiaires, des participations et des présences.

(instruction/04 §2 — la présence datée ajoutée le 24/09 vit ici : elle
appartient au même agrégat que l'inscription.)
"""

import uuid
from datetime import date

from sqlalchemy import select

from app.domain.execution import Beneficiaire, Participation, Presence
from app.infrastructure.repositories.base import BaseRepository


class BeneficiaireRepository(BaseRepository[Beneficiaire]):
    model = Beneficiaire

    def get_by_identifiant_externe(self, identifiant: str) -> Beneficiaire | None:
        stmt = select(Beneficiaire).where(Beneficiaire.identifiant_externe == identifiant)
        return self.session.scalars(stmt).first()

    def list_by_statut(self, statut: str) -> list[Beneficiaire]:
        stmt = select(Beneficiaire).where(Beneficiaire.statut == statut)
        return list(self.session.scalars(stmt))


class ParticipationRepository(BaseRepository[Participation]):
    model = Participation

    def get_for_couple(
        self, session_id: uuid.UUID, beneficiaire_id: uuid.UUID
    ) -> Participation | None:
        stmt = select(Participation).where(
            Participation.session_id == session_id,
            Participation.beneficiaire_id == beneficiaire_id,
        )
        return self.session.scalars(stmt).first()

    def list_for_session(self, session_id: uuid.UUID) -> list[Participation]:
        stmt = select(Participation).where(Participation.session_id == session_id)
        return list(self.session.scalars(stmt))

    def list_for_beneficiaire(self, beneficiaire_id: uuid.UUID) -> list[Participation]:
        stmt = select(Participation).where(Participation.beneficiaire_id == beneficiaire_id)
        return list(self.session.scalars(stmt))


class PresenceRepository(BaseRepository[Presence]):
    """Pointages datés — lectures seules (la règle vit dans ``PresenceService``)."""

    model = Presence

    def get_for_couple(self, participation_id: uuid.UUID, jour: date) -> Presence | None:
        """Pointage d'une personne **pour une date** donnée, s'il existe."""
        stmt = select(Presence).where(
            Presence.participation_id == participation_id,
            Presence.date == jour,
        )
        return self.session.scalars(stmt).first()

    def list_for_participation(self, participation_id: uuid.UUID) -> list[Presence]:
        stmt = (
            select(Presence)
            .where(Presence.participation_id == participation_id)
            .order_by(Presence.date)
        )
        return list(self.session.scalars(stmt))

    def list_for_session(
        self, session_id: uuid.UUID, jour: date | None = None
    ) -> list[Presence]:
        """Pointages d'une session, toutes dates ou une seule, du plus ancien au plus récent."""
        stmt = (
            select(Presence)
            .join(Participation, Presence.participation_id == Participation.id)
            .where(Participation.session_id == session_id)
        )
        if jour is not None:
            stmt = stmt.where(Presence.date == jour)
        return list(self.session.scalars(stmt.order_by(Presence.date)))
