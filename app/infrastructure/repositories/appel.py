"""Repositories de la chaîne commerciale : appels à proposition et lots."""

import uuid

from sqlalchemy import select

from app.domain.organization import Appel, Lot
from app.infrastructure.repositories.base import BaseRepository


class AppelRepository(BaseRepository[Appel]):
    model = Appel

    def get_by_reference(self, reference: str) -> Appel | None:
        stmt = select(Appel).where(Appel.reference == reference)
        return self.session.scalars(stmt).first()

    def list_by_statut(self, statut: str) -> list[Appel]:
        stmt = select(Appel).where(Appel.statut == statut)
        return list(self.session.scalars(stmt))


class LotRepository(BaseRepository[Lot]):
    model = Lot

    def list_for_appel(self, appel_id: uuid.UUID) -> list[Lot]:
        stmt = select(Lot).where(Lot.appel_id == appel_id)
        return list(self.session.scalars(stmt))

    def get_by_numero(self, appel_id: uuid.UUID, numero: str) -> Lot | None:
        stmt = select(Lot).where(
            Lot.appel_id == appel_id, Lot.numero == numero
        )
        return self.session.scalars(stmt).first()
