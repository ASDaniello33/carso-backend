"""Cache mémoire à durée de vie (TTL) des recherches web et vidéo.

Pourquoi ici : une même recherche est souvent rejouée dans un run — l'agent
reformule sa demande, l'écran rafraîchit, deux collaborateurs cherchent la même
chose. Sans cache, chaque répétition repart chez les fournisseurs (LangSearch,
Bing, YouTube…), consomme un quota et ajoute plusieurs secondes d'attente pour
un résultat identique.

Ce que ce module garantit :

- **30 minutes** (:data:`TTL_SECONDES`) : au-delà, l'entrée est ignorée puis
  effacée — une réponse périmée n'est jamais servie indéfiniment ;
- **borné** (:data:`TAILLE_MAX`) : au plus 256 recherches par cache, les plus
  anciennes évincées — un processus long ne grossit pas sans limite ;
- **thread-safe** : FastAPI exécute des handlers dans un pool de threads, et
  deux agents peuvent chercher en parallèle ;
- **copies** : la valeur rendue est une copie, un appelant qui modifierait le
  résultat ne corrompt pas le cache ;
- **échecs jamais mémorisés** : seuls les résultats réussis sont mémorisés par
  les tools (une panne passagère se retente au lieu de rester collée 30 min).

Ce n'est **pas** un cache partagé entre instances : chaque processus Python a
le sien. Il réduit les répétitions d'un même serveur, il ne remplace pas un
cache distribué (Redis) — hors périmètre tant qu'un seul backend suffit.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

__all__ = ["TAILLE_MAX", "TTL_SECONDES", "CacheTTL"]

#: Durée de vie d'une recherche mémorisée : 30 minutes.
TTL_SECONDES = 30 * 60
#: Nombre maximal d'entrées par cache (les plus anciennes sont évincées).
TAILLE_MAX = 256

V = TypeVar("V")


class CacheTTL(Generic[V]):
    """Cache mémoire borné, à entrées expirant après ``ttl`` secondes.

    Args:
        ttl: durée de vie en secondes (30 minutes par défaut).
        taille_max: nombre maximal d'entrées conservées.
        horloge: source de temps injectable (``time.monotonic`` par défaut) —
            les tests d'expiration avancent l'horloge sans attendre.
    """

    def __init__(
        self,
        *,
        ttl: float = TTL_SECONDES,
        taille_max: int = TAILLE_MAX,
        horloge: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = float(ttl)
        self._taille_max = max(1, int(taille_max))
        self._horloge = horloge
        self._verrou = threading.Lock()
        self._entrees: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()

    def obtenir(self, cle: Hashable) -> V | None:
        """Valeur encore valide pour ``cle``, ``None`` si absente ou expirée.

        Une entrée expirée est supprimée à la lecture : elle ne sera pas servie
        une seconde fois, et ne sera pas non plus comptée dans la borne.
        """
        with self._verrou:
            entree = self._entrees.get(cle)
            if entree is None:
                return None
            expiration, valeur = entree
            if self._horloge() >= expiration:
                del self._entrees[cle]
                return None
            self._entrees.move_to_end(cle)
            return copy.deepcopy(valeur)

    def memoriser(self, cle: Hashable, valeur: V) -> None:
        """Mémorise ``valeur`` pour ``cle`` pendant ``ttl`` secondes."""
        with self._verrou:
            maintenant = self._horloge()
            self._purger(maintenant)
            self._entrees[cle] = (maintenant + self._ttl, copy.deepcopy(valeur))
            self._entrees.move_to_end(cle)
            while len(self._entrees) > self._taille_max:
                self._entrees.popitem(last=False)

    def vider(self) -> None:
        """Vide le cache (tests, ou invalidation explicite)."""
        with self._verrou:
            self._entrees.clear()

    def _purger(self, maintenant: float) -> None:
        """Retire les entrées expirées (appelé sous verrou)."""
        expirees = [
            cle for cle, (expiration, _) in self._entrees.items() if maintenant >= expiration
        ]
        for cle in expirees:
            del self._entrees[cle]

    def __len__(self) -> int:
        with self._verrou:
            return len(self._entrees)
