"""Repositories budgets et lignes de budget."""

import uuid

from sqlalchemy import select

from app.domain.organization import Budget, LigneBudget
from app.infrastructure.repositories.base import BaseRepository


class BudgetRepository(BaseRepository[Budget]):
    model = Budget

    def list_for_mission(self, mission_id: uuid.UUID) -> list[Budget]:
        stmt = select(Budget).where(Budget.mission_id == mission_id)
        return list(self.session.scalars(stmt))

    def list_for_offre(self, offre_id: uuid.UUID) -> list[Budget]:
        stmt = select(Budget).where(Budget.offre_id == offre_id)
        return list(self.session.scalars(stmt))


class LigneBudgetRepository(BaseRepository[LigneBudget]):
    model = LigneBudget

    def list_for_budget(self, budget_id: uuid.UUID) -> list[LigneBudget]:
        stmt = (
            select(LigneBudget)
            .where(LigneBudget.budget_id == budget_id)
            .order_by(LigneBudget.ordre)
        )
        return list(self.session.scalars(stmt))
