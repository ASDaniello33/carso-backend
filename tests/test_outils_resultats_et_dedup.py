"""Déduplication des appels d'outils et erreurs d'outil portées par un résultat.

Deux anomalies constatées dans le chat :

- certains modèles répètent le **même** appel (même nom, mêmes arguments) dans
  un message : deux cartes, un seul résultat, une phase de raisonnement en plus ;
- un outil en échec ne laissait aucun ``TOOL_CALL_RESULT`` exploitable : le
  modèle réessayait à l'aveugle et la carte restait « en préparation ».
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from app.agents.historique_outils import (
    dedupliquer_appels,
    dedupliquer_sequence,
    filtrer_appels_invalides,
)
from app.core.errors import ValidationError as BusinessValidationError
from app.tools.base import TypedTool, to_langchain_tool


def _appel(nom: str, arguments: dict[str, Any], identifiant: str) -> dict[str, Any]:
    return {"name": nom, "args": arguments, "id": identifiant, "type": "tool_call"}


# --- Déduplication ----------------------------------------------------------


def test_appels_identiques_dedupliques() -> None:
    """Deux appels identiques : le premier garde son id, le second disparaît."""
    message = AIMessage(
        content="",
        tool_calls=[
            _appel("query_analytics", {"metrique": "missions_par_statut"}, "call_1"),
            _appel("query_analytics", {"metrique": "missions_par_statut"}, "call_2"),
        ],
    )

    propre, doublons = dedupliquer_appels(message)

    assert doublons == 1
    assert [appel["id"] for appel in propre.tool_calls] == ["call_1"]


def test_arguments_differents_conserves() -> None:
    message = AIMessage(
        content="",
        tool_calls=[
            _appel("query_analytics", {"metrique": "missions_par_statut"}, "call_1"),
            _appel("query_analytics", {"metrique": "offres_par_statut"}, "call_2"),
            _appel("search_missions", {"page": 1}, "call_3"),
        ],
    )

    propre, doublons = dedupliquer_appels(message)

    assert doublons == 0
    assert len(propre.tool_calls) == 3


def test_ordre_des_arguments_indifferent() -> None:
    """Même appel écrit avec les clés dans un autre ordre = même appel."""
    message = AIMessage(
        content="",
        tool_calls=[
            _appel("search_missions", {"page": 1, "limite": 10}, "call_1"),
            _appel("search_missions", {"limite": 10, "page": 1}, "call_2"),
        ],
    )

    _, doublons = dedupliquer_appels(message)

    assert doublons == 1


def test_sequence_ne_touche_pas_aux_messages_sans_appel() -> None:
    from langchain_core.messages import HumanMessage

    messages = [
        HumanMessage(content="question"),
        AIMessage(
            content="",
            tool_calls=[
                _appel("query_analytics", {"metrique": "beneficiaires_total"}, "a"),
                _appel("query_analytics", {"metrique": "beneficiaires_total"}, "b"),
            ],
        ),
    ]

    propres, doublons = dedupliquer_sequence(messages)

    assert doublons == 1
    assert propres[0] is messages[0]


# --- Erreur d'outil portée par un résultat ---------------------------------


class EntreeOutil(BaseModel):
    valeur: str = "x"


def _outil(handler: Any) -> Any:
    return to_langchain_tool(
        TypedTool(name="outil_test", description="test", input_schema=EntreeOutil, handler=handler)
    )


def test_erreur_metier_produit_un_message_d_outil_en_erreur() -> None:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        raise BusinessValidationError(
            "session_id requis", details={"methode": "search_sessions"}
        )

    sortie = _outil(handler).invoke(
        {"type": "tool_call", "id": "call_1", "name": "outil_test", "args": {"valeur": "x"}}
    )

    assert isinstance(sortie, ToolMessage)
    assert sortie.status == "error"
    assert '"ok": false' in str(sortie.content)
    assert "search_sessions" in str(sortie.content)


def test_arguments_invalides_produisent_un_resultat_en_erreur() -> None:
    """Un appel `{}` (schéma non satisfait) doit laisser un résultat, pas rien."""

    class Exige(BaseModel):
        metrique: str = Field(description="requis")

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "recu": payload}

    outil = to_langchain_tool(
        TypedTool(name="outil_test", description="test", input_schema=Exige, handler=handler)
    )
    sortie = outil.invoke(
        {"type": "tool_call", "id": "call_1", "name": "outil_test", "args": {}}
    )

    assert isinstance(sortie, ToolMessage)
    assert sortie.status == "error"


def test_exception_inattendue_remonte_toujours() -> None:
    """Une panne inattendue reste un bug visible, jamais un résultat d'outil."""

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("panne de la base")

    outil = TypedTool(
        name="outil_test", description="test", input_schema=EntreeOutil, handler=handler
    )

    with pytest.raises(RuntimeError):
        outil.invoke({"valeur": "x"})


# --- Refus des arguments non conformes au schéma ----------------------------


class _MetriqueRequise(BaseModel):
    metrique: str = Field(description="requis")


def test_appel_incomplet_refuse_quand_un_appel_conforme_existe() -> None:
    """`query_analytics {}` à côté d'un appel valide : la carte inutile disparaît."""
    message = AIMessage(
        content="",
        tool_calls=[
            _appel("query_analytics", {"metrique": "missions_par_statut"}, "call_1"),
            _appel("query_analytics", {}, "call_2"),
        ],
    )

    propre, refuses = filtrer_appels_invalides(message, {"query_analytics": _MetriqueRequise})

    assert refuses == 1
    assert [appel["id"] for appel in propre.tool_calls] == ["call_1"]


def test_appel_incomplet_seul_conserve_pour_porter_l_erreur() -> None:
    """Seul appel du message : il est gardé, sinon le modèle corrigerait à l'aveugle."""
    message = AIMessage(content="", tool_calls=[_appel("query_analytics", {}, "call_1")])

    propre, refuses = filtrer_appels_invalides(message, {"query_analytics": _MetriqueRequise})

    assert refuses == 0
    assert propre is message
