"""Garde anti-répétition : arrêter une boucle serrée, sans brider un parcours long.

Le prompt demande déjà de ne pas rappeler le même outil avec les mêmes
arguments. Une consigne ne borne rien : un modèle qui boucle ne lit pas ce
qu'il vient de produire. La garde compte les appels **identiques consécutifs**
et refuse le troisième, en rappelant que le résultat précédent est déjà dans la
conversation.

Elle ne doit jamais gêner un usage légitime :
- hors conversation (tests, scripts), aucune garde n'est posée ;
- un autre outil appelé entre deux remet le compteur à zéro (relire une liste
  après une création reste possible).
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.tools.base import TypedTool
from app.tools.garde_repetition import (
    MAX_IDENTIQUES,
    fermer_garde,
    ouvrir_garde,
    resume,
    verifier_appel,
)
from tests.conftest import *  # noqa: F401,F403  (fixtures communes)


def _outil(appels: list[dict]) -> TypedTool:
    def handler(payload: dict) -> dict:
        appels.append(payload)
        return {"ok": True, "vu": payload}

    return TypedTool(name="lire_liste", description="lit", handler=handler)


def test_hors_conversation_rien_n_est_borne() -> None:
    appels: list[dict] = []
    outil = _outil(appels)

    for _ in range(5):
        assert outil.invoke({"lot": "1"})["ok"] is True

    assert len(appels) == 5
    assert resume() is None


def test_troisieme_appel_identique_refuse_et_handler_non_execute() -> None:
    appels: list[dict] = []
    outil = _outil(appels)
    ouvrir_garde()
    try:
        assert outil.invoke({"lot": "1"})["ok"] is True
        assert outil.invoke({"lot": "1"})["ok"] is True
        troisieme = outil.invoke({"lot": "1"})
    finally:
        fermer_garde()

    assert troisieme["ok"] is False
    assert troisieme["error"]["code"] == "business_validation_error"
    assert "mêmes arguments" in troisieme["error"]["message"]
    # Le handler n'a pas tourné une troisième fois.
    assert len(appels) == MAX_IDENTIQUES
    # Compteurs du run : trois tentatives, un refus.
    assert resume() is None  # garde refermée


def test_un_autre_appel_remet_le_compteur_a_zero() -> None:
    appels: list[dict] = []
    outil = _outil(appels)
    ouvrir_garde()
    try:
        outil.invoke({"lot": "1"})
        outil.invoke({"lot": "1"})
        # Un autre appel (état changé entre-temps) : relire reste possible.
        outil.invoke({"lot": "2"})
        resultat = outil.invoke({"lot": "1"})
    finally:
        fermer_garde()

    assert resultat["ok"] is True
    assert len(appels) == 4


def test_les_compteurs_du_run_sont_exposes() -> None:
    ouvrir_garde()
    try:
        verifier_appel("lire_liste", {"lot": "1"})
        verifier_appel("lire_liste", {"lot": "1"})
        with pytest.raises(ValidationError):
            verifier_appel("lire_liste", {"lot": "1"})
        compteurs = resume()
    finally:
        fermer_garde()

    assert compteurs == {
        "appels": 3,
        "refus_repetition": 1,
        "par_outil": {"lire_liste": 3},
    }
