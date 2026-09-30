"""Agent généraliste en lecture seule (clinrules 06, Lot A1, Phase 6).

``agent_generaliste_readonly`` est un agent **indépendant** de consultation :
il n'est PAS un coordinateur, ne supervise aucun agent, ne route rien
(clinrules 06 §Position). Son périmètre : rechercher, consulter, agréger.

Sécurité (clinrules 06 §Sécurité) :

- **lecture seule sur toutes les tables** (matrice d'accès validée) : le kit
  est construit en ``readonly=True`` — toute opération de mutation lèverait
  ``PermissionDeniedError`` à la construction, avant tout appel de modèle ;
- ``query_analytics`` n'expose que des agrégations nommées, calculées par le
  service (instruction/10 §9) — jamais de SQL libre (clinrules 08) ;
- ``read_document`` renvoie le texte extrait, soumis à la permission
  ``document_read`` (traçage, type autorisé).

Le périmètre outils vit dans ``app/agents/tools/agent_generaliste_tools.py``
(un lieu par agent, refactor outils) : cette classe ne porte que le contrat,
le chemin déterministe ``consulter`` et ``build_agent``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import prompt
from app.agents.base import AgentDefinition
from app.agents.runtime import AgentTaskService
from app.agents.toolkit import KIT_CAPABILITY, ouvrir_trace
from app.agents.tools.agent_generaliste_tools import (
    ANALYTIQUES_AUTORISEES,
    SCENARIOS_HITL,
    AnalyticsInput,
    construire_tools_generaliste,
    executer_analytique,
)
from app.infrastructure.database import SessionLocal
from app.tools.permissions import PermissionPolicy

#: Identifiant de l'agent (clinrules 06).
AGENT_ID = "agent_generaliste_readonly"

#: Nom du tool de lecture documentaire (fourni par ``app.tools.documents``).
READ_DOCUMENT_TOOL = "read_document"

TASK_TYPE = "consultation_generale"


class QuestionInput(BaseModel):
    """Entrée de consultation (chemin déterministe et chemin Deep Agents)."""

    question: str = Field(min_length=1, max_length=2000)
    limite: int = Field(default=20, ge=1, le=100)


DEFINITION = AgentDefinition(
    agent_id=AGENT_ID,
    display_name="Agent Généraliste (lecture seule)",
    description=(
        "Assistant généraliste indépendant : recherche, consultation et "
        "analyse des données et documents CARSO autorisés. Strictement "
        "READ-ONLY — aucune mutation, approbation ou affectation."
    ),
    policy=PermissionPolicy(
        allowed_capabilities=frozenset({KIT_CAPABILITY, "web_search", "document_read"})
    ),
    capabilities=(KIT_CAPABILITY, "web_search", "document_read"),
    tools=(
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
        READ_DOCUMENT_TOOL,
        "query_analytics",
        "langsearch_web_search",
    ),
    collaboration=(),
    prompt=prompt.GENERALISTE_SYSTEM_PROMPT,
    output_schema_name="GeneralistAnswer",
    hitl_scenarios=SCENARIOS_HITL,
)


class AgentGeneralisteReadOnly:
    """Agent généraliste : recherches métier + agrégations + lecture document.

    Deux chemins : ``consulter`` déterministe (sans LLM, testable) et
    ``build_agent`` Deep Agents. La trace ``agent_tasks`` vit dans la
    transaction de l'appelant ; les tools ouvrent leur session via le kit.
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
            session: session de l'appelant (trace ``agent_tasks`` uniquement).
            session_factory: sessions des tools (défaut : fabrique applicative).
            read_document_tool: tool ``read_document`` câblé sur
                ``DocumentService`` — fourni par l'appelant pour garder
                l'agent indépendant du câblage documentaire.
        """
        self._session = session
        self.definition = DEFINITION
        self._tasks = AgentTaskService(session)
        self._session_factory = session_factory or SessionLocal
        self._read_document_tool = read_document_tool

    # --- Chemin déterministe (sans LLM) ---------------------------------

    def consulter(
        self,
        question: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Répond en balayant les recherches métier (sans LLM).

        Prouve l'intégration base + tools + agent + sécurité : chaque
        recherche bornée est exécutée, les résultats agrégés. La réponse
        porte ``requires_approval=False`` — une consultation ne décide rien
        (aucune donnée officielle n'est écrite, par construction).
        """
        self.definition.policy.require(self.definition.agent_id, KIT_CAPABILITY)
        entree = QuestionInput.model_validate({"question": question})
        task = ouvrir_trace(
            self._tasks,
            from_agent=self.definition.agent_id,
            task_type=TASK_TYPE,
            payload={"question": entree.question},
            correlation_id=correlation_id,
            requires_approval=False,
        )
        try:
            from app.agents.consultation import (
                rechercher_appels_a_proposition,
                rechercher_documents,
                rechercher_equipes,
                rechercher_missions,
                rechercher_organisations,
            )

            # Chemin déterministe : la transaction appartient à l'appelant,
            # comme ``analyse_rfp`` de l'analyseur (une consultation ne
            # committe rien — le commit du trace appartient à la route).
            session = self._session
            bornes = {"recherche": entree.question, "limite": entree.limite}
            resultat = {
                "question": entree.question,
                **rechercher_organisations(session, dict(bornes)),
                **rechercher_appels_a_proposition(session, dict(bornes)),
                **rechercher_missions(session, dict(bornes)),
                **rechercher_equipes(session, dict(bornes)),
                **rechercher_documents(session, dict(bornes)),
                **executer_analytique(session, {"metrique": "missions_par_statut"}),
                **executer_analytique(session, {"metrique": "affectations_par_role"}),
            }
            self._tasks.complete(task, {"question": entree.question})
        except Exception as exc:
            self._tasks.fail(task, f"{type(exc).__name__}")
            raise
        reponse = AgentTaskService.as_response(task)
        reponse["consultation"] = resultat
        return reponse

    def executer_analytique(self, metrique: str, **parametres: Any) -> dict[str, Any]:
        """Exécute une agrégation nommée (périmètre fermé, pas de SQL libre).

        Raises:
            ValidationError: métrique inconnue ou paramètre manquant (clinrules
                10 : refus technique, jamais de chiffre inventé).
            NotFoundError: entité cible absente (ex. session inconnue).
        """
        with self._session_factory() as session:
            return executer_analytique(session, {"metrique": metrique, **parametres})

    # --- Chemin Deep Agents (LLM) ----------------------------------------

    def build_agent(
        self,
        *,
        runtime: Any | None = None,
        system_prompt_extra: str | None = None,
    ) -> Any:
        """Construit le deep agent (harnais partagé, allow-list du contrat).

        Le mot-clé ``system_prompt_extra`` porte les instructions administrées
        (Paramètres) : **tous** les agents du runtime partagent cette signature
        — le manager l'appelle de façon uniforme (incrément 13 : ce paramètre
        s'appelait ``extra_prompt`` ici et le montage AG-UI échouait en
        ``TypeError``).
        """
        from app.agents.harness import create_carso_deep_agent

        return create_carso_deep_agent(
            definition=self.definition,
            tools=self.deep_tools(),
            runtime=runtime,
            system_prompt_extra=system_prompt_extra,
        )

    def deep_tools(self) -> tuple[Any, ...]:
        """Tools exposés au modèle : délégués au module du même périmètre."""
        return construire_tools_generaliste(
            self.definition,
            self._session_factory,
            read_document_tool=self._read_document_tool,
        )


def build_generaliste(
    session: Session,
    *,
    session_factory: Callable[[], Session] | None = None,
    read_document_tool: Any | None = None,
) -> AgentGeneralisteReadOnly:
    """Fabrique l'agent généraliste (symétrique de ``build_analyseur``)."""
    return AgentGeneralisteReadOnly(
        session,
        session_factory=session_factory,
        read_document_tool=read_document_tool,
    )


__all__ = [
    "AGENT_ID",
    "ANALYTIQUES_AUTORISEES",
    "DEFINITION",
    "READ_DOCUMENT_TOOL",
    "TASK_TYPE",
    "AnalyticsInput",
    "AgentGeneralisteReadOnly",
    "build_generaliste",
]
