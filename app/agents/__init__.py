"""Agents métier indépendants (instruction/05) exécutés par Deep Agents.

Pas de coordinateur central : chaque agent est instancié directement par
l'interface (ou par un autre agent via un tool) et reste propriétaire de son
contrat (``AgentDefinition``). Le **harnais** partagé
(``app/agents/harness.py``) transforme ce contrat en agent exécutable
``create_deep_agent``, avec allow-list de tools, backend sûr, approbations
humaines et sortie structurée.

Le modèle utilisé est une décision de déploiement
(``app/agents/providers.py`` : openai, google genai, endpoint compatible OpenAI) —
aucun agent ne le connaît. Les paquets ``deepagents``/``langchain`` sont
optionnels (extra ``agents``) : le backend démarre sans eux.
"""

from app.agents.agent_rh import DEFINITION as RH_DEFINITION
from app.agents.agent_rh import AgentRH, build_rh
from app.agents.agent_statistique import DEFINITION as STATISTIQUE_DEFINITION
from app.agents.agent_statistique import AgentStatistique, build_statistique
from app.agents.assistant_formateur import (
    DEFINITION as ASSISTANT_DEFINITION,
)
from app.agents.assistant_formateur import (
    AgentAssistantFormateur,
    build_assistant,
)
from app.agents.base import AgentDefinition, CollaborationGrant
from app.agents.collaboration import (
    GET_RESULT_CAPABILITY,
    REQUEST_TASK_CAPABILITY,
    AgentHandler,
    AgentHandlerRegistry,
    AgentRequest,
    AgentResponse,
    InterAgentGateway,
    build_collaboration_tools,
)
from app.agents.consultation import (
    compter_affectations_par_role,
    compter_beneficiaires_par_session,
    compter_missions_par_statut,
    rechercher_appels_a_proposition,
    rechercher_documents,
    rechercher_equipes,
    rechercher_missions,
    rechercher_organisations,
)
from app.agents.generaliste_readonly import (
    DEFINITION as GENERALISTE_DEFINITION,
)
from app.agents.generaliste_readonly import (
    AgentGeneralisteReadOnly,
    build_generaliste,
)
from app.agents.generateur_offre import (
    DEFINITION as GENERATEUR_DEFINITION,
)
from app.agents.generateur_offre import (
    AgentGenerateurOffre,
    build_generateur,
)
from app.agents.harness import (
    AgentRegistry,
    AgentRuntimeConfig,
    build_system_prompt,
    create_carso_deep_agent,
    recursion_limit,
)
from app.agents.providers import (
    SUPPORTED_PROVIDERS,
    AgentConfigurationError,
    build_chat_model,
    is_agent_configured,
)
from app.agents.runtime import AgentTaskService, strip_secrets
from app.agents.skills import lister_skills, racine_skills, resoudre_skills
from app.agents.toolkit import (
    KIT_CAPABILITY,
    AgentTools,
    OperationSpec,
    PageSearchInput,
    ouvrir_trace,
    serialiser_entite,
)

__all__ = [
    "ASSISTANT_DEFINITION",
    "GENERATEUR_DEFINITION",
    "GENERALISTE_DEFINITION",
    "RH_DEFINITION",
    "STATISTIQUE_DEFINITION",
    "GET_RESULT_CAPABILITY",
    "KIT_CAPABILITY",
    "REQUEST_TASK_CAPABILITY",
    "SUPPORTED_PROVIDERS",

    "AgentAssistantFormateur",
    "AgentRH",
    "AgentStatistique",
    "AgentConfigurationError",
    "AgentDefinition",
    "AgentGenerateurOffre",
    "AgentGeneralisteReadOnly",
    "AgentHandler",
    "AgentHandlerRegistry",
    "AgentRegistry",
    "AgentRequest",
    "AgentResponse",
    "AgentRuntimeConfig",
    "AgentTaskService",
    "AgentTools",
    "CollaborationGrant",
    "InterAgentGateway",
    "OperationSpec",
    "PageSearchInput",
    "build_assistant",
    "build_chat_model",
    "build_collaboration_tools",
    "build_generateur",
    "build_generaliste",
    "build_rh",
    "build_statistique",
    "build_system_prompt",
    "compter_affectations_par_role",
    "compter_beneficiaires_par_session",
    "compter_missions_par_statut",
    "create_carso_deep_agent",
    "is_agent_configured",
    "lister_skills",
    "ouvrir_trace",
    "racine_skills",
    "resoudre_skills",
    "recursion_limit",
    "rechercher_appels_a_proposition",
    "rechercher_documents",
    "rechercher_equipes",
    "rechercher_missions",
    "rechercher_organisations",
    "serialiser_entite",
    "strip_secrets",
]
