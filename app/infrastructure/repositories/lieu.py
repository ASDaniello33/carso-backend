"""Repository des lieux d'exécution (donnée distincte d'une mission)."""

from sqlalchemy import func, select

from app.domain.execution import Lieu
from app.infrastructure.repositories.base import BaseRepository


class LieuRepository(BaseRepository[Lieu]):
    model = Lieu

    def find_by_nom(self, nom: str) -> Lieu | None:
        """Lieu portant ce nom (comparaison insensible à la casse et aux espaces)."""
        normalise = nom.strip().lower()
        if not normalise:
            return None
        # ``trim`` (portable SQLite/PostgreSQL) + ``lower`` : espaces de bord et
        # casse neutralisés, sans inventer de règle de normalisation CARSO.
        stmt = select(Lieu).where(func.lower(func.trim(Lieu.nom)) == normalise)
        return self.session.scalars(stmt).first()

    def list_all(self) -> list[Lieu]:
        """Tous les lieux, ordre alphabétique (usage UI / sélecteur)."""
        stmt = select(Lieu).order_by(Lieu.nom)
        return list(self.session.scalars(stmt))

    def list_for_ville(self, ville: str) -> list[Lieu]:
        stmt = select(Lieu).where(Lieu.ville == ville)
        return list(self.session.scalars(stmt))


__all__ = ["LieuRepository"]
