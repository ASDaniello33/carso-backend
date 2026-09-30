"""Repository de la configuration runtime des agents."""

from sqlalchemy import select

from app.domain.agent_runtime import ConfigurationRuntimeAgent
from app.infrastructure.repositories.base import BaseRepository


class ConfigurationRuntimeAgentRepository(BaseRepository[ConfigurationRuntimeAgent]):
    model = ConfigurationRuntimeAgent

    def get_active(self) -> ConfigurationRuntimeAgent | None:
        """Configuration appliquée au runtime, ou ``None`` si jamais configurée.

        Déterministe même si l'historique est incohérent : la plus récente
        configuration active gagne.
        """
        stmt = (
            select(ConfigurationRuntimeAgent)
            .where(ConfigurationRuntimeAgent.actif.is_(True))
            .order_by(ConfigurationRuntimeAgent.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def list_recent(self, limit: int = 20) -> list[ConfigurationRuntimeAgent]:
        """Historique, du plus récent au plus ancien."""
        stmt = (
            select(ConfigurationRuntimeAgent)
            .order_by(ConfigurationRuntimeAgent.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())
