"""Assainissement de l'entrée AG-UI : arguments d'outils jamais JSON cassés.

Constat de recette (01/11) : un run coupé en plein appel d'outil laisse des
``arguments`` tronqués dans l'historique du client ; ``ag-ui-langgraph`` les
relisait (``json.loads``) à chaque tour et levait ``JSONDecodeError`` avant
même d'appeler le modèle — le fil restait empoisonné. Les tests couvrent la
réparation pure et le décorateur qui la branche sur l'entrée du run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.api.agui_historique import (
    ARGUMENTS_VIDES,
    HistoriqueArgumentsSains,
    assainir_entree_agui,
    reparer_arguments,
)


class Appel:
    """Appel d'outil minimal, forme pydantic (attributs)."""

    def __init__(self, arguments: Any, *, nom: str = "outil", id_: str = "call_0") -> None:
        self.id = id_
        self.type = "function"
        self.function = _Fonction(nom, arguments)

    def model_copy(self, *, update: dict[str, Any]) -> Appel:
        copie = Appel(self.function.arguments, nom=self.function.name, id_=self.id)
        for cle, valeur in update.items():
            setattr(copie, cle, valeur)
        return copie


class _Fonction:
    def __init__(self, name: str, arguments: Any) -> None:
        self.name = name
        self.arguments = arguments

    def model_copy(self, *, update: dict[str, Any]) -> _Fonction:
        copie = _Fonction(self.name, self.arguments)
        for cle, valeur in update.items():
            setattr(copie, cle, valeur)
        return copie


class Message:
    def __init__(self, tool_calls: list[Any], *, id_: str = "m1", role: str = "assistant") -> None:
        self.id = id_
        self.role = role
        self.tool_calls = tool_calls

    def model_copy(self, *, update: dict[str, Any]) -> Message:
        copie = Message(list(self.tool_calls), id_=self.id, role=self.role)
        for cle, valeur in update.items():
            setattr(copie, cle, valeur)
        return copie


class Entree:
    def __init__(self, messages: list[Any], *, thread_id: str = "fil-1") -> None:
        self.thread_id = thread_id
        self.messages = messages
        self.resume: list[Any] = []

    def model_copy(self, *, update: dict[str, Any]) -> Entree:
        copie = Entree(list(self.messages), thread_id=self.thread_id)
        for cle, valeur in update.items():
            setattr(copie, cle, valeur)
        return copie


# --- réparation des arguments ------------------------------------------------


def test_arguments_valides_inchanges():
    """Un historique sain n'est jamais réécrit (identité de la chaîne)."""
    brut = '{"zone": "Toliara"}'
    arguments, change = reparer_arguments(brut)
    assert arguments is brut
    assert change is False


def test_arguments_vides_acceptes_tels_quels():
    """``None`` et chaîne vide passent déjà la garde d'ag-ui-langgraph."""
    assert reparer_arguments(None) == (None, False)
    assert reparer_arguments("") == ("", False)
    assert reparer_arguments("   ") == ("   ", False)


def test_fragments_concatennes_gardent_la_premiere_valeur():
    """Cas de recette : « Extra data: line 1 column 11 »."""
    arguments, change = reparer_arguments('{"lot": 1}{"lot": 2}')
    assert change is True
    assert json.loads(arguments) == {"lot": 1}


def test_arguments_tronques_deviennent_vides():
    """Illisible : un appel sans paramètre, jamais un contenu inventé."""
    arguments, change = reparer_arguments('{"zone": "Toli')
    assert (arguments, change) == (ARGUMENTS_VIDES, True)


def test_arguments_non_chaine_serialises():
    """Un client peut envoyer un objet déjà parsé : on le sérialise."""
    arguments, change = reparer_arguments({"lot": 2})
    assert change is True
    assert json.loads(arguments) == {"lot": 2}


def test_arguments_non_serialisable_replie_sur_vide():
    arguments, change = reparer_arguments({"objet": object()})
    assert (arguments, change) == (ARGUMENTS_VIDES, True)


