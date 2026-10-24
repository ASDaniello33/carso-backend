"""Budget de recherche web d'un tour — le plafond que le prompt ne suffit pas à tenir.

Le prompt dit à l'agent **combien** de recherches sont raisonnables (8 pour une
question ordinaire, 15 quand une vérification externe est nécessaire, 25 pour
une recherche approfondie). Un modèle qui boucle ne lit pas ce qu'il a déjà
écrit : une consigne seule ne borne rien. Ce module pose donc une borne
**exécutable**, comptée par tour de conversation.

Un tour = un run AG-UI. La garde d'entrée (``preparer_acces_agent``) ouvre le
budget avant que le graphe ne tourne ; le tool ``langsearch_web_search``
consomme un crédit par appel. Au-delà, le tool refuse avec un message
actionnable (« réponds avec les sources déjà obtenues ») au lieu d'une
exception : l'agent conclut au lieu de s'acharner.

Portée
------
Le compteur vit dans un ``ContextVar``—le même transport que
``UserContext`` (``app/agents/contexte.py``) : posé dans la tâche qui itère le
flux SSE, il est hérité par les tâches filles du run, dont l'exécution des
tools. Hors de ce chemin (tests, scripts, tâche inter-agent), aucun budget
n'est posé : le module ne borne rien plutôt que de partager un compteur
global entre deux conversations.

Ce que ce module ne fait pas : il ne décide pas **si** une recherche est
utile, et il ne compte pas les lectures de pages (``fetch_url`` /
``read_urls``), qui ne repassent pas par les moteurs de recherche.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from app.core.errors import ValidationError

#: Plafond utilisé quand la configuration n'en fournit pas (aligné sur Settings).
PLAFOND_PAR_DEFAUT = 25

#: Nom du tool dont les appels consomment le budget.
TOOL_RECHERCHE = "langsearch_web_search"

#: Paliers annoncés par l'agent à son premier appel (règle métier demandée).
#: Le plafond de configuration reste un maximum absolu : un palier ne l'élargit
#: jamais, il le resserre.
PALIERS: dict[str, int] = {
    "ordinaire": 8,
    "verification": 15,
    "approfondie": 25,
}


@dataclass
class BudgetRecherche:
    """Crédits de recherche restants pour le tour courant."""

    plafond: int = PLAFOND_PAR_DEFAUT
    appels: int = field(default=0)

    @property
    def restants(self) -> int:
        """Crédits encore disponibles (jamais négatif)."""
        return max(0, self.plafond - self.appels)

    def annoncer_niveau(self, niveau: str | None) -> None:
        """Resserre le plafond au palier annoncé (jamais au-delà du maximum).

        Le palier peut arriver après quelques appels : il s'applique quand même
        et un tour déjà au-dessus se solde par un refus au prochain appel.
        Un nom de palier inconnu est ignoré : le schéma du tool l'a déjà validé.
        """
        if not niveau:
            return
        plafond = PALIERS.get(niveau)
        if plafond is not None:
            self.plafond = min(self.plafond, plafond)

    def consommer(self) -> None:
        """Consomme un crédit.

        Raises:
            ValidationError: budget épuisé — l'agent reçoit un message qui lui
                dit de conclure avec ce qu'il a, pas une erreur technique.
        """
        if self.appels >= self.plafond:
            raise ValidationError(
                f"Budget de recherche web atteint pour ce tour "
                f"({self.plafond} appels à {TOOL_RECHERCHE}). "
                "N'appelle plus d'outil de recherche : réponds avec les sources "
                "déjà obtenues, dis ce qui reste à vérifier et propose la suite."
            )
        self.appels += 1


_BUDGET: ContextVar[BudgetRecherche | None] = ContextVar(
    "budget_recherche_web", default=None
)


def ouvrir_budget(plafond: int | None = None) -> BudgetRecherche:
    """Ouvre le budget du tour courant (un run = un budget neuf)."""
    budget = BudgetRecherche(plafond=plafond or PLAFOND_PAR_DEFAUT)
    _BUDGET.set(budget)
    return budget


def consommer_recherche(niveau: str | None = None) -> int:
    """Consomme un crédit s'il existe un budget ; rend les crédits restants.

    Args:
        niveau: palier annoncé par l'agent (``ordinaire``, ``verification``,
            ``approfondie``). Il resserre le plafond du tour au premier appel ;
            ``None`` laisse le maximum de configuration.

    Sans budget posé, la fonction ne fait rien et rend ``-1`` : les chemins
    hors conversation (tests unitaires du tool, scripts) ne sont pas bornés.
    """
    budget = _BUDGET.get()
    if budget is None:
        return -1
    budget.annoncer_niveau(niveau)
    budget.consommer()
    return budget.restants


def fermer_budget() -> None:
    """Referme le budget du tour (le contexte du run se termine)."""
    _BUDGET.set(None)


__all__ = [
    "BudgetRecherche",
    "PALIERS",
    "PLAFOND_PAR_DEFAUT",
    "TOOL_RECHERCHE",
    "consommer_recherche",
    "fermer_budget",
    "ouvrir_budget",
]
