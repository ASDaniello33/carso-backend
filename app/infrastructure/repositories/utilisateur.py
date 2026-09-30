"""Repository des comptes utilisateurs."""

from sqlalchemy import select

from app.domain.identity import Utilisateur
from app.infrastructure.repositories.base import BaseRepository


class UtilisateurRepository(BaseRepository[Utilisateur]):
    model = Utilisateur

    def get_by_email(self, email: str) -> Utilisateur | None:
        stmt = select(Utilisateur).where(Utilisateur.email == email.lower().strip())
        return self.session.scalars(stmt).first()
