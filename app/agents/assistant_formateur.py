"""AgentAssistantFormateur (clinrules 04).

Aperçu d'import Excel + documents opérationnels. Matrice d'accès (validée) :
Read/Write ``Beneficiaire`` (import sous HITL, doublons signalés jamais
fusionnés) et ``Document`` rattaché à une mission ; lecture seule pour
Equipe/Mission/Lot/Offre de la mission. Périmètre outils :
``app/agents/tools/agent_assistant_formateur_tools.py``.
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
from app.agents.tools.agent_assistant_formateur_tools import (
    NOMS_TOOLS,
    construire_tools_assistant_formateur,
)
from app.infrastructure.database import SessionLocal
from app.tools.permissions import PermissionPolicy

AGENT_ID = "agent_assistant_formateur"
TASK_TYPE = "preparer_documents_mission"

PROMPT = prompt.ASSISTANT_FORMATEUR_SYSTEM_PROMPT

DEFINITION = AgentDefinition(
    agent_id=AGENT_ID,
    display_name="Agent Assistant Formateur",
    description="Documents opérationnels + import bénéficiaires (HITL).",
    prompt=PROMPT,
    policy=PermissionPolicy(
        allowed_capabilities=frozenset(
            {KIT_CAPABILITY, "web_search", "document_read", "document_propose"}
        )
    ),
    capabilities=(KIT_CAPABILITY, "web_search", "document_read", "document_propose"),
    tools=NOMS_TOOLS,
    collaboration=(),
    approval_required=(
        "generer_document",
        "generer_document_html",
        "fill_template",
        "import_beneficiaires",
    ),
    output_schema_name="ApercuImport",
    skills=("xlsx-officiels", "docx-officiels", "scripts-python"),
    hitl_scenarios=(
        HitlScenario(
            id="import_beneficiaires",
            titre="Importer cette liste de bénéficiaires ?",
            contexte=(
                "L'aperçu distingue lignes valides, lignes incomplètes et lignes "
                "ignorées. Rien n'est écrit avant votre décision."
            ),
            action=(
                "importer les bénéficiaires de la session selon le périmètre choisi"
            ),
            reponses=(
                HitlReponse(
                    valeur="importer_valides",
                    libelle="Importer les lignes valides",
                    description=(
                        "Seules les lignes complètes sont importées ; les autres sont listées."
                    ),
                ),
                HitlReponse(
                    valeur="importer_tout_corrigable",
                    libelle="Importer aussi les lignes corrigeables",
                    description=(
                        "Les lignes incomplètes mais corrigeables sont importées "
                        "et marquées à compléter."
                    ),
                ),
                HitlReponse(
                    valeur="annuler",
                    libelle="Annuler l'import",
                    description="Aucune écriture, l'aperçu reste consultable.",
                ),
            ),
        ),
        HitlScenario(
            id="generation_document_session",
            titre="Générer le document de session ?",
            contexte=(
                "Fiche de présence, fiche technique ou checklist : le document "
                "est créé en proposition et reste à valider par un humain."
            ),
            action=(
                "générer le document depuis le modèle de l'organisation et le déposer "
                "en proposition"
            ),
            reponses=(
                HitlReponse(
                    valeur="generer",
                    libelle="Générer le document",
                    description="Document proposé, en attente de votre validation.",
                ),
                HitlReponse(
                    valeur="autre_document",
                    libelle="Choisir un autre type de document",
                    description="Vous précisez le type ou le modèle avant génération.",
                ),
                HitlReponse(
                    valeur="annuler",
                    libelle="Ne pas générer",
                    description="Aucun document créé.",
                ),
            ),
        ),
    ),
)


class AgentAssistantFormateur:
    """Aperçu import + import HITL + documents. Doublons jamais fusionnés."""

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

    def apercu_import(
        self, document_id: UUID, *, correlation_id: str | None = None
    ) -> dict[str, Any]:
        self.definition.policy.require(self.definition.agent_id, KIT_CAPABILITY)
        task = ouvrir_trace(
            self._tasks,
            from_agent=self.definition.agent_id,
            task_type=TASK_TYPE,
            payload={"document_id": str(document_id)},
            correlation_id=correlation_id,
            requires_approval=True,
        )
        try:
            from app.agents.tools.agent_assistant_formateur_tools import (
                _preview_import,
            )

            apercu = _preview_import(self._session, {"document_id": str(document_id)})
            self._tasks.complete(task, {"nb_lignes_apercu": apercu["nb_lignes_apercu"]})
        except Exception as exc:
            self._tasks.fail(task, type(exc).__name__)
            raise
        reponse = AgentTaskService.as_response(task)
        reponse["apercu"] = apercu
        return reponse

    def deep_tools(self) -> tuple[Any, ...]:
        return construire_tools_assistant_formateur(
            self.definition, self._session_factory, extra_tools=self._extra_tools
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


def build_assistant(
    session: Session,
    *,
    session_factory: Callable[[], Session] | None = None,
    extra_tools: tuple[Any, ...] = (),
) -> AgentAssistantFormateur:
    return AgentAssistantFormateur(
        session, session_factory=session_factory, extra_tools=extra_tools
    )


__all__ = [
    "AGENT_ID",
    "DEFINITION",
    "AgentAssistantFormateur",
    "build_assistant",
]
