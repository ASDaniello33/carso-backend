"""Outils métier des agents CARSO — un module par agent (refactor outils).

Chaque module ``agent_*_tools.py`` est le **lieu unique** où vit le périmètre
outils d'un agent : schémas d'entrée Pydantic, opérations (requêtes nommées et
services) et fabrique ``construire_tools_*``. Les classes d'agents
(``app/agents/*.py``) ne gardent que le contrat, le chemin déterministe et
``build_agent``.

Chaîne conservée (AGENTS.md §2.5) :

```text
Agent → Tool → Application Service / requête nommée → Domain → Repository → PostgreSQL
```

Matrice d'accès base de données (docs/agents/REFACTOR-OUTILS-AGENTS.md §3) :

| Agent | Read/Write (HITL pour les écritures) | Lecture seule |
|---|---|---|
| généraliste | aucun (readonly total) | toutes les tables |
| statistique | aucun (readonly total) | toutes les tables |
| générateur d'offre | Document, AppelAProposition, Lot, Offre | autres |
| RH | Document, Equipe, Mission | autres |
| assistant formateur | Beneficiaire, Document (mission) | Equipe/Mission/Lot/Offre de la mission |

Les écritures passent toujours par des services qui exigent une décision
humaine (zone *proposal*, ``Decision``) **et** sont déclarées dans
``AgentDefinition.approval_required`` (interrupt LangGraph).
"""

from app.agents.tools.agent_assistant_formateur_tools import (
    NOMS_TOOLS as NOMS_TOOLS_ASSISTANT,
)
from app.agents.tools.agent_assistant_formateur_tools import (
    construire_tools_assistant_formateur,
)
from app.agents.tools.agent_generaliste_tools import (
    NOMS_TOOLS as NOMS_TOOLS_GENERALISTE,
)
from app.agents.tools.agent_generaliste_tools import (
    construire_tools_generaliste,
)
from app.agents.tools.agent_generateur_offre_tools import (
    NOMS_TOOLS as NOMS_TOOLS_GENERATEUR,
)
from app.agents.tools.agent_generateur_offre_tools import (
    construire_tools_generateur_offre,
)
from app.agents.tools.agent_rh_tools import (
    NOMS_TOOLS as NOMS_TOOLS_RH,
)
from app.agents.tools.agent_rh_tools import (
    construire_tools_rh,
)
from app.agents.tools.agent_statistique_tools import (
    NOMS_TOOLS as NOMS_TOOLS_STATISTIQUE,
)
from app.agents.tools.agent_statistique_tools import (
    construire_tools_statistique,
)

__all__ = [
    "NOMS_TOOLS_ASSISTANT",
    "NOMS_TOOLS_GENERALISTE",
    "NOMS_TOOLS_GENERATEUR",
    "NOMS_TOOLS_RH",
    "NOMS_TOOLS_STATISTIQUE",
    "construire_tools_assistant_formateur",
    "construire_tools_generaliste",
    "construire_tools_generateur_offre",
    "construire_tools_rh",
    "construire_tools_statistique",
]
