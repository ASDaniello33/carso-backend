"""Schémas API—paramètres d'administration (surcharges d'agent, skills, MCP).

Deux règles, appliquées partout ici :

- un **secret entre, ne sort jamais**. Les en-têtes d'authentification d'un
  connecteur MCP sont acceptés en écriture (``en_tetes``) et la réponse n'en
  donne que les **noms** ;
- une **surcharge ne remplace pas un contrat**—elle le complète. Les schémas
  portent donc le contrat *et* la surcharge, pour que l'interface affiche les
  outils disponibles avant de proposer d'en désactiver un.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SurchargeAgentRead(BaseModel):
    """Réglages administrés effectifs d'un agent."""

    agent_id: str
    outils_desactives: list[str] = Field(default_factory=list)
    instructions_supplementaires: str | None = None
    skills_ajoutes: list[str] = Field(default_factory=list)
    actif: bool = True
    modifie_par: str | None = None
    updated_at: datetime | None = None


class SurchargeAgentUpdate(BaseModel):
    """Nouvelle surcharge d'un agent (remplace la précédente)."""

    outils_desactives: list[str] = Field(default_factory=list)
    instructions_supplementaires: str | None = Field(default=None, max_length=20_000)
    skills_ajoutes: list[str] = Field(default_factory=list)


class AgentParametrableRead(BaseModel):
    """Agent vu par l'écran Paramètres : contrat, surcharge, disponibilité."""

    agent_id: str
    display_name: str
    description: str
    expose_ui: bool = Field(description="Agent du catalogue AG-UI (les autres sont internes)")
    outils_du_contrat: list[str]
    outils_effectifs: list[str]
    outils_desactives: list[str] = Field(default_factory=list)
    skills_du_contrat: list[str] = Field(default_factory=list)
    skills_effectifs: list[str] = Field(default_factory=list)
    instructions_supplementaires: str | None = None
    approval_required: list[str] = Field(default_factory=list)
    surcharge_active: bool = False
    connecteurs_mcp: list[str] = Field(default_factory=list)


class SkillsDisponiblesRead(BaseModel):
    """Skills installables (un répertoire ``SKILL.md`` par skill)."""

    racine: str
    disponibles: list[str] = Field(default_factory=list)


class SondeMcpRead(BaseModel):
    """Dernier résultat **réel** de connexion à un connecteur (lecture seule).

    Une sonde ne dit pas ce qui *devrait* marcher : elle dit ce qui a répondu.
    C'est la seule façon de distinguer un connecteur sain d'un connecteur
    enregistré mais jamais utilisable — l'écart constaté le 05/10 sur un serveur
    exigeant OAuth (401 à chaque chargement, connecteur affiché « actif »).
    """

    le: datetime
    ok: bool
    duree_ms: int = 0
    nb_outils: int = 0
    outils: list[str] = Field(default_factory=list)
    erreur: str | None = None
    causes: list[str] = Field(default_factory=list)


class AutorisationMcpRead(BaseModel):
    """Autorisation OAuth à ouvrir dans le navigateur.

    ``url_autorisation`` est l'URL de consentement du serveur MCP (déjà porteuse
    du client, du PKCE et de l'état) ; ``expire_le`` borne l'attente côté
    backend. Aucun secret n'y figure : un jeton n'existera qu'après le retour du
    navigateur sur la route de rappel.
    """

    url_autorisation: str
    expire_le: datetime


