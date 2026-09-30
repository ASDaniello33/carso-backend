"""Repository des tâches inter-agents (instruction/04 : agent_tasks, sans secrets)."""


from sqlalchemy import select

from app.domain.document import AgentTask
from app.infrastructure.repositories.base import BaseRepository


class AgentTaskRepository(BaseRepository[AgentTask]):
    model = AgentTask

    def get_by_correlation(self, correlation_id: str) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(AgentTask.correlation_id == correlation_id)
            .order_by(AgentTask.created_at)
        )
        return list(self.session.scalars(stmt))
