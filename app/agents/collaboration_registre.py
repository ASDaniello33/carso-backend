"""Registre partagé des agents appelables (refactor collaboration, point 2).

Le registre est le point unique où chaque agent appelable expose son handler
(permissions propres, **aucun** transfert de privilège) ; la passerelle
``InterAgentGateway`` applique autorisation (grant), anti-boucle et trace
``agent_tasks``.

Aujourd'hui un seul agent est appelable : ``agent_rh`` (proposition
d'affectation). L'ancien handler de l'analyseur d'appel à proposition a été
supprimé avec l'agent : le générateur d'offre fait lui-même la lecture
documentaire et l'extraction, il n'a plus rien à déléguer. Le registre reste
néanmoins la couture d'ajout d'un futur agent interne justifié par l'analyse
des besoins.

``request_agent_task`` invoque l'agent cible et **attend** sa réponse avant de
la renvoyer à l'appelant.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.collaboration import (
    AgentHandlerRegistry,
    AgentRequest,
    AgentResponse,
    InterAgentGateway,
    build_collaboration_tools,
)
from app.core.errors import ValidationError as BusinessValidationError
from app.tools.base import TypedTool


def _uuid_requis(payload: dict[str, Any], champ: str) -> UUID:
    """Extrait un UUID obligatoire du payload A2A (message clair sinon)."""
    brut = payload.get(champ)
    if brut is None or str(brut).strip() == "":
        raise BusinessValidationError(
            f"payload[{champ!r}] obligatoire pour cette tâche inter-agent",
            details={"champ": champ},
        )
    try:
        return UUID(str(brut))
    except ValueError as exc:
        raise BusinessValidationError(
            f"payload[{champ!r}] n'est pas un UUID valide",
            details={"champ": champ, "valeur": str(brut)},
        ) from exc


def _handler_rh(session_factory: Callable[[], Session]) -> Any:
    """Handler RH : exécute ``proposer`` (chemin déterministe, HITL).

    La proposition d'affectation est un use-case borné (mission + équipe +
    rôle) : le chemin déterministe suffit et n'exige pas de LLM.
    """
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.agent_rh import TASK_TYPE as RH_TASK
    from app.agents.agent_rh import build_rh

    class _RhHandler:
        agent_id = RH_DEF.agent_id

        def supported_task_types(self) -> frozenset[str]:
            return frozenset({RH_TASK})

        def handle(self, request: AgentRequest) -> AgentResponse:
            session = session_factory()
            try:
                rh = build_rh(session, session_factory=session_factory)
                payload = dict(request.payload)
                resultat = rh.proposer(
                    _uuid_requis(payload, "mission_id"),
                    _uuid_requis(payload, "equipe_id"),
                    str(payload.get("role") or payload.get("role_dans_mission") or ""),
                    justification=str(payload.get("justification") or ""),
                    correlation_id=request.correlation_id,
                )
            finally:
                session.close()
            return AgentResponse(
                task_id=request.task_id,
                status="completed",
                result=dict(resultat.get("result") or resultat),
                requires_approval=bool(resultat.get("requires_approval", True)),
            )

    return _RhHandler()


def construire_registre(session_factory: Callable[[], Session]) -> AgentHandlerRegistry:
    """Registre des agents appelables (aujourd'hui : RH uniquement)."""
    registre = AgentHandlerRegistry()
    registre.register(_handler_rh(session_factory))
    return registre


def tools_collaboration_generateur(
    session_factory: Callable[[], Session],
) -> tuple[TypedTool, ...]:
    """Tools ``request_agent_task`` / ``get_agent_task_result`` du générateur."""
    registre = construire_registre(session_factory)
    gateway = InterAgentGateway(session_factory, handlers=registre)
    from app.agents.generateur_offre import DEFINITION

    return build_collaboration_tools(caller=DEFINITION, gateway=gateway)


__all__ = [
    "construire_registre",
    "tools_collaboration_generateur",
]
