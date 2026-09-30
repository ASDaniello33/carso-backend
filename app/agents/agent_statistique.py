"""AgentStatistique (clinrules 05).

Chiffres uniquement via le service déterministe. Aucune mutation.
Lecture seule sur toutes les tables (matrice d'accès validée) ; le périmètre
outils vit dans ``app/agents/tools/agent_statistique_tools.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.agents import prompt
from app.agents.base import AgentDefinition
from app.agents.runtime import AgentTaskService
from app.agents.toolkit import KIT_CAPABILITY, ouvrir_trace
from app.agents.tools.agent_statistique_tools import (
    SCENARIOS_HITL,
    construire_tools_statistique,
)
from app.infrastructure.database import SessionLocal
from app.tools.permissions import PermissionPolicy

AGENT_ID = "agent_statistique"
TASK_TYPE = "produire_statistiques"

PROMPT = prompt.STATISTIQUE_SYSTEM_PROMPT

DEFINITION = AgentDefinition(
    agent_id=AGENT_ID,
    display_name="Agent Statistique",
    description="KPI déterministes + synthèse. Strictement lecture.",
    prompt=PROMPT,
    policy=PermissionPolicy(
        allowed_capabilities=frozenset({KIT_CAPABILITY, "web_search"})
    ),
    capabilities=(KIT_CAPABILITY, "web_search"),
    tools=(
        "query_analytics",
        "search_organisations",
        "search_appels_a_proposition",
        "search_lots",
        "search_offres",
        "search_missions",
        "search_sessions",
        "search_beneficiaires",
        "search_equipes",
        "search_affectations",
        "search_documents",
        "langsearch_web_search",
    ),
    collaboration=(),
    approval_required=(),
    output_schema_name="StatistiqueReponse",
    skills=(),
    hitl_scenarios=SCENARIOS_HITL,
)


class AgentStatistique:
    """Lecture seule. Le service calcule, l'agent explique."""

    def __init__(
        self,
        session: Session,
        *,
        session_factory: Callable[[], Session] | None = None,
        extra_tools: tuple[Any, ...] = (),
    ) -> None:
        self._session = session
        self.definition = DEFINITION
        self._tasks = AgentTaskService(session)
        self._session_factory = session_factory or SessionLocal
        self._extra_tools = extra_tools

    def kpi(self, metrique: str, **params: Any) -> dict[str, Any]:
        self.definition.policy.require(self.definition.agent_id, KIT_CAPABILITY)
        task = ouvrir_trace(
            self._tasks,
            from_agent=self.definition.agent_id,
            task_type=TASK_TYPE,
            payload={"metrique": metrique},
            requires_approval=False,
        )
        try:
            from app.agents.tools.agent_statistique_tools import executer_agregation

            with self._session_factory() as session:
                resultat = executer_agregation(session, {"metrique": metrique, **params})
            self._tasks.complete(task, {"metrique": metrique})
        except Exception as exc:
            self._tasks.fail(task, type(exc).__name__)
            raise
        reponse = AgentTaskService.as_response(task)
        reponse["kpi"] = resultat
        return reponse

    def deep_tools(self) -> tuple[Any, ...]:
        return construire_tools_statistique(self.definition, self._session_factory)

    def build_agent(
        self, *, runtime: Any | None = None, system_prompt_extra: str | None = None
    ) -> Any:
        from app.agents.harness import create_carso_deep_agent

        return create_carso_deep_agent(
            definition=self.definition,
            tools=self.deep_tools(),
            runtime=runtime,
            system_prompt_extra=system_prompt_extra,
        )


def build_statistique(
    session: Session,
    *,
    session_factory: Callable[[], Session] | None = None,
    extra_tools: tuple[Any, ...] = (),
) -> AgentStatistique:
    return AgentStatistique(
        session, session_factory=session_factory, extra_tools=extra_tools
    )


__all__ = ["AGENT_ID", "DEFINITION", "AgentStatistique", "build_statistique"]
