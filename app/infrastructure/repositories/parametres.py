"""Repositories des paramètres d'administration (surcharges d'agent, MCP).

Un repository par agrégat, comme partout ailleurs : la requête vit ici, la règle
métier dans le service applicatif.
"""

from __future__ import annotations

from sqlalchemy import select

from app.domain.agent_runtime import SurchargeAgent
from app.domain.mcp import ServeurMcp
from app.infrastructure.repositories.base import BaseRepository


class SurchargeAgentRepository(BaseRepository[SurchargeAgent]):
    """Réglages par agent — une seule ligne par ``agent_id``."""

    model = SurchargeAgent

    def get_par_agent(self, agent_id: str) -> SurchargeAgent | None:
        """Surcharge active d'un agent, ou ``None`` s'il suit son contrat."""
        stmt = select(SurchargeAgent).where(
            SurchargeAgent.agent_id == agent_id,
            SurchargeAgent.actif.is_(True),
        )
        return self.session.scalars(stmt).first()

    def get_toute_ligne(self, agent_id: str) -> SurchargeAgent | None:
        """Ligne de surcharge d'un agent, **active ou désactivée**.

        La contrainte ``uq_surcharges_agent_agent_id`` porte sur ``agent_id``,
        toutes lignes confondues : un ``actif=False`` (retour au contrat) reste
        physiquement en base. Toute écriture doit donc réutiliser cette ligne —
        réactiver ou mettre à jour — jamais en insérer une seconde, sinon
        ``UniqueViolation`` (bug 500 « Enregistrement impossible » des
        Paramètres → Agents).
        """
        stmt = select(SurchargeAgent).where(SurchargeAgent.agent_id == agent_id)
        return self.session.scalars(stmt).first()

    def lister_actives(self) -> list[SurchargeAgent]:
        """Toutes les surcharges actives, ordre stable (``agent_id``)."""
        stmt = (
            select(SurchargeAgent)
            .where(SurchargeAgent.actif.is_(True))
            .order_by(SurchargeAgent.agent_id)
        )
        return list(self.session.scalars(stmt).all())


class ServeurMcpRepository(BaseRepository[ServeurMcp]):
    """Connecteurs MCP déclarés (actifs ou non)."""

    model = ServeurMcp

    def get_par_nom(self, nom: str) -> ServeurMcp | None:
        """Connecteur par nom (unicité en base)."""
        stmt = select(ServeurMcp).where(ServeurMcp.nom == nom)
        return self.session.scalars(stmt).first()

    def lister(self, *, actifs_seulement: bool = False) -> list[ServeurMcp]:
        """Connecteurs, ordre stable (``nom``)."""
        stmt = select(ServeurMcp).order_by(ServeurMcp.nom)
        if actifs_seulement:
            stmt = stmt.where(ServeurMcp.actif.is_(True))
        return list(self.session.scalars(stmt).all())

    def lister_pour_agent(self, agent_id: str) -> list[ServeurMcp]:
        """Connecteurs actifs qui autorisent explicitement cet agent."""
        actifs = self.lister(actifs_seulement=True)
        return [serveur for serveur in actifs if agent_id in (serveur.agents or [])]


__all__ = ["ServeurMcpRepository", "SurchargeAgentRepository"]
