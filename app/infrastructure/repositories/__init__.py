"""Repositories — point d'import unique (instruction/09 : centraliser)."""

from app.infrastructure.repositories.agent_runtime import (
    ConfigurationRuntimeAgentRepository,
)
from app.infrastructure.repositories.agent_task import AgentTaskRepository
from app.infrastructure.repositories.appel_a_proposition import (
    AppelAPropositionRepository,
    LotRepository,
)
from app.infrastructure.repositories.audit import ApprobationRepository, AuditRepository
from app.infrastructure.repositories.base import BaseRepository
from app.infrastructure.repositories.beneficiaire import (
    BeneficiaireRepository,
    ParticipationRepository,
    PresenceRepository,
)
from app.infrastructure.repositories.budget import BudgetRepository, LigneBudgetRepository
from app.infrastructure.repositories.document import (
    DocumentRepository,
    ModeleDocumentRepository,
)
from app.infrastructure.repositories.lieu import LieuRepository
from app.infrastructure.repositories.mission import (
    AffectationEquipeRepository,
    EquipeRepository,
    MissionRepository,
)
from app.infrastructure.repositories.offre import OffreRepository
from app.infrastructure.repositories.organisation import OrganisationRepository
from app.infrastructure.repositories.parametres import (
    ServeurMcpRepository,
    SurchargeAgentRepository,
)
from app.infrastructure.repositories.session import SessionRepository
from app.infrastructure.repositories.support_formation import (
    SupportFormationRepository,
)
from app.infrastructure.repositories.utilisateur import UtilisateurRepository

__all__ = [
    "AffectationEquipeRepository",
    "AgentTaskRepository",
    "AppelAPropositionRepository",
    "ApprobationRepository",
    "AuditRepository",
    "BaseRepository",
    "BeneficiaireRepository",
    "BudgetRepository",
    "ConfigurationRuntimeAgentRepository",
    "DocumentRepository",
    "EquipeRepository",
    "LieuRepository",
    "LigneBudgetRepository",
    "LotRepository",
    "MissionRepository",
    "ModeleDocumentRepository",
    "OffreRepository",
    "OrganisationRepository",
    "ParticipationRepository",
    "PresenceRepository",
    "ServeurMcpRepository",
    "SessionRepository",
    "SurchargeAgentRepository",
    "SupportFormationRepository",
    "UtilisateurRepository",
]