# --- entrée complète ---------------------------------------------------------


def test_entree_saine_rendue_telle_quelle():
    entree = Entree([Message([Appel('{"lot": 1}')])])
    rendue, reparations = assainir_entree_agui(entree)
    assert rendue is entree
    assert reparations == 0


def test_entree_avec_appel_casse_reparée_sans_toucher_au_fil():
    entree = Entree([Message([Appel('{"lot": 1}garbage', id_="call_9")])])
    rendue, reparations = assainir_entree_agui(entree)
    assert reparations == 1
    assert rendue is not entree
    # Le fil et les identifiants d'appels sont portés tels quels.
    assert rendue.thread_id == "fil-1"
    assert rendue.messages[0].tool_calls[0].id == "call_9"
    assert json.loads(rendue.messages[0].tool_calls[0].function.arguments) == {"lot": 1}
    # L'entrée d'origine n'a pas été modifiée en place.
    assert entree.messages[0].tool_calls[0].function.arguments == '{"lot": 1}garbage'


def test_entree_camel_case_reste_camel_case():
    """Les messages buffet gardent leur clé d'origine (``toolCalls``)."""
    message = {
        "id": "m1",
        "role": "assistant",
        "toolCalls": [
            {"id": "call_0", "type": "function", "function": {"name": "x", "arguments": "{"}}
        ],
    }
    rendue, reparations = assainir_entree_agui({"messages": [message], "threadId": "f"})
    assert reparations == 1
    assert "tool_calls" not in rendue["messages"][0]
    assert rendue["messages"][0]["toolCalls"][0]["function"]["arguments"] == ARGUMENTS_VIDES


def test_entree_sans_messages_inchangee():
    entree = {"threadId": "fil"}
    assert assainir_entree_agui(entree) == (entree, 0)


def test_appels_multiples_comptes():
    entree = Entree(
        [
            Message([Appel('{"a": 1}x'), Appel('{"b": 2}y')]),
            Message([Appel('{"c": 3}')]),
        ]
    )
    _, reparations = assainir_entree_agui(entree)
    assert reparations == 2


# --- décorateur ---------------------------------------------------------------


class _AgentFactice:
    """Agent minimal : rejoue ``json.loads`` sur l'entrée, comme ag-ui-langgraph."""

    def __init__(self) -> None:
        self.vue: Any = None

    def clone(self) -> _AgentFactice:
        return _AgentFactice()

    async def run(self, input_data: Any):  # type: ignore[no-untyped-def]
        self.vue = input_data
        for message in input_data.messages:
            for appel in message.tool_calls:
                json.loads(appel.function.arguments)
        yield {"type": "RUN_FINISHED"}


def test_decorateur_repare_avant_relecture():
    """Le fil empoisonné redevient utilisable : l'appel cassé est réparé."""
    interne = _AgentFactice()
    entree = Entree([Message([Appel('{"lot": 1}{"lot": 2}')])])

    async def _collecter():
        agent = HistoriqueArgumentsSains(interne)
        return [evenement async for evenement in agent.run(entree)]

    evenements = asyncio.run(_collecter())
    assert evenements == [{"type": "RUN_FINISHED"}]
    assert interne.vue is not entree
    assert json.loads(interne.vue.messages[0].tool_calls[0].function.arguments) == {"lot": 1}


def test_decorateur_transmet_un_historique_sain_inchange():
    """Aucun appel cassé : l'entrée traverse sans copie."""
    interne = _AgentFactice()
    entree = Entree([Message([Appel('{"lot": 1}')])])

    async def _collecter():
        agent = HistoriqueArgumentsSains(interne)
        return [evenement async for evenement in agent.run(entree)]

    asyncio.run(_collecter())
    assert interne.vue is entree


def test_decorateur_repond_au_clone():
    agent = HistoriqueArgumentsSains(_AgentFactice())
    clone = agent.clone()
    assert isinstance(clone, HistoriqueArgumentsSains)
    assert clone is not agent
