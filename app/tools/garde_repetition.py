"""Garde anti-répétition : un même outil, mêmes arguments, en boucle.

Le prompt du générateur demande déjà de ne pas rappeler deux fois le même outil
avec les mêmes arguments, et de s'arrêter après trois tentatives sans progrès.
Une consigne seule ne borne rien : un modèle qui boucle ne lit pas ce qu'il a
déjà produit.

Cette garde est **conservatrice** : elle ne compte que les appels **consécutifs**
identiques. Un autre outil appelé entre deux remet le compteur à zéro — donc
relire une liste après avoir créé un objet reste possible, alors qu'une boucle
serrée sur le même appel est arrêtée au troisième essai, avec le rappel que le
résultat précédent est déjà dans la conversation.

Portée : un ``ContextVar`` posé par la garde d'entrée AG-UI, comme le budget de
recherche web. Hors conversation (tests, scripts, tâche inter-agent), aucune
garde n'est posée : rien n'est borné.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ValidationError

#: Nombre d'appels identiques consécutifs autorisés. Le suivant est refusé :
#: deux répétitions couvrent une reprise légitime (réessai), la troisième est
#: une boucle.
MAX_IDENTIQUES = 2


@dataclass
class EtatRepetition:
    """Appels identiques consécutifs observés pendant un run."""

    signature: str | None = None
    repetitions: int = 0
    appels: int = 0
    refus: int = 0
    par_outil: dict[str, int] = field(default_factory=dict)


_ETAT: ContextVar[EtatRepetition | None] = ContextVar("garde_repetition", default=None)


def _signature(nom: str, payload: dict[str, Any]) -> str:
    """Empreinte stable d'un appel (clés triées, valeurs JSON)."""
    try:
        corps = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):  # objet non sérialisable : on ne compare pas
        corps = repr(payload)
    return f"{nom}:{corps}"


def ouvrir_garde() -> EtatRepetition:
    """Ouvre la garde du run courant (un run = une garde neuve)."""
    etat = EtatRepetition()
    _ETAT.set(etat)
    return etat


def verifier_appel(nom: str, payload: dict[str, Any]) -> None:
    """Compte l'appel et refuse la troisième répétition consécutive identique.

    Sans garde posée, la fonction ne fait rien : les chemins hors conversation
    ne sont pas bornés.

    Raises:
        ValidationError: appel identique répété au-delà de ``MAX_IDENTIQUES``.
            Le message dit que le résultat précédent est déjà dans la
            conversation et invite à conclure ou à changer d'arguments — jamais
            une erreur technique.
    """
    etat = _ETAT.get()
    if etat is None:
        return
    signature = _signature(nom, payload)
    etat.appels += 1
    etat.par_outil[nom] = etat.par_outil.get(nom, 0) + 1
    if signature == etat.signature:
        etat.repetitions += 1
    else:
        etat.signature = signature
        etat.repetitions = 1
    if etat.repetitions > MAX_IDENTIQUES:
        etat.refus += 1
        raise ValidationError(
            f"{nom} a déjà été appelé {MAX_IDENTIQUES} fois avec exactement les "
            "mêmes arguments pendant ce tour. Son résultat est déjà dans la "
            "conversation : utilise-le, change d'arguments, ou conclus en "
            "signalant ce qui reste à faire."
        )


def resume() -> dict[str, Any] | None:
    """Compteurs du run (outils appelés, refus), ``None`` hors conversation."""
    etat = _ETAT.get()
    if etat is None:
        return None
    return {
        "appels": etat.appels,
        "refus_repetition": etat.refus,
        "par_outil": dict(sorted(etat.par_outil.items())),
    }


def fermer_garde() -> None:
    """Referme la garde du run."""
    _ETAT.set(None)


__all__ = [
    "MAX_IDENTIQUES",
    "EtatRepetition",
    "fermer_garde",
    "ouvrir_garde",
    "resume",
    "verifier_appel",
]
