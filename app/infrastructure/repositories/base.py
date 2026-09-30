"""Repositories — accès persistant typé, sans logique métier (instruction/09 §4).

Un repository ne fait **jamais** de commit ni de rollback : la transaction
appartient au service applicatif (instruction/04 §5 — atomicité des opérations
critiques). Un repository ne contient aucune règle de décision métier.
"""

import uuid
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.base import Base

M = TypeVar("M", bound=Base)


class BaseRepository(Generic[M]):
    """Base commune : session liée, add/get/list. Pas de transaction ici."""

    model: type[M]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, entity_id: uuid.UUID) -> M | None:
        """Charge une entité par clé primaire."""
        return self.session.get(self.model, entity_id)

    def add(self, entity: M) -> M:
        """Enregistre une entité nouvelle/modifiée dans la session courante."""
        self.session.add(entity)
        return entity

    def delete(self, entity: M) -> None:
        """Marque une entité pour suppression dans la session courante.

        Usage contrôlé : uniquement via un service métier qui trace l'action
        (audit) et applique ses garde-fous avant l'appel.
        """
        self.session.delete(entity)

    def list_all(self) -> list[M]:
        """Liste toutes les entités du modèle (usage contrôlé, tables modestes)."""
        return list(self.session.scalars(select(self.model)))
