"""Moteurs d'extraction — dépendance injectée de l'agent.

``ExtractionEngine`` est un protocole : le jour où l'adaptateur LLM arrive
(LangChain, structured output), il implémente le même protocole et l'agent ne
change pas. Aucun moteur n'invente de données : il transforme une source
fournie en schéma validé, sinon il lève.
"""

from typing import Any, Protocol, runtime_checkable

from app.agents.extraction.schemas import ExtractionAppelAProposition
from app.core.errors import ValidationError as BusinessValidationError


@runtime_checkable
class ExtractionEngine(Protocol):
    """Contrat d'un moteur d'extraction (LLM ou non)."""

    name: str

    def extract(self, payload: dict[str, Any]) -> ExtractionAppelAProposition:
        """Produit une proposition d'extraction validée, ou lève."""
        ...


class ManualExtractionEngine:
    """Moteur manuel (Phase courante : aucun LLM).

    Un opérateur fournit la structure (saisie contrôlée ou reprise d'un
    document analysé hors système) ; le moteur se contente de VALIDER la
    forme via le schéma Pydantic. Le payload attendu contient la clé
    ``extraction`` portant la proposition.
    """

    name = "manual"

    def extract(self, payload: dict[str, Any]) -> ExtractionAppelAProposition:
        data = payload.get("extraction")
        if not isinstance(data, dict):
            msg = (
                "payload['extraction'] manquant : fournir la proposition "
                "d'extraction structurée"
            )
            raise BusinessValidationError(msg)
        try:
            return ExtractionAppelAProposition.model_validate(data)
        except Exception as exc:
            msg = f"Proposition d'extraction invalide: {exc}"
            raise BusinessValidationError(msg) from exc
