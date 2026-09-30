"""Service des paramètres d'administration — surcharges d'agent et connecteurs MCP.

Deux responsabilités, un même principe : l'administrateur règle le **comportement**
sans jamais élargir les droits.

- une **surcharge d'agent** retire des outils du contrat, ajoute des consignes et
  des skills. Elle refuse un outil hors contrat (liste des outils valides dans
  l'erreur) : « désactiver » un outil que l'agent n'a pas est une
  incompréhension, pas un réglage ;
- un **connecteur MCP** est rattaché à des agents nommés. Les en-têtes
  d'authentification sont chiffrés au repos et l'API n'en expose que les noms.

Aucun secret ne sort de ce module en clair : ni par la projection (``resume``),
ni par l'audit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.surcharges import definition_effective, outils_hors_contrat
from app.application.trace import TraceContext
from app.core.chiffrement import chiffrement_disponible, chiffrer, dechiffrer
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.agent_runtime import SurchargeAgent
from app.domain.enums import ActionAudit, ActorType
from app.domain.mcp import TRANSPORTS_MCP, ServeurMcp
from app.infrastructure.repositories import ServeurMcpRepository, SurchargeAgentRepository

_ENTITY_SURCHARGE = "surcharge_agent"
_ENTITY_MCP = "serveur_mcp"


def definition_du_contrat(agent_id: str) -> Any:
    """Contrat déclaré d'un agent, ou ``NotFoundError`` explicite."""
    for definition in AGENT_DEFINITIONS:
        if definition.agent_id == agent_id:
            return definition
    raise NotFoundError(
        f"Agent inconnu : {agent_id!r}",
        details={"agents": [d.agent_id for d in AGENT_DEFINITIONS]},
    )


