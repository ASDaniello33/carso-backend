"""Règles métier pures, testables sans base de données.

Règle 7 (Phase 2 §5, confirmée [C]) : les coûts sont calculés par des
fonctions déterministes côté serveur — le LLM n'invente aucun montant.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def compute_cout_total(quantite: Decimal, cout_unitaire: Decimal) -> Decimal:
    """Coût d'une ligne budgétaire : ``quantite × cout_unitaire``, arrondi au centime.

    Raises:
        ValueError: si une des valeurs est négative.
    """
    if quantite < 0 or cout_unitaire < 0:
        msg = "quantite et cout_unitaire doivent être positifs ou nuls"
        raise ValueError(msg)
    return (quantite * cout_unitaire).quantize(CENT, rounding=ROUND_HALF_UP)


def compute_budget_total(couts_lignes: Iterable[Decimal]) -> Decimal:
    """Somme déterministe des coûts de lignes, arrondie au centime."""
    return sum(couts_lignes, Decimal("0")).quantize(CENT, rounding=ROUND_HALF_UP)
