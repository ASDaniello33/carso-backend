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

#: Tools documentaires de l'agent personnel, **en proposition** uniquement.
#:
#: INC-118 : le compte demandait un livrable et l'agent répondait que l'outil
#: n'existait pas — il n'était déclaré à aucun contrat. Il l'est désormais ici,
#: et à lui seul : la liste lecture seule partagée avec le généraliste
#: (``NOMS_TOOLS``) est **réduite** de ces noms avant d'être réutilisée, pour
#: qu'aucun autre agent lecture seule n'hérite du tool par effet de bord.
TOOLS_DOCUMENTS_PROPOSE: tuple[str, ...] = (
    "generer_document",
    "generer_document_professionnel",
    "corriger_document",
    # Images (INC-135) : même dépôt en proposition, le compte peut les retirer.
    "generer_image",
    "generer_image_svg",
    "composer_image",
    "verifier_image_svg",
)

#: Surface de lecture commune moins les tools de documentation ci-dessus.
NOMS_TOOLS_LECTURE_SEULE: tuple[str, ...] = tuple(
    nom for nom in NOMS_TOOLS if nom not in TOOLS_DOCUMENTS_PROPOSE
)

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
            {
                KIT_CAPABILITY,
                "web_search",
                "document_read",
                "document_propose",
                # Scripts confinés dans l'espace de travail du compte (INC-116) :
                # aucune donnée métier n'est écrite, seuls des fichiers locaux.
                "script_python",
            }
        )
    ),
    capabilities=(
        KIT_CAPABILITY,
        "web_search",
        "document_read",
        "document_propose",
        "script_python",
    ),
    # Surface de lecture partagée avec le généraliste : recherche métier bornée,
    # agrégations nommées, lecture documentaire, lecture de pages web — plus les
    # livrables en proposition et le script confiné. Le compte peut en retirer
    # (configuration) ; le contrat, jamais.
    tools=(
        *NOMS_TOOLS_LECTURE_SEULE,
        *TOOLS_DOCUMENTS_PROPOSE,
        "run_python_script",
    ),
    # Supprimer un document est une décision humaine : HITL avant exécution.
    approval_required=("supprimer_document",),
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
        """Tools exposés au modèle : lectures du kit, documents, espace de travail.

        Les tools documentaires sont assemblés par ``tools_documents_pour`` et
        filtrés sur le contrat : l'agent personnel reçoit ainsi
        ``generer_document_professionnel`` (proposition) sans câblage ad hoc.
        """
        from app.agents.outillage import tools_documents_pour, tools_scripts_pour
        from app.infrastructure.database import SessionLocal

        fabrique = self._session_factory or SessionLocal
        outils = construire_tools_generaliste(
            self.definition,
            fabrique,
            read_document_tool=self._read_document_tool,
        )
        deja = {tool.name for tool in outils}
        documents = [
            tool
            for tool in tools_documents_pour(self.definition, fabrique)
            if tool.name not in deja
        ]
        # Espace de travail personnel : le script tourne dans le dossier du
        # compte, jamais dans un dossier partagé (INC-116). La session est
        # fournie pour que le mode **ancré** (INC-124) puisse déposer un
        # livrable quand l'utilisateur désigne une mission ou une session.
        scripts = tools_scripts_pour(self.definition, session_factory=fabrique)
        return tuple(outils) + tuple(documents) + scripts


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
    "NOMS_TOOLS_LECTURE_SEULE",
    "READ_DOCUMENT_TOOL",
    "TOOLS_DOCUMENTS_PROPOSE",
    "AgentPersonnel",
    "build_agent_personnel",
]
