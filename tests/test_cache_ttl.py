"""Cache TTL des recherches — expiration, borne et copies.

Ce que ces tests verrouillent : la durée de vie **est** de 30 minutes (pas
« environ »), une entrée expirée n'est jamais resservie, le cache ne grossit pas
sans limite, et un appelant ne peut pas corrompre une entrée en modifiant la
valeur qu'il a reçue.
"""

from __future__ import annotations

from typing import Any

from app.tools.cache_ttl import TAILLE_MAX, TTL_SECONDES, CacheTTL


class _Horloge:
    """Horloge factice : le test avance le temps sans attendre."""

    def __init__(self) -> None:
        self.secondes = 0.0

    def __call__(self) -> float:
        return self.secondes


def test_la_duree_de_vie_par_defaut_est_de_30_minutes() -> None:
    assert TTL_SECONDES == 30 * 60
    assert TAILLE_MAX >= 100, "le cache doit absorber les recherches d'une journée"


def test_une_entree_expire_apres_30_minutes() -> None:
    horloge = _Horloge()
    cache: CacheTTL[dict[str, Any]] = CacheTTL(horloge=horloge)
    cache.memoriser("cle", {"resultats": [{"titre": "A"}]})

    horloge.secondes = TTL_SECONDES - 1
    assert cache.obtenir("cle") == {"resultats": [{"titre": "A"}]}

    horloge.secondes = TTL_SECONDES
    assert cache.obtenir("cle") is None, "à 30 minutes pile, l'entrée est périmée"
    assert len(cache) == 0, "elle est effacée, pas seulement ignorée"


def test_la_valeur_rendue_est_une_copie() -> None:
    cache: CacheTTL[dict[str, Any]] = CacheTTL()
    valeur = {"resultats": [{"titre": "A"}]}
    cache.memoriser("cle", valeur)

    valeur["resultats"][0]["titre"] = "modifié après coup"
    assert cache.obtenir("cle") == {"resultats": [{"titre": "A"}]}

    rendu = cache.obtenir("cle")
    assert rendu is not None
    rendu["resultats"][0]["titre"] = "modifié à la lecture"
    assert cache.obtenir("cle") == {"resultats": [{"titre": "A"}]}


def test_la_taille_est_bornee_et_les_plus_anciennes_partent() -> None:
    cache: CacheTTL[int] = CacheTTL(taille_max=2)
    cache.memoriser("a", 1)
    cache.memoriser("b", 2)
    cache.memoriser("c", 3)

    assert cache.obtenir("a") is None
    assert cache.obtenir("b") == 2
    assert cache.obtenir("c") == 3
    assert len(cache) == 2
