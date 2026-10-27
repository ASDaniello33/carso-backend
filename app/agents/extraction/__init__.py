"""Extraction d'appels—schémas de sortie structurée et moteurs.

Le moteur est une dépendance injectée—aujourd'hui manuel (aucun LLM),
demain un adaptateur LangChain produira le MÊME schéma validé—le contrat
de l'agent ne change pas (instruction/05 §11).
"""

from app.agents.extraction.engine import ExtractionEngine, ManualExtractionEngine
from app.agents.extraction.schemas import (
    ExtractionAppel,
    LotExtrait,
    OrganisationExtrait,
)

__all__ = [
    "ExtractionAppel",
    "ExtractionEngine",
    "LotExtrait",
    "ManualExtractionEngine",
    "OrganisationExtrait",
]
