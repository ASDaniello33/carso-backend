"""AgentRH (clinrules 03).

Propose une affectation à partir des données disponibles uniquement.
N'invente ni diplôme, ni disponibilité. HITL obligatoire.
Read/Write (sous HITL) : Document, Equipe, Mission — autres tables lecture
seule (matrice d'accès validée). Périmètre outils :
``app/agents/tools/agent_rh_tools.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents import prompt
from app.agents.base import AgentDefinition
from app.agents.hitl import HitlReponse, HitlScenario
from app.agents.runtime import AgentTaskService
from app.agents.toolkit import KIT_CAPABILITY, ouvrir_trace
from app.agents.tools.agent_rh_tools import (
    NOMS_TOOLS,
    _create_proposal,
    _score,
    construire_tools_rh,
)
from app.infrastructure.database import SessionLocal
from app.tools.permissions import PermissionPolicy

AGENT_ID = "agent_rh"
TASK_TYPE = "propose_team_assignment"

PROMPT = prompt.RH_SYSTEM_PROMPT

DEFINITION = AgentDefinition(
    agent_id=AGENT_ID,
    display_name="Agent RH",
    description="Proposition d'affectation mission ↔ équipe (HITL).",
    prompt=PROMPT,
    policy=PermissionPolicy(
        allowed_capabilities=frozenset(
            {KIT_CAPABILITY, "document_read", "document_propose"}
        )
    ),
    capabilities=(KIT_CAPABILITY, "document_read", "document_propose"),
    tools=NOMS_TOOLS,
    collaboration=(),
    approval_required=(
        "create_assignment_proposal",
        "propose_equipe_update",
        "propose_mission_update",
        "generer_document",
        "generer_document_html",
    ),
    output_schema_name="AssignmentProposal",
    skills=(),
    hitl_scenarios=(
        HitlScenario(
            id="proposition_affectation",
            titre="Proposer une affectation pour cette mission ?",
            contexte=(
                "L'analyse croise CV, compétences et disponibilités déclarées "
                "des équipes avec les exigences de la mission. Aucun rôle n'est "
                "inventé : la proposition reste modifiable."
            ),
            action=(
                "créer une proposition d'affectation (statut proposed) que vous "
                "approuvez, modifiez ou refusez"
            ),
            reponses=(
                HitlReponse(
                    valeur="proposer",
                    libelle="Proposer une affectation",
                    description="Crée la proposition, sans effet tant que vous ne l'approuvez pas.",
                ),
                HitlReponse(
                    valeur="cibler",
                    libelle="Cibler une équipe précise",
                    description="L'analyse se limite à l'équipe que vous indiquez.",
                ),
                HitlReponse(
                    valeur="annuler",
                    libelle="Ne rien proposer",
                    description="Aucune proposition créée, aucune écriture.",
                ),
            ),
        ),
    ),
)


class AgentRH:
    """Propose des affectations. N'approuve rien."""

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

    def proposer(
        self,
        mission_id: UUID,
        equipe_id: UUID,
        role_dans_mission: str,
        *,
        justification: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self.definition.policy.require(self.definition.agent_id, KIT_CAPABILITY)
        task = ouvrir_trace(
            self._tasks,
            from_agent=self.definition.agent_id,
            task_type=TASK_TYPE,
            payload={
                "mission_id": str(mission_id),
                "equipe_id": str(equipe_id),
                "role": role_dans_mission,
            },
            correlation_id=correlation_id,
            requires_approval=True,
        )
        try:
            proposition = _create_proposal(
                self._session,
                {
                    "mission_id": str(mission_id),
                    "equipe_id": str(equipe_id),
                    "role_dans_mission": role_dans_mission,
                    "justification": justification,
                },
            )
            self._tasks.complete(task, {"affectation_id": proposition["affectation_id"]})
        except Exception as exc:
            self._tasks.fail(task, type(exc).__name__)
            raise
        reponse = AgentTaskService.as_response(task)
        reponse["proposition"] = proposition
        return reponse

    def deep_tools(self) -> tuple[Any, ...]:
        from app.agents.outillage import tools_documents_pour

        extras = tools_documents_pour(self.definition, self._session_factory)
        extras = extras + self._extra_tools
        return construire_tools_rh(
            self.definition, self._session_factory, extra_tools=extras
        )

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


def build_rh(
    session: Session,
    *,
    session_factory: Callable[[], Session] | None = None,
    extra_tools: tuple[Any, ...] = (),
) -> AgentRH:
    return AgentRH(session, session_factory=session_factory, extra_tools=extra_tools)


# Réexports : la route d'affectation réutilise le score déterministe.
__all__ = [
    "AGENT_ID",
    "DEFINITION",
    "AgentRH",
    "build_rh",
    "_score",
    "_create_proposal",
]
