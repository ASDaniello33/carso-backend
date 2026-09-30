"""Schémas API — paramètres d'administration (surcharges d'agent, skills, MCP).

Deux règles, appliquées partout ici :

- un **secret entre, ne sort jamais**. Les en-têtes d'authentification d'un
  connecteur MCP sont acceptés en écriture (``en_tetes``) et la réponse n'en
  donne que les **noms** ;
- une **surcharge ne remplace pas un contrat** : elle le complète. Les schémas
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


class ServeurMcpUpdate(BaseModel):
    """Déclaration d'un connecteur MCP (écriture seule pour les en-têtes).

    ``en_tetes`` remplace intégralement les en-têtes existants ; laisser ``None``
    les conserve tels quels (changer d'URL ne doit pas obliger à ressaisir un
    jeton).
    """

    transport: str = Field(min_length=1, max_length=30)
    url: str | None = Field(default=None, max_length=1000)
    commande: str | None = Field(default=None, max_length=500)
    arguments: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    en_tetes: dict[str, str] | None = None
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
    "ParametresEtatRead",
    "ServeurMcpRead",
    "ServeurMcpUpdate",
    "SkillsDisponiblesRead",
    "SurchargeAgentRead",
    "SurchargeAgentUpdate",
]
