"""Repository des organisations clientes.

Utilisé comme ancre documentaire (``documents.organisation_id``) et comme
propriétaire des modèles de documents (``modeles_documents``).
"""

from __future__ import annotations

from sqlalchemy import select

from app.domain.organization import Organisation
from app.infrastructure.repositories.base import BaseRepository


class OrganisationRepository(BaseRepository[Organisation]):
    """Accès persistant aux organisations (lecture contrôlée, pas de commit)."""

    model = Organisation

    def get_by_nom(self, nom: str) -> Organisation | None:
        stmt = select(Organisation).where(Organisation.nom == nom)
        return self.session.scalars(stmt).first()
