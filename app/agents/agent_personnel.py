"""Agent personnel—le consultation de **son** compte, en lecture seule.

``agent_personnel`` est l'agent de la page « Agent Personnel »—chaque compte
connecté y converse avec son agent, dont il choisit les instructions et les
outils, et auquel se rattachent ses connecteurs MCP personnels.

Il n'est pas un coordinateur—il n'appelle aucun autre agent (aucun
``CollaborationGrant``), ne supervise rien et ne route rien. Sa surface de
lecture est **celle du généraliste**—mêmes requêtes nommées, mêmes agrégations
déterministes, même lecture documentaire—construite en ``readonly=True`` :
toute opération de mutation lèverait ``PermissionDeniedError`` à la
construction, avant tout appel de modèle.

Ce qui lui est propre :

- ``par_utilisateur=True``—le runtime résout son graphe **par compte**
  (configuration du compte + connecteurs personnels), et les graphes ne sont
  jamais partagés entre deux comptes ;
- ses connecteurs MCP—un connecteur personnel ne charge que cet agent
  (``app.domain.mcp.AGENT_CONNECTEURS_PERSONNELS``), pour que les outils, les
  pannes et les jetons d'un compte ne touchent jamais un agent partagé.

La configuration du compte (instructions, outils retirés) vit dans
``configurations_agent_personnel`` et se règle depuis l'API
(``app.api.routes.agents`` → ``/agents/personnel``)—jamais dans ce module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.agents import prompt
from app.agents.base import AgentDefinition
from app.agents.toolkit import KIT_CAPABILITY
from app.agents.tools.agent_generaliste_tools import (
    NOMS_TOOLS,
    SCENARIOS_HITL,
    construire_tools_generaliste,
)
from app.tools.permissions import PermissionPolicy

#: Identifiant de l'agent (contrat, catalogue AG-UI, clé de graphe).
AGENT_ID = "agent_personnel"

#: Nom du tool de lecture documentaire (fourni par ``app.tools.documents``).
READ_DOCUMENT_TOOL = "read_document"

DEFINITION = AgentDefinition(
    agent_id=AGENT_ID,
    display_name="Agent Personnel",
    description=(
        "Agent personnel du compte : consultation en lecture seule de ses "
        "données CARSO autorisées, avec ses propres instructions, ses outils "
        "choisis et ses connecteurs MCP personnels."
    ),
    policy=PermissionPolicy(
        allowed_capabilities=frozenset(
            {KIT_CAPABILITY, "web_search", "document_read", "document_propose"}
        )
    ),
    capabilities=(KIT_CAPABILITY, "web_search", "document_read", "document_propose"),
    # Surface de lecture partagée avec le généraliste : recherche métier bornée,
    # agrégations nommées, lecture documentaire, lecture de pages web. Le
    # compte peut en retirer (configuration) ; le contrat, jamais.
    tools=NOMS_TOOLS,
    collaboration=(),
    prompt=prompt.PERSONNEL_SYSTEM_PROMPT,
    output_schema_name="PersonnelAnswer",
    hitl_scenarios=SCENARIOS_HITL,
    # Le graphe se résout par compte : instructions, outils retirés et
    # connecteurs personnels entrent dans la clé de cache du runtime.
    par_utilisateur=True,
)


class AgentPersonnel:
    """Agent personnel : même outillage lecture seule que le généraliste.

    La classe ne porte que le contrat et le montage : les requêtes nommées
    vivent dans ``app/agents/tools/agent_generaliste_tools.py``, les règles
    d'accès dans les services, le filtrage de périmètre dans les ContextVar
    posés par la garde AG-UI.
    """

    def __init__(
        self,
        session: Session,
        *,
        session_factory: Callable[[], Session] | None = None,
        read_document_tool: Any | None = None,
    ) -> None:
        """
        Args:
            session: session de construction du runtime (les tools ouvrent la
                leur, une transaction par appel).
            session_factory: fabrique de sessions des tools (défaut :
                fabrique applicative).
            read_document_tool: tool ``read_document`` câblé sur
                ``DocumentService`` — injecté par l'appelant, comme pour le
                généraliste (indépendance du câblage documentaire).
        """
        # La session de construction n'est pas retenue : les tools ouvrent la
        # leur (une transaction par appel), le runtime la ferme après montage.
        _ = session
        self.definition = DEFINITION
        self._session_factory = session_factory
        self._read_document_tool = read_document_tool

    def build_agent(
        self,
        *,
        runtime: Any | None = None,
        system_prompt_extra: str | None = None,
    ) -> Any:
        """Construit le deep agent (harnais partagé, allow-list du contrat).

        ``system_prompt_extra`` porte les consignes **du compte** (et celles de
        l'administration) : même signature que tous les agents du runtime, le
        manager les fournit de façon uniforme.
        """
        from app.agents.harness import create_carso_deep_agent

        return create_carso_deep_agent(
            definition=self.definition,
            tools=self.deep_tools(),
            runtime=runtime,
            system_prompt_extra=system_prompt_extra,
        )

    def deep_tools(self) -> tuple[Any, ...]:
        """Tools exposés au modèle : mêmes opérations lecture seule, définitions du compte."""
        from app.infrastructure.database import SessionLocal

        return construire_tools_generaliste(
            self.definition,
            self._session_factory or SessionLocal,
            read_document_tool=self._read_document_tool,
        )


def build_agent_personnel(
    session: Session,
    *,
    session_factory: Callable[[], Session] | None = None,
    read_document_tool: Any | None = None,
) -> AgentPersonnel:
    """Fabrique l'agent personnel (symétrique de ``build_generaliste``)."""
    return AgentPersonnel(
        session,
        session_factory=session_factory,
        read_document_tool=read_document_tool,
    )


__all__ = [
    "AGENT_ID",
    "DEFINITION",
    "READ_DOCUMENT_TOOL",
    "AgentPersonnel",
    "build_agent_personnel",
]
