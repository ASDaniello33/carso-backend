"""Repository des skills d'organisation — lecture et écriture de la ligne.

Les bornes (nombre, longueurs, nom déjà pris) appartiennent au service : ici,
on ne fait que lire et écrire la ligne demandée. Aucun filtre par compte : un
skill n'appartient à personne, il est porté par le prompt de tous les runs.
"""

from __future__ import annotations

from sqlalchemy import select

from app.domain.skill import Skill
from app.infrastructure.repositories.base import BaseRepository


class SkillRepository(BaseRepository[Skill]):
    """Skills de l'organisation, triés par nom."""

    model = Skill

    def lister(self) -> list[Skill]:
        """Tous les skills (ordre alphabétique, stable)."""
        return list(self.session.scalars(select(Skill).order_by(Skill.nom)))

    def get_par_nom(self, nom: str) -> Skill | None:
        """Skill par son nom canonique, ou ``None``."""
        return self.session.scalars(select(Skill).where(Skill.nom == nom)).first()


__all__ = ["SkillRepository"]
