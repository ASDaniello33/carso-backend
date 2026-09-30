"""Application layer — use cases over the domain (instruction/09 §2).

Service → Domain rules → Repository → PostgreSQL. Les services n'exposent
que des actions métier nommées (instruction/09 §6) ; ils ne commitent pas :
la transaction appartient à l'appelant (route via ``get_session``, script).
"""

from app.application.dto import (
    AffectationProposee,
    BudgetTotal,
    DecisionInput,
    DocumentAnchor,
    DocumentUploadInput,
    ExtractedDocument,
    LigneBudgetInput,
)
from app.application.trace import Decision, TraceContext

__all__ = [
    "AffectationProposee",
    "BudgetTotal",
    "Decision",
    "DecisionInput",
    "DocumentAnchor",
    "DocumentUploadInput",
    "ExtractedDocument",
    "LigneBudgetInput",
    "TraceContext",
]
