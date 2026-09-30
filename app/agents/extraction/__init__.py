"""Extraction d'appels à proposition — schémas de sortie structurée et moteurs.

Le moteur est une dépendance injectée : aujourd'hui manuel (aucun LLM),
demain un adaptateur LangChain produira le MÊME schéma validé — le contrat
de l'agent ne change pas (instruction/05 §11).
"""

from app.agents.extraction.engine import ExtractionEngine, ManualExtractionEngine
from app.agents.extraction.schemas import (
    ExtractionAppelAProposition,
    LotExtrait,
    OrganisationExtrait,
)

__all__ = [
    "ExtractionAppelAProposition",
    "ExtractionEngine",
    "LotExtrait",
    "ManualExtractionEngine",
    "OrganisationExtrait",
]
