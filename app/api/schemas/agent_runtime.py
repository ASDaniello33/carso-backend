"""Schémas API de la configuration runtime des agents (administration).

Un secret peut **entrer** (``api_key``, en écriture seule) mais n'en ressort
jamais : la réponse ne dit que si une clé administrée est enregistrée
(``cle_administree``), jamais sa valeur. La clé du ``.env`` reste le repli.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentRuntimeValuesRead(BaseModel):
    """Configuration effective appliquée au runtime."""

    provider: str
    model: str
    base_url: str | None = None
    temperature: float
    max_iterations: int
    source: str = Field(description="'base' (administrée) ou 'env' (déploiement)")
    cle_administree: bool = Field(
        default=False,
        description="Une clé de provider administrée est enregistrée (chiffrée en base)",
    )


class AgentRuntimeStateRead(BaseModel):
    """État complet du runtime d'agents."""

    configured: bool
    values: AgentRuntimeValuesRead | None = None
    agents: list[str]
    built_agents: list[str]
    last_error: str | None = None


class AgentRuntimeUpdate(BaseModel):
    """Nouvelle configuration à appliquer à chaud (admin).

    ``api_key`` est en **écriture seule** : fournie, elle est chiffrée avant
    écriture et n'est jamais relue ; absente, la clé déjà enregistrée est
    conservée (changer de modèle ne doit pas obliger à la ressaisir).
    ``conserver_cle = false`` retire la clé administrée (retour au ``.env``).
    """

    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=150)
    base_url: str | None = Field(default=None, max_length=500)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_iterations: int = Field(default=1000, ge=1, le=10_000)
    api_key: str | None = Field(default=None, max_length=500, repr=False)
    conserver_cle: bool = True


class AgentRuntimeTestRead(BaseModel):
    """Résultat d'un test de configuration (le modèle répond, ou pourquoi non)."""

    ok: bool
    provider: str
    model: str
    reponse: str | None = None
    erreur: str | None = None


class AgentRuntimeApplicationRead(BaseModel):
    """Résultat de l'application d'une configuration (changement ou rechargement)."""

    configured: bool
    values: AgentRuntimeValuesRead | None = None
    built_agents: list[str]
    last_error: str | None = None


class AgentRuntimeConfigRead(BaseModel):
    """Version persistée de la configuration (historique)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    model: str
    base_url: str | None
    temperature: float
    max_iterations: int
    actif: bool
    modifie_par: str | None
    created_at: datetime


class HitlReponseItem(BaseModel):
    """Réponse prédéfinie proposée par l'agent dans un questionnaire HITL."""

    valeur: str
    libelle: str
    description: str


class HitlScenarioItem(BaseModel):
    """Moment où l'agent interrompt et questionne l'utilisateur (contrat HITL).

    La réponse personnalisée (texte libre) est toujours disponible en plus des
    choix listés — elle n'a pas besoin d'être déclarée.
    """

    id: str
    titre: str
    contexte: str
    action: str
    reponses: list[HitlReponseItem]


class AgentCatalogueItem(BaseModel):
    """Description d'un agent exposé — introspection pour l'interface."""

    agent_id: str
    display_name: str
    description: str
    tools: list[str]
    capabilities: list[str]
    collaboration: list[str]
    skills: list[str]
    approval_required: list[str]
    output_schema_name: str | None = None
    hitl_scenarios: list[HitlScenarioItem] = []


__all__ = [
    "AgentCatalogueItem",
    "AgentRuntimeApplicationRead",
    "AgentRuntimeConfigRead",
    "AgentRuntimeStateRead",
    "AgentRuntimeTestRead",
    "AgentRuntimeUpdate",
    "AgentRuntimeValuesRead",
    "HitlReponseItem",
    "HitlScenarioItem",
]