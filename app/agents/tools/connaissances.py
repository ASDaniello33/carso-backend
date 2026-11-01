"""Outil de consultation de la base de connaissance (RAG) pour les agents.

**Un outil de recherche, pas un pipeline.** L'agent décide quand une question
demande une connaissance documentaire (méthode, cadre de référence, expertise
CARSO, guide technique) et appelle ``search_knowledge_base``. Rien n'est injecté
d'office dans le prompt : la bibliothèque est consultée à la demande, comme une
page web.

Le résultat porte la **source** (titre, auteur, page, section) et la
similarité : le modèle cite au lieu de paraphraser de mémoire, et l'humain peut
vérifier le passage dans l'ouvrage.

Lecture seule : aucune écriture, aucune approbation. Le tool passe par le
service applicatif (AGENTS.md §2.5), qui applique la configuration effective et
le contrôle de signature anti-mélange.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.base import AgentDefinition
from app.application.services.connaissance_service import ConnaissanceService
from app.core.errors import CarsoError
from app.tools.base import TypedTool, formater_erreur_outil

logger = logging.getLogger(__name__)

#: Nom stable du tool (déclaré dans les contrats ; l'agent personnel peut le retirer).
TOOL_NAME = "search_knowledge_base"

DESCRIPTION = (
    "Consulte la bibliothèque de connaissances de CARSO : livres, ouvrages, "
    "études, méthodologies, guides, expertises et rapports techniques. "
    "Renvoie les passages les plus proches de la question, chacun avec sa "
    "source (ouvrage, auteur, page ou section) et sa similarité. À utiliser "
    "dès qu'une réponse dépend d'une référence documentaire ou d'une méthode "
    "déjà écrite, et citer la source au lieu de paraphraser de mémoire. Ne "
    "l'utilise PAS pour les données métier (missions, offres, bénéficiaires) : "
    "leurs outils existent séparément. Une bibliothèque vide ou un index en "
    "cours de reconstruction est signalé explicitement."
)


class RechercheConnaissanceInput(BaseModel):
    """Question en langage naturel + filtres facultatifs."""

    question: str = Field(
        min_length=2,
        max_length=1000,
        description="Question ou thème recherché, en français de préférence.",
    )
    limite: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Nombre de passages renvoyés (5 suffit pour la plupart des questions).",
    )
    categorie: str | None = Field(
        default=None,
        max_length=100,
        description="Restreindre à une catégorie (livre, étude, guide, rapport…).",
    )
    document_id: UUID | None = Field(
        default=None,
        description="Relire un ouvrage précis déjà identifié (identifiant de fiche).",
    )


def construire_tool_connaissance(
    definition: AgentDefinition,
    session_factory: Callable[[], Session],
) -> TypedTool | None:
    """Construit ``search_knowledge_base`` si le contrat de l'agent le déclare.

    Returns:
        Le tool typé, ou ``None`` pour un agent qui n'a pas cette capacité —
        le harnais refuserait de toute façon un outil hors contrat.
    """
    if TOOL_NAME not in definition.allowed_tools():
        return None

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        entree = RechercheConnaissanceInput.model_validate(payload)
        session = session_factory()
        try:
            resultat = ConnaissanceService(session).rechercher(
                entree.question,
                limite=entree.limite,
                document_id=entree.document_id,
                categorie=entree.categorie,
            )
        except CarsoError as exc:
            return formater_erreur_outil(exc)
        finally:
            session.close()
        return {
            "ok": True,
            "consigne": (
                "Cite les passages utilisés avec leur ouvrage et leur page/section. "
                "N'invente aucun contenu absent des extraits."
            ),
            **resultat,
        }

    return TypedTool(
        name=TOOL_NAME,
        description=DESCRIPTION,
        input_schema=RechercheConnaissanceInput,
        handler=handler,
        tags=("knowledge", "rag", "lecture"),
    )


def outils_connaissance_pour(
    definition: AgentDefinition,
    session_factory: Callable[[], Session],
) -> tuple[TypedTool, ...]:
    """Tuple (0 ou 1 tool) prêt à être concaténé aux outils d'un agent."""
    tool = construire_tool_connaissance(definition, session_factory)
    return (tool,) if tool is not None else ()


__all__ = [
    "DESCRIPTION",
    "TOOL_NAME",
    "RechercheConnaissanceInput",
    "construire_tool_connaissance",
    "outils_connaissance_pour",
]
