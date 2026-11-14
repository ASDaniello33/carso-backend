"""AgentRH (clinrules 03).

Propose une affectation à partir des données disponibles uniquement.
N'invente ni diplôme, ni disponibilité. HITL obligatoire.
Read/Write (sous HITL)—Document, Equipe, Mission—autres tables lecture
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
    CAPACITE_EMAIL,
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
            {
                KIT_CAPABILITY,
                "document_read",
                "document_propose",
                # Recherche web et lecture de pages : la même surface de lecture
                # externe que les autres agents (jamais une capacité de plus).
                "web_search",
                # Scripts confinés dans l'espace de travail du compte (INC-116).
                "script_python",
                # Email sortant vers un contact **enregistré** (HITL avant envoi).
                CAPACITE_EMAIL,
            }
        )
    ),
    capabilities=(
        KIT_CAPABILITY,
        "document_read",
        "document_propose",
        "web_search",
        "script_python",
        CAPACITE_EMAIL,
    ),
    tools=NOMS_TOOLS,
    collaboration=(),
    # Les outils de génération de documents (``generer_document``,
    # ``generer_document_html``) ne sont PAS sous approbation d'appel (INC-123) :
    # ils déposent un livrable en ``proposed``, jamais officiel — la décision
    # humaine reste la validation du document. Les écritures métier ci-dessous
    # restent sous HITL : elles modifient des données réelles.
    approval_required=(
        # Supprimer un document est une décision humaine : HITL avant exécution.
        "supprimer_document",
        "create_assignment_proposal",
        "propose_equipe_update",
        "propose_mission_update",
        # Budgets de mission : toute écriture budgétaire (créer, ajouter/
        # modifier/supprimer une ligne, soumettre) modifie des données réelles
        # — HITL avant exécution, comme les autres écritures de l'agent. Le
        # montant final reste approuvé par le circuit budget du service.
        "creer_budget_mission",
        "ajouter_ligne_budget",
        "modifier_ligne_budget",
        "retirer_ligne_budget",
        "soumettre_budget",
        # Un email envoyé ne se rappelle pas : approbation humaine **avant**
        # l'exécution, comme toute écriture sensible de l'agent.
        "envoyer_email",
    ),
    # Le contenu du message est corrigeable dans la carte de confirmation
    # (destinataires, objet, corps) : l'à-valider est le message réel, pas
    # seulement l'intention. L'outil revalide chaque valeur à l'exécution.
    approval_editable=("envoyer_email",),
    output_schema_name="AssignmentProposal",
    # ``generation-document`` : la méthode et les composants HTML du générateur
    # professionnel — la référence des documents de l'agent (CV, notes).
    skills=(
        "generation-document",
        # Structure/lisibilité d'un PDF et scripts ancrés : outils réels de
        # l'agent, leurs guides doivent être chargeables.
        "pdf-officiels",
        "scripts-python",
    ),
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
        from app.agents.outillage import tools_documents_pour, tools_scripts_pour

        extras = tools_documents_pour(self.definition, self._session_factory)
        # Script ancré (INC-124) : un livrable produit sous une mission/équipe
        # est déposé en ``proposed`` dans la même transaction.
        extras = extras + self._extra_tools + tools_scripts_pour(
            self.definition, session_factory=self._session_factory
        )
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
