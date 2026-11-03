"""Outil de consultation de la base de connaissance (RAG) pour les agents.

**Un outil de recherche, pas un pipeline.** L'agent décide quand une question
demande une connaissance documentaire (méthode, cadre de référence, expertise
CARSO, guide technique) et appelle ``search_knowledge_base``. Rien n'est injecté
d'office dans le prompt : la bibliothèque est consultée à la demande, comme une
page web.

**Un appel, plusieurs formulations.** ``query`` est une *liste* de 1 à 5
formulations : l'agent y regroupe les variantes d'une même question (synonyme,
sigle développé, autre langue, un angle par entrée) et reçoit les passages
**groupés par formulation**. C'est ce qui coupe la boucle de reformulation
observée en recette : au lieu d'enchaîner « entrepreneur », puis
« entrepreneuriat », puis « définition entrepreneur », le modèle formule une
fois et conclut avec ce qu'il a.

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
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.base import AgentDefinition
from app.application.services.connaissance_service import (
    MAX_QUESTIONS_PAR_RECHERCHE,
    ConnaissanceService,
)
from app.core.errors import CarsoError
from app.tools.base import TypedTool, formater_erreur_outil

logger = logging.getLogger(__name__)

#: Nom stable du tool (déclaré dans les contrats ; l'agent personnel peut le retirer).
TOOL_NAME = "search_knowledge_base"

#: Rappel joint à chaque résultat : citer, ne pas réinventer, ne pas relancer.
CONSIGNE = (
    "Réponds avec ces passages et cite-les (ouvrage, page ou section). "
    "N'invente aucun contenu absent des extraits. Ne relance pas la même "
    "recherche avec d'autres mots : les formulations demandées sont déjà "
    "couvertes — si un point précis reste manquant, un seul appel ciblé sur ce "
    "point, puis tu conclus."
)

DESCRIPTION = (
    "Consulte la bibliothèque de connaissances de CARSO : livres, ouvrages, "
    "études, méthodologies, guides, expertises et rapports techniques. "
    "`query` est une LISTE de 1 à 5 formulations : regroupe dans CE SEUL appel "
    "les variantes d'une même question (synonyme, sigle développé, "
    "français/anglais, un angle par entrée) au lieu d'enchaîner les appels. "
    "Les passages reviennent groupés par formulation, chacun avec sa source "
    "(ouvrage, auteur, page ou section) et sa similarité. À utiliser dès "
    "qu'une réponse dépend d'une référence documentaire ou d'une méthode déjà "
    "écrite, et citer la source au lieu de paraphraser de mémoire. Ne "
    "l'utilise PAS pour les données métier (missions, offres, bénéficiaires) : "
    "leurs outils existent séparément. Une bibliothèque vide ou un index en "
    "cours de reconstruction est signalé explicitement."
)

#: Une formulation : 2 caractères au moins, bornée pour ne pas saturer le modèle.
Formulation = Annotated[str, Field(min_length=2, max_length=1000)]


class RechercheConnaissanceInput(BaseModel):
    """Formulations recherchées (ensemble) + filtres facultatifs."""

    query: list[Formulation] = Field(
        min_length=1,
        max_length=MAX_QUESTIONS_PAR_RECHERCHE,
        description=(
            "1 à 5 formulations, en français de préférence. Regrouper ici les "
            "synonymes, sigles développés, traductions et angles d'une même "
            "question : un seul appel suffit."
        ),
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Passages renvoyés PAR formulation (3 à 5 suffisent).",
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
            resultat = ConnaissanceService(session).rechercher_plusieurs(
                entree.query,
                limite=entree.top_k,
                document_id=entree.document_id,
                categorie=entree.categorie,
            )
        except CarsoError as exc:
            return formater_erreur_outil(exc)
        finally:
            session.close()
        return {
            "ok": True,
            "consigne": CONSIGNE,
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
    "CONSIGNE",
    "DESCRIPTION",
    "TOOL_NAME",
    "RechercheConnaissanceInput",
    "construire_tool_connaissance",
    "outils_connaissance_pour",
]
