"""Service des paramètres d'administration—surcharges d'agent et connecteurs MCP.

Deux responsabilités, un même principe—l'administrateur règle le **comportement**
sans jamais élargir les droits.

- une **surcharge d'agent** retire des outils du contrat, ajoute des consignes et
  des skills. Elle refuse un outil hors contrat (liste des outils valides dans
  l'erreur)—« désactiver » un outil que l'agent n'a pas est une
  incompréhension, pas un réglage ;
- un **connecteur MCP** est rattaché à des agents nommés. Les en-têtes
  d'authentification sont chiffrés au repos et l'API n'en expose que les noms.

Aucun secret ne sort de ce module en clair—ni par la projection (``resume``),
ni par l'audit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.surcharges import definition_effective, outils_hors_contrat
from app.application.trace import TraceContext
from app.core.chiffrement import chiffrement_disponible, chiffrer, dechiffrer
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.agent_runtime import SurchargeAgent
from app.domain.enums import ActionAudit, ActorType
from app.domain.mcp import (
    AGENT_CONNECTEURS_PERSONNELS,
    MODES_AUTHENTIFICATION_MCP,
    TRANSPORTS_MCP,
    ServeurMcp,
)
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
        from app.agents.registre_skills import lister_skills

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

    def lister(
        self,
        *,
        utilisateur_id: UUID | None = None,
        perimetre_global_seulement: bool = False,
    ) -> list[ServeurMcp]:
        """Connecteurs d'un perimetre de proprietaire (actifs ou non).

        Sans argument : perimetre **admin** (tous les connecteurs) —
        comportement historique des routes d'administration.

        Avec ``utilisateur_id`` : ce compte **plus** les globaux. Avec
        ``perimetre_global_seulement`` : uniquement les globaux, pour lister
        ce qu'un compte herite du Runtime partage.
        """
        return self.serveurs.lister(
            utilisateur_id=utilisateur_id,
            perimetre_global_seulement=perimetre_global_seulement,
        )

    def lister_pour_agent(
        self, agent_id: str, *, utilisateur_id: UUID | None = None
    ) -> list[ServeurMcp]:
        """Connecteurs actifs autorisant cet agent, dans le perimetre du compte."""
        return self.serveurs.lister_pour_agent(agent_id, utilisateur_id=utilisateur_id)

    def nom_pris_par_un_compte(self, nom: str) -> bool:
        """Ce nom est-il deja porte par le connecteur **personnel** d'un compte ?

        Reserve a l'administration : un nom existe desormais dans deux espaces
        (global et personnel). Ecrire en global sous un nom deja personnel
        creerait une seconde ligne, partagee a tous, sans modifier celle du
        collaborateur -- une escalade de perimetre silencieuse. La regle est donc
        refusee, pas resolue par un tri de priorite.
        """
        return self.serveurs.existe_personnel((nom or "").strip())

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
        utilisateur_id: UUID | None = None,
        mode_authentification: str | None = None,
        oauth_client_id: str | None = None,
    ) -> ServeurMcp:
        """Crée ou met à jour un connecteur (identifié par son nom).

        Raises:
            NotFoundError: un agent listé n'existe pas.
            BusinessValidationError: transport inconnu, URL/commande manquante,
                mode d'authentification inconnu, OAuth sur un transport local,
                secret fourni alors que le chiffrement est indisponible, ou
                connecteur personnel nommant un agent autre que l'agent
                personnel.
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

        # Mode d'authentification (incr. 47). ``None`` conserve l'existant :
        # modifier l'URL d'un connecteur OAuth ne doit pas le faire retomber
        # silencieusement en mode ouvert.
        mode_n: str | None = None
        if mode_authentification is not None:
            mode_n = (mode_authentification or "").strip().lower()
            if mode_n not in MODES_AUTHENTIFICATION_MCP:
                raise BusinessValidationError(
                    f"Mode d'authentification MCP inconnu : {mode_n!r}",
                    details={"modes": sorted(MODES_AUTHENTIFICATION_MCP)},
                )
            if mode_n == "oauth":
                if transport_n == "stdio":
                    raise BusinessValidationError(
                        "OAuth suppose un transport réseau : un serveur stdio "
                        "reçoit ses identifiants de l'environnement (spécification "
                        "MCP, autorisation).",
                        details={"champ": "mode_authentification"},
                    )
                if not chiffrement_disponible():
                    raise BusinessValidationError(
                        "OAuth stocke des jetons en base : renseigner "
                        "PARAMETRES_CHIFFREMENT_KEY (ou AUTH_SECRET) avant de "
                        "l'activer.",
                        details={"champ": "mode_authentification"},
                    )

        agents_n = sorted({identifiant for identifiant in agents if identifiant})
        if utilisateur_id is not None:
            # Un connecteur personnel n'alimente que l'agent personnel : ses
            # outils distants, ses pannes et ses jetons ne touchent jamais un
            # agent partagé de CARSO (décision 10/10). Un nom d'agent partagé
            # est **refusé** plutôt que silencieusement remplacé — l'auteur
            # saurait sinon avoir configuré autre chose que ce qui est écrit.
            agents_n = self._agents_du_connecteur_personnel(agents_n)
        for identifiant in agents_n:
            definition_du_contrat(identifiant)

        # Portee de proprietaire : sans `utilisateur_id`, ecriture
        # **globale** (administrateur). Renseigne, la ligne appartient a ce
        # compte et n'est atteignable que depuis son perimetre — jamais
        # depuis celui d'un autre, ni depuis l'administration.
        ligne = self.serveurs.get_par_nom(nom_n, utilisateur_id=utilisateur_id)
        avant = ligne.resume() if ligne is not None else None
        if ligne is None:
            # Le proprietaire est pose **des la creation** : l'oublier ferait
            # tomber un connecteur personnel dans l'espace global partage
            # (fuite vers tous les comptes) — et ferait echouer l'unicite
            # globale au second enregistrement du meme nom.
            ligne = ServeurMcp(nom=nom_n, utilisateur_id=utilisateur_id)
            self.serveurs.add(ligne)

        ligne.transport = transport_n
        ligne.url = url_n
        ligne.commande = commande_n
        ligne.arguments = [str(a) for a in arguments if str(a).strip()]
        ligne.agents = agents_n
        ligne.actif = bool(actif)
        ligne.modifie_par = modifie_par
        if mode_n is not None:
            ligne.mode_authentification = mode_n
            # L'état d'autorisation suit le mode : hors OAuth il n'a pas de sens,
            # et en OAuth une ligne jamais autorisée doit le dire (sinon l'écran
            # afficherait « autorisé » pour un mode qu'on vient de quitter).
            ligne.oauth_statut = "jamais" if mode_n == "oauth" else "non_requis"
            if mode_n != "oauth":
                ligne.oauth_derniere_erreur = None
        if oauth_client_id is not None:
            # Chaîne vide = effacement explicite (l'interface ne peut pas
            # distinguer « champ inchangé » d'un effacement en envoyant ``None``).
            ligne.oauth_client_id = (oauth_client_id or "").strip() or None
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

    @staticmethod
    def _agents_du_connecteur_personnel(demandes: list[str]) -> list[str]:
        """Agents d'un connecteur **personnel** : l'agent personnel, rien d'autre.

        Le défaut (liste vide) est l'agent personnel : l'écran n'a donc rien à
        cocher pour que le connecteur serve à quelque chose. Toute liste qui
        nomme un autre agent est refusée avec la raison et la liste valide.

        Raises:
            BusinessValidationError: un agent autre que l'agent personnel est
                demandé (agents partagés compris).
        """
        autres = sorted(
            nom for nom in demandes if nom != AGENT_CONNECTEURS_PERSONNELS
        )
        if autres:
            raise BusinessValidationError(
                "Un connecteur personnel ne peut alimenter que l'agent "
                "personnel : les agents CARSO partagés gardent leurs outils.",
                details={
                    "agents_refuses": autres,
                    "agents_autorises": [AGENT_CONNECTEURS_PERSONNELS],
                },
            )
        return [AGENT_CONNECTEURS_PERSONNELS]

    def supprimer(self, nom: str, *, utilisateur_id: UUID | None = None) -> bool:
        """Supprime un connecteur **de ce perimetre** et trace l'operation.

        Sans ``utilisateur_id`` : suppression globale (administration).
        Avec : seul un connecteur appartenant au compte est atteignable.
        """
        ligne = self.serveurs.get_par_nom(nom, utilisateur_id=utilisateur_id)
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
    def sonde_lue(serveur: ServeurMcp) -> dict[str, Any] | None:
        """Dernière sonde d'un connecteur, ou ``None`` si absente ou illisible.

        La sonde n'est pas un secret (noms d'outils, erreur, horodatage) : elle
        est stockée en clair et relue telle quelle. Un contenu illisible est
        traité comme absent — jamais comme un succès.
        """
        import json

        if not serveur.derniere_sonde:
            return None
        try:
            contenu = json.loads(serveur.derniere_sonde)
        except (TypeError, ValueError):
            return None
        return contenu if isinstance(contenu, dict) else None

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
