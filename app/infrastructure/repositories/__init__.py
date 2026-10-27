"""Repositories — point d'import unique (instruction/09 : centraliser)."""

from app.infrastructure.repositories.agent_personnel import (
    ConfigurationAgentPersonnelRepository,
)
from app.infrastructure.repositories.agent_runtime import (
    ConfigurationRuntimeAgentRepository,
)
from app.infrastructure.repositories.agent_task import AgentTaskRepository
from app.infrastructure.repositories.appel import (
    AppelRepository,
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
from app.infrastructure.repositories.geo import (
    ItineraireCacheRepository,
    OsmVoieRepository,
    ParametresVoyageRepository,
)
from app.infrastructure.repositories.reinitialisation import (
    CodeReinitialisationRepository,
)
from app.infrastructure.repositories.session import SessionRepository
from app.infrastructure.repositories.skill import SkillRepository
from app.infrastructure.repositories.support_formation import (
    SupportFormationRepository,
)
from app.infrastructure.repositories.utilisateur import UtilisateurRepository

__all__ = [
    "AffectationEquipeRepository",
    "AgentTaskRepository",
    "AppelRepository",
    "ApprobationRepository",
    "AuditRepository",
    "BaseRepository",
    "BeneficiaireRepository",
    "BudgetRepository",
    "CodeReinitialisationRepository",
    "ConfigurationAgentPersonnelRepository",
    "ConfigurationRuntimeAgentRepository",
    "DocumentRepository",
    "EquipeRepository",
    "ItineraireCacheRepository",
    "LieuRepository",
    "ParametresVoyageRepository",
    "LigneBudgetRepository",
    "LotRepository",
    "MissionRepository",
    "ModeleDocumentRepository",
    "OffreRepository",
    "OrganisationRepository",
    "OsmVoieRepository",
    "ParticipationRepository",
    "PresenceRepository",
    "ServeurMcpRepository",
    "SessionRepository",
    "SkillRepository",
    "SurchargeAgentRepository",
    "SupportFormationRepository",
    "UtilisateurRepository",
]
