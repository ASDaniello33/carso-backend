"""Repositories du cycle offre (concept générique ``Offre``).

Une offre répond à ``Appel + Lot`` et porte un type. Le repository ne contient
aucune règle métier : les règles (unicité lot+type, cohérence appel/lot,
cycle de vie) vivent dans ``OffreService``.
"""

import uuid

from sqlalchemy import select

from app.domain.organization import Offre
from app.infrastructure.repositories.base import BaseRepository


class OffreRepository(BaseRepository[Offre]):
    model = Offre

    def get_by_reference(self, reference: str) -> Offre | None:
        stmt = select(Offre).where(Offre.reference == reference)
        return self.session.scalars(stmt).first()

    def list_by_statut(self, statut: str) -> list[Offre]:
        stmt = select(Offre).where(Offre.statut == statut)
        return list(self.session.scalars(stmt))

    def list_by_type(self, offre_type: str) -> list[Offre]:
        """Offres d'un type donné (technique, financière, autre)."""
        stmt = select(Offre).where(Offre.type == offre_type)
        return list(self.session.scalars(stmt))

    def list_all(self) -> list[Offre]:
        """Toutes les offres, de la plus récente à la plus ancienne (usage UI)."""
        stmt = select(Offre).order_by(Offre.created_at.desc())
        return list(self.session.scalars(stmt))

    def list_for_lot(self, lot_id: uuid.UUID) -> list[Offre]:
        """Offres rattachées à un lot (au plus une par type)."""
        stmt = select(Offre).where(Offre.lot_id == lot_id)
        return list(self.session.scalars(stmt))

    def list_for_appel(self, appel_a_proposition_id: uuid.UUID) -> list[Offre]:
        """Offres rattachées à un appel, tous lots confondus."""
        stmt = select(Offre).where(
            Offre.appel_a_proposition_id == appel_a_proposition_id
        )
        return list(self.session.scalars(stmt))

    def get_for_lot_and_type(self, lot_id: uuid.UUID, offre_type: str) -> Offre | None:
        """Offre existante pour ce lot et ce type (0 ou 1 — unicité lot+type)."""
        stmt = select(Offre).where(Offre.lot_id == lot_id, Offre.type == offre_type)
        return self.session.scalars(stmt).first()

    def list_for_modele_document(self, document_id: uuid.UUID) -> list[Offre]:
        """Offres qui ont choisi ce document comme modèle (purge, ADR 0006).

        ``offres.modele_document_id`` est déclarée ``ON DELETE SET NULL`` : le
        moteur accepterait donc de purger la fiche en cassant silencieusement la
        référence. La règle CARSO (« les dépendants ne sont pas détruits ») est
        plus stricte que le schéma : la purge s'arrête sur cette liste.
        """
        stmt = select(Offre).where(Offre.modele_document_id == document_id)
        return list(self.session.scalars(stmt))
