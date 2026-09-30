"""Repositories de la chaîne commerciale : appels à proposition et lots."""

import uuid

from sqlalchemy import select

from app.domain.organization import AppelAProposition, Lot
from app.infrastructure.repositories.base import BaseRepository


class AppelAPropositionRepository(BaseRepository[AppelAProposition]):
    model = AppelAProposition

    def get_by_reference(self, reference: str) -> AppelAProposition | None:
        stmt = select(AppelAProposition).where(AppelAProposition.reference == reference)
        return self.session.scalars(stmt).first()

    def list_by_statut(self, statut: str) -> list[AppelAProposition]:
        stmt = select(AppelAProposition).where(AppelAProposition.statut == statut)
        return list(self.session.scalars(stmt))


class LotRepository(BaseRepository[Lot]):
    model = Lot

    def list_for_appel_a_proposition(self, appel_a_proposition_id: uuid.UUID) -> list[Lot]:
        stmt = select(Lot).where(Lot.appel_a_proposition_id == appel_a_proposition_id)
        return list(self.session.scalars(stmt))

    def get_by_numero(self, appel_a_proposition_id: uuid.UUID, numero: str) -> Lot | None:
        stmt = select(Lot).where(
            Lot.appel_a_proposition_id == appel_a_proposition_id, Lot.numero == numero
        )
        return self.session.scalars(stmt).first()