class ParametresAgentService:
    """Surcharges par agent : lire, enregistrer, supprimer."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.surcharges = SurchargeAgentRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def surcharge(self, agent_id: str) -> SurchargeAgent | None:
        """Surcharge active d'un agent (``None`` s'il suit son contrat)."""
        definition_du_contrat(agent_id)
        return self.surcharges.get_par_agent(agent_id)

    def effectives(self) -> dict[str, SurchargeAgent]:
        """Surcharges actives indexées par agent (pour le catalogue)."""
        return {ligne.agent_id: ligne for ligne in self.surcharges.lister_actives()}

    def enregistrer(
        self,
        agent_id: str,
        *,
        outils_desactives: list[str],
        instructions_supplementaires: str | None,
        skills_ajoutes: list[str],
        modifie_par: str | None = None,
    ) -> SurchargeAgent:
        """Crée ou remplace la surcharge d'un agent (une seule ligne par agent).

        Raises:
            NotFoundError: agent ou skill inconnu.
            BusinessValidationError: outil hors contrat, nom de skill invalide.
        """
        definition = definition_du_contrat(agent_id)
        retires = sorted({nom for nom in outils_desactives if nom})
        hors_contrat = outils_hors_contrat(definition, retires)
        if hors_contrat:
            raise BusinessValidationError(
                f"Outil hors contrat pour {agent_id!r} : {', '.join(hors_contrat)}",
                details={
                    "agent_id": agent_id,
                    "outils_hors_contrat": hors_contrat,
                    "outils_du_contrat": sorted(definition.tools),
                },
            )
        skills = self._valider_skills(skills_ajoutes)

        # La contrainte unique porte sur agent_id, lignes désactivées comprises :
        # on réutilise la ligne existante (même inactive) au lieu d'en insérer
        # une seconde — sinon UniqueViolation au flush (ré-enregistrement après
        # un « retour au contrat »).
        ligne = self.surcharges.get_toute_ligne(agent_id)
        avant = ligne.resume() if ligne is not None else None
        if ligne is None:
            ligne = SurchargeAgent(agent_id=agent_id, actif=True)
            self.surcharges.add(ligne)

        ligne.outils_desactives = retires
        ligne.instructions_supplementaires = (
            instructions_supplementaires.strip() if instructions_supplementaires else None
        )
        ligne.skills_ajoutes = skills
        ligne.actif = True
        ligne.modifie_par = modifie_par
        self._session.flush()

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SURCHARGE_AGENT_MODIFIEE.value,
            entity_type=_ENTITY_SURCHARGE,
            entity_id=ligne.id,
            after=ligne.resume(),
            event_metadata={"avant": avant},
        )
        return ligne

    def supprimer(self, agent_id: str) -> bool:
        """Rend l'agent à son contrat (désactive sa surcharge).

        Returns:
            ``True`` si une surcharge existait, ``False`` sinon.
        """
        definition_du_contrat(agent_id)
        ligne = self.surcharges.get_par_agent(agent_id)
        if ligne is None:
            return False
        avant = ligne.resume()
        ligne.actif = False
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SURCHARGE_AGENT_MODIFIEE.value,
            entity_type=_ENTITY_SURCHARGE,
            entity_id=ligne.id,
            after={**avant, "actif": False},
            event_metadata={"avant": avant, "raison": "retour au contrat de l'agent"},
        )
        return True

    @staticmethod
    def _valider_skills(noms: list[str]) -> list[str]:
        """Valide des noms de skills contre le registre (``SKILL.md`` présent)."""
        from app.agents.skills import lister_skills

        demandes = sorted({nom for nom in noms if nom})
        if not demandes:
            return []
        disponibles = lister_skills()
        inconnus = [nom for nom in demandes if nom not in disponibles]
        if inconnus:
            raise BusinessValidationError(
                f"Skill inconnu : {', '.join(inconnus)}",
                details={"skills_inconnus": inconnus, "disponibles": disponibles},
            )
        return demandes


class ServeurMcpService:
    """Connecteurs MCP : déclarer, modifier, activer/désactiver, supprimer."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.serveurs = ServeurMcpRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def lister(self) -> list[ServeurMcp]:
        """Tous les connecteurs, actifs ou non."""
        return self.serveurs.lister()

    def lister_pour_agent(self, agent_id: str) -> list[ServeurMcp]:
        """Connecteurs actifs autorisant explicitement cet agent."""
        return self.serveurs.lister_pour_agent(agent_id)

    def enregistrer(
        self,
        *,
        nom: str,
        transport: str,
        url: str | None,
        commande: str | None,
        arguments: list[str],
        agents: list[str],
        en_tetes: dict[str, str] | None = None,
        actif: bool = True,
        modifie_par: str | None = None,
    ) -> ServeurMcp:
        """Crée ou met à jour un connecteur (identifié par son nom).

        Raises:
            NotFoundError: un agent listé n'existe pas.
            BusinessValidationError: transport inconnu, URL/commande manquante,
                ou en-têtes fournis alors que le chiffrement est indisponible.
        """
        nom_n = (nom or "").strip()
        if not nom_n:
            raise BusinessValidationError("Nom du connecteur obligatoire")
        transport_n = (transport or "").strip().lower()
        if transport_n not in TRANSPORTS_MCP:
            raise BusinessValidationError(
                f"Transport MCP inconnu : {transport_n!r}",
                details={"transports": sorted(TRANSPORTS_MCP)},
            )
        url_n = (url or "").strip() or None
        commande_n = (commande or "").strip() or None
        if transport_n == "stdio" and not commande_n:
            raise BusinessValidationError(
                "Un connecteur stdio doit déclarer la commande à lancer"
            )
        if transport_n != "stdio" and not url_n:
            raise BusinessValidationError(
                "Un connecteur réseau doit déclarer son URL"
            )

        agents_n = sorted({identifiant for identifiant in agents if identifiant})
        for identifiant in agents_n:
            definition_du_contrat(identifiant)

        ligne = self.serveurs.get_par_nom(nom_n)
        avant = ligne.resume() if ligne is not None else None
        if ligne is None:
            ligne = ServeurMcp(nom=nom_n)
            self.serveurs.add(ligne)

        ligne.transport = transport_n
        ligne.url = url_n
        ligne.commande = commande_n
        ligne.arguments = [str(a) for a in arguments if str(a).strip()]
        ligne.agents = agents_n
        ligne.actif = bool(actif)
        ligne.modifie_par = modifie_par
        if en_tetes:
            if not chiffrement_disponible():
                raise BusinessValidationError(
                    "Aucune clé de chiffrement configurée : renseigner "
                    "PARAMETRES_CHIFFREMENT_KEY (ou AUTH_SECRET) avant "
                    "d'enregistrer des en-têtes d'authentification.",
                    details={"champ": "en_tetes"},
                )
            import json

            ligne.en_tetes_chiffres = chiffrer(json.dumps(en_tetes, ensure_ascii=False))

        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SERVEUR_MCP_MODIFIE.value,
            entity_type=_ENTITY_MCP,
            entity_id=ligne.id,
            after=ligne.resume(noms_en_tetes=self.noms_en_tetes(ligne)),
            event_metadata={"avant": avant},
        )
        return ligne

    def supprimer(self, nom: str) -> bool:
        """Supprime un connecteur et trace l'opération."""
        ligne = self.serveurs.get_par_nom(nom)
        if ligne is None:
            return False
        avant = ligne.resume(noms_en_tetes=self.noms_en_tetes(ligne))
        self.serveurs.delete(ligne)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SERVEUR_MCP_SUPPRIME.value,
            entity_type=_ENTITY_MCP,
            entity_id=None,
            after=avant,
        )
        return True

    @staticmethod
    def noms_en_tetes(serveur: ServeurMcp) -> list[str]:
        """Noms des en-têtes configurés — jamais leurs valeurs."""
        import json

        brut = dechiffrer(serveur.en_tetes_chiffres)
        if not brut:
            return []
        try:
            contenu = json.loads(brut)
        except (TypeError, ValueError):
            return []
        return sorted(str(cle) for cle in contenu) if isinstance(contenu, dict) else []

    @staticmethod
    def en_tetes(serveur: ServeurMcp) -> dict[str, str]:
        """En-têtes déchiffrés, pour la construction du client MCP uniquement.

        Un jeton illisible (clé de chiffrement changée) rend un dictionnaire vide
        plutôt qu'un connecteur à moitié authentifié : l'appel échouera côté
        serveur, avec un message clair, au lieu d'envoyer un en-tête corrompu.
        """
        import json

        brut = dechiffrer(serveur.en_tetes_chiffres)
        if not brut:
            return {}
        try:
            contenu = json.loads(brut)
        except (TypeError, ValueError):
            return {}
        return {str(cle): str(valeur) for cle, valeur in contenu.items()}


def definition_avec_surcharge(agent_id: str, surcharges: dict[str, SurchargeAgent]) -> Any:
    """Contrat effectif d'un agent selon les surcharges connues.

    Fonction libre : le catalogue et les routes l'appellent sans instancier de
    service (aucune session nécessaire).
    """
    definition = definition_du_contrat(agent_id)
    return definition_effective(definition, surcharges.get(agent_id))


__all__ = [
    "ParametresAgentService",
    "ServeurMcpService",
    "definition_avec_surcharge",
    "definition_du_contrat",
]
