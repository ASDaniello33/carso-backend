"""Repository des codes de réinitialisation « mot de passe oublié »."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.domain.identity import CodeReinitialisation
from app.infrastructure.repositories.base import BaseRepository


class CodeReinitialisationRepository(BaseRepository[CodeReinitialisation]):
    model = CodeReinitialisation

    def dernier_actif(self, utilisateur_id: UUID) -> CodeReinitialisation | None:
        """Dernier code demandé par le compte (actif ou non)."""
        stmt = (
            select(CodeReinitialisation)
            .where(CodeReinitialisation.utilisateur_id == utilisateur_id)
            .order_by(CodeReinitialisation.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def invalider_actifs(self, utilisateur_id: UUID) -> None:
        """Marque consommés tous les codes non consommés du compte.

        Un compte ne porte qu'**un** code actif : chaque nouvelle demande
        invalide les précédents (pas d'empilement de codes valides).
        """
        codes = self.session.scalars(
            select(CodeReinitialisation).where(
                CodeReinitialisation.utilisateur_id == utilisateur_id,
                CodeReinitialisation.consomme_at.is_(None),
            )
        ).all()
        maintenant = datetime.now(UTC)
        for code in codes:
            code.consomme_at = maintenant
