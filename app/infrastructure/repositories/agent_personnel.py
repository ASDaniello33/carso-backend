"""Repository de la configuration personnelle de l'agent personnel.

Une ligne par compte—ce repository ne fait aucune règle métier (validation des
outils, refus, audit)—elle vit dans ``AgentPersonnelService``. Il ne fait pas
de commit non plus—la transaction appartient au service appelant.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.domain.agent_personnel import ConfigurationAgentPersonnel
from app.infrastructure.repositories.base import BaseRepository


class ConfigurationAgentPersonnelRepository(BaseRepository[ConfigurationAgentPersonnel]):
    """Configuration personnelle — lecture par compte, ligne unique réutilisée."""

    model = ConfigurationAgentPersonnel

    def get_pour_utilisateur(self, utilisateur_id: UUID) -> ConfigurationAgentPersonnel | None:
        """Configuration **active** d'un compte, ou ``None`` s'il suit son contrat."""
        stmt = select(ConfigurationAgentPersonnel).where(
            ConfigurationAgentPersonnel.utilisateur_id == utilisateur_id,
            ConfigurationAgentPersonnel.actif.is_(True),
        )
        return self.session.scalars(stmt).first()

    def get_toute_ligne(self, utilisateur_id: UUID) -> ConfigurationAgentPersonnel | None:
        """Ligne d'un compte, **active ou désactivée**.

        La contrainte unique porte sur ``utilisateur_id``, lignes désactivées
        comprises : toute écriture doit réutiliser cette ligne (réactiver ou
        mettre à jour), jamais en insérer une seconde — sinon ``UniqueViolation``
        au rétablissement après une réinitialisation.
        """
        stmt = select(ConfigurationAgentPersonnel).where(
            ConfigurationAgentPersonnel.utilisateur_id == utilisateur_id
        )
        return self.session.scalars(stmt).first()


__all__ = ["ConfigurationAgentPersonnelRepository"]
