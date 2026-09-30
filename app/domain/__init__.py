"""Domain layer — import all model modules so the declarative registry is complete.

Every module defining a ``Base`` subclass must be imported here: SQLAlchemy
resolves string relationship targets only after all classes are registered.
``migrations/env.py`` imports ``target_metadata`` from this package.
"""

from app.domain.agent_runtime import ConfigurationRuntimeAgent, SurchargeAgent
from app.domain.base import Base
from app.domain.document import AgentTask, Approbation, AuditEvent, Document
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    Equipe,
    Lieu,
    Mission,
    MissionOffre,
    ModeleDocument,
    Participation,
    Presence,
    SessionFormation,
    SupportFormation,
)
from app.domain.identity import Utilisateur
from app.domain.mcp import ServeurMcp
from app.domain.organization import (
    AppelAProposition,
    Budget,
    LigneBudget,
    Lot,
    Offre,
    Organisation,
)
from app.domain.social import (
    Annonce,
    Commentaire,
    Conversation,
    MembreConversation,
    Message,
    Reaction,
)

__all__ = [
    "AffectationEquipe",
    "AgentTask",
    "Annonce",
    "AppelAProposition",
    "Approbation",
    "AuditEvent",
    "Base",
    "Beneficiaire",
    "Budget",
    "Commentaire",
    "Conversation",
    "MembreConversation",
    "Message",
    "Reaction",
    "ConfigurationRuntimeAgent",
    "Document",
    "ServeurMcp",
    "SurchargeAgent",
    "Equipe",
    "Lieu",
    "LigneBudget",
    "Lot",
    "Mission",
    "MissionOffre",
    "ModeleDocument",
    "Offre",
    "Organisation",
    "Participation",
    "Presence",
    "SessionFormation",
    "SupportFormation",
    "Utilisateur",
]
