"""Repositories des paramètres d'administration (surcharges d'agent, MCP).

Un repository par agrégat, comme partout ailleurs : la requête vit ici, la règle
métier dans le service applicatif.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.domain.agent_runtime import SurchargeAgent
from app.domain.mcp import AGENT_CONNECTEURS_PERSONNELS, ServeurMcp
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

    def get_par_nom(
        self, nom: str, *, utilisateur_id: UUID | None = None
    ) -> ServeurMcp | None:
        """Connecteur par nom **dans un périmètre de propriétaire**.

        L'unicité est devenue par propriétaire : deux comptes peuvent nommer
        leur connecteur ``github``. ``utilisateur_id=None`` désigne le
        périmètre **global** (admin) — ne renvoie donc pas le connecteur
        personnel d'un compte.
        """
        stmt = select(ServeurMcp).where(
            ServeurMcp.nom == nom,
            ServeurMcp.utilisateur_id.is_(None)
            if utilisateur_id is None
            else ServeurMcp.utilisateur_id == utilisateur_id,
        )
        return self.session.scalars(stmt).first()

    def existe_personnel(self, nom: str) -> bool:
        """Un connecteur **personnel** porte-t-il ce nom, quel que soit son compte ?

        L'unicite est par proprietaire : un nom peut coexister en global et en
        personnel. Cette question sert a interdire a l'administration de creer un
        connecteur **global** sous le nom d'un connecteur de compte : sans cette
        verification, un simple enregistrement creerait une seconde ligne
        partagee a tous, Cesarant silencieusement le choix du collaborateur.
        """
        stmt = select(func.count()).select_from(ServeurMcp).where(
            ServeurMcp.nom == nom,
            ServeurMcp.utilisateur_id.is_not(None),
        )
        return bool(self.session.scalar(stmt))

    def lister(
        self,
        actifs_seulement: bool = False,
        utilisateur_id: UUID | None = None,
        perimetre_global_seulement: bool = False,
    ) -> list[ServeurMcp]:
        """Connecteurs, ordre stable (``nom``), dans un périmètre de propriétaire.

        Sans ``utilisateur_id`` : périmètre **admin** (tous les connecteurs,
        y compris ceux des comptes). C'est le comportement historique, conservé
        pour les routes d'administration.

        Avec ``utilisateur_id`` : ce compte **plus** les globaux — jamais ceux
        d'un autre compte. Le filtre est fait en SQL, pas filtré ensuite en
        Python : un connecteur personnel d'autrui ne doit jamais être lu.
        """
        stmt = select(ServeurMcp).order_by(ServeurMcp.nom)
        if actifs_seulement:
            stmt = stmt.where(ServeurMcp.actif.is_(True))
        if perimetre_global_seulement:
            stmt = stmt.where(ServeurMcp.utilisateur_id.is_(None))
        elif utilisateur_id is not None:
            stmt = stmt.where(
                (ServeurMcp.utilisateur_id.is_(None))
                | (ServeurMcp.utilisateur_id == utilisateur_id)
            )
        return list(self.session.scalars(stmt).all())

    def lister_pour_agent(
        self, agent_id: str, *, utilisateur_id: UUID | None = None
    ) -> list[ServeurMcp]:
        """Connecteurs **effectifs** d'un agent dans le contexte d'un compte.

        ``utilisateur_id=None`` = contexte global administré (comportement
        historique, inchangé). Renseigné : globaux ∪ personnels de ce compte.

        Un connecteur **personnel** ne charge que ``AGENT_CONNECTEURS_PERSONNELS``
        (l'agent personnel), même si sa ligne nomme encore un agent partagé : la
        règle est ici volontairement redondante avec la validation d'écriture et
        la migration — c'est la lecture qui décide réellement des outils
        chargés dans un graphe, et une ligne héritée d'un ancien rattachement ne
        doit jamais pouvoir réinjecter ses outils dans un agent CARSO.
        """
        if utilisateur_id is None:
            actifs = self.lister(actifs_seulement=True)
        else:
            actifs = self.lister(actifs_seulement=True, utilisateur_id=utilisateur_id)
        return [
            serveur
            for serveur in actifs
            if agent_id in (serveur.agents or [])
            and (
                serveur.utilisateur_id is None
                or agent_id == AGENT_CONNECTEURS_PERSONNELS
            )
        ]


__all__ = ["ServeurMcpRepository", "SurchargeAgentRepository"]