class ServeurMcpRead(BaseModel):
    """Connecteur MCP tel qu'exposé à l'interface (en-têtes par leur nom).

    Construit explicitement depuis ``ServeurMcp.resume()`` : ``en_tetes`` ne
    correspond à aucune colonne, c'est la liste des noms déchiffrables.
    """

    id: UUID
    nom: str
    transport: str
    url: str | None = None
    commande: str | None = None
    arguments: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    en_tetes: list[str] = Field(default_factory=list)
    actif: bool = True
    modifie_par: str | None = None
    updated_at: datetime | None = None
    #: Connecteur **personnel** (incr. 46) : la reponse melange les globaux
    #: herites et ceux du compte appelant, ce booleen dit lesquels sont
    #: modifiables. L'identite du proprietaire n'est volontairement pas exposee
    #: : un compte ne voit que les connecteurs d'autrui a travers son perimetre,
    #: et l'administration ne liste que ce qu'elle peut agir dessus.
    personnel: bool = False
    #: ``aucune`` | ``en_tetes`` | ``oauth`` (incr. 47).
    mode_authentification: str = "aucune"
    #: Client OAuth **pré-enregistré** — public par nature (jamais un secret).
    oauth_client_id: str | None = None
    #: ``non_requis`` | ``jamais`` | ``en_attente`` | ``autorise`` | ``erreur``.
    oauth_statut: str = "non_requis"
    oauth_derniere_erreur: str | None = None
    #: Dernière sonde réellement exécutée, ou ``None`` si aucune.
    sonde: SondeMcpRead | None = None


class McpPersonnelRead(BaseModel):
    """Surface **personnelle** : ce que le compte a déclaré, plus ce qu'il hérite.

    Réponse d'un objet et non d'une liste : l'écran doit aussi savoir s'il
    peut saisir un en-tête d'authentification. Sans ce drapeau, le collaborateur
    composerait son jeton pour se le faire refuser — l'écart est expliquable
    avant la saisie, pas après.
    """

    chiffrement_disponible: bool = Field(
        description="Un en-tête d'authentification peut être chiffré au repos."
    )
    #: URI de rappel OAuth du backend (incr. 47). Affichée dans le mode OAuth :
    #: un serveur qui n'accepte que des clients connus (Meta) exige qu'on la lui
    #: déclare, et la faire deviner serait la seule étape non vérifiable.
    uri_redirection: str = ""
    connecteurs: list[ServeurMcpRead] = Field(
        default_factory=list,
        description="Globaux hérités + personnels du compte, triés par nom. "
        "`personnel` distingue ce que l'écran peut modifier.",
    )


class ServeurMcpUpdate(BaseModel):
    """Déclaration d'un connecteur MCP (écriture seule pour les en-têtes).

    ``en_tetes`` remplace intégralement les en-têtes existants ; laisser ``None``
    les conserve tels quels (changer d'URL ne doit pas obliger à ressaisir un
    jeton). Même règle pour ``mode_authentification`` et ``oauth_client_id`` :
    ``None`` conserve, une chaîne vide **efface** le client pré-enregistré.

    ``mode_authentification = "oauth"`` suppose un transport réseau : un serveur
    lancé localement (``stdio``) reçoit ses identifiants de l'environnement, il
    n'y a pas de navigateur à ouvrir (spécification MCP, autorisation).
    """

    transport: str = Field(min_length=1, max_length=30)
    url: str | None = Field(default=None, max_length=1000)
    commande: str | None = Field(default=None, max_length=500)
    arguments: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    en_tetes: dict[str, str] | None = None
    mode_authentification: str | None = Field(default=None, max_length=20)
    oauth_client_id: str | None = Field(default=None, max_length=255)
    actif: bool = True


class ParametresEtatRead(BaseModel):
    """Ce dont l'écran a besoin pour savoir quoi afficher dès l'ouverture."""

    chiffrement_disponible: bool = Field(
        description="Un secret peut être enregistré (clé de chiffrement configurée)"
    )
    fournisseurs_supportes: list[str] = Field(default_factory=list)
    agents: list[AgentParametrableRead] = Field(default_factory=list)
    connecteurs_mcp: list[ServeurMcpRead] = Field(default_factory=list)
    skills: SkillsDisponiblesRead


__all__ = [
    "AgentParametrableRead",
    "AutorisationMcpRead",
    "ParametresEtatRead",
    "ServeurMcpRead",
    "ServeurMcpUpdate",
    "SkillsDisponiblesRead",
    "SondeMcpRead",
    "SurchargeAgentRead",
    "SurchargeAgentUpdate",
]
