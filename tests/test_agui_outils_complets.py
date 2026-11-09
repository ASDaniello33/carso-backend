"""Chaque appel d'outil du flux AG-UI reçoit un résultat (INC-156)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from app.api.agui_outils_complets import (
    MESSAGE_SANS_RESULTAT,
    ResultatsOutilsComplets,
    SuiviAppelsOuverts,
    est_interruption,
    resultat_absent,
    type_evenement,
)


def _start(ident: str, nom: str = "get_document_structure", message: str | None = "msg-1"):
    return SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id=ident,
        tool_call_name=nom,
        parent_message_id=message,
    )


def _args(ident: str):
    return SimpleNamespace(type="TOOL_CALL_ARGS", tool_call_id=ident, delta="{}")


def _resultat(ident: str):
    return SimpleNamespace(
        type="TOOL_CALL_RESULT",
        tool_call_id=ident,
        message_id="outil-reel",
        content='{"ok": true}',
    )


def _fin(outcome: Any = None):
    return SimpleNamespace(type="RUN_FINISHED", outcome=outcome)


def _erreur():
    return SimpleNamespace(type="RUN_ERROR", message="flux coupé")


class _Flux:
    """Agent interne factice : rejoue une liste d'événements."""

    def __init__(self, evenements: list[Any]) -> None:
        self.evenements = evenements

    def clone(self) -> _Flux:
        return _Flux(self.evenements)

    async def run(self, _entree: Any):  # type: ignore[no-untyped-def]
        for evenement in self.evenements:
            yield evenement


def _derouler(evenements: list[Any]) -> list[Any]:
    """Événements réellement reçus par le client pour ce flux."""

    async def collecter() -> list[Any]:
        agent = ResultatsOutilsComplets(_Flux(evenements))
        return [evenement async for evenement in agent.run(SimpleNamespace())]

    return asyncio.run(collecter())


def _types(evenements: list[Any]) -> list[str]:
    # Les événements réels portent un ``EventType`` : on lit sa valeur.
    return [type_evenement(evenement) for evenement in evenements]


def test_un_appel_sans_resultat_recoit_une_erreur_avant_la_fin() -> None:
    sortie = _derouler([_start("call_3"), _args("call_3"), _fin()])
    assert _types(sortie) == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_RESULT",
        "RUN_FINISHED",
    ]
    synthetique = sortie[2]
    # Champs pydantic en snake_case (les alias camelCase ne servent qu'au JSON).
    assert synthetique.tool_call_id == "call_3"
    assert synthetique.message_id == "msg-1"
    assert synthetique.role == "tool"
    charge = json.loads(synthetique.content)
    assert charge["ok"] is False
    assert charge["error"]["message"] == MESSAGE_SANS_RESULTAT


def test_un_appel_deja_repondu_n_ajoute_rien() -> None:
    sortie = _derouler([_start("call_1"), _resultat("call_1"), _fin()])
    assert _types(sortie) == ["TOOL_CALL_START", "TOOL_CALL_RESULT", "RUN_FINISHED"]


def test_seuls_les_appels_restes_ouverts_sont_clos() -> None:
    sortie = _derouler(
        [
            _start("call_1"),
            _start("call_2"),
            _resultat("call_1"),
            _fin(),
        ]
    )
    synthetiques = [e for e in sortie if type_evenement(e) == "TOOL_CALL_RESULT"]
    # Le résultat réel de call_1 reste tel quel ; seul call_2 est clos d'office.
    assert [e.tool_call_id for e in synthetiques] == ["call_1", "call_2"]
    # Le résultat synthétique précède immédiatement l'événement terminal.
    assert _types(sortie)[-2:] == ["TOOL_CALL_RESULT", "RUN_FINISHED"]
    assert json.loads(sortie[-2].content)["ok"] is False
    assert json.loads(sortie[2].content)["ok"] is True


def test_un_run_en_erreur_clot_aussi_les_appels_ouverts() -> None:
    sortie = _derouler([_start("call_9"), _erreur()])
    assert _types(sortie) == ["TOOL_CALL_START", "TOOL_CALL_RESULT", "RUN_ERROR"]


def test_une_interruption_hitl_n_est_pas_close() -> None:
    interruption = SimpleNamespace(type="interrupt")
    sortie = _derouler([_start("call_hitl"), _fin(interruption)])
    assert _types(sortie) == ["TOOL_CALL_START", "RUN_FINISHED"]
    assert est_interruption(_fin(interruption)) is True
    assert est_interruption(_fin()) is False


def test_message_absent_par_defaut() -> None:
    evenement = resultat_absent("call_4")
    assert evenement.message_id == "outil-call_4"


def test_le_suivi_ne_rouvre_pas_un_appel_clos() -> None:
    suivi = SuiviAppelsOuverts()
    suivi.observer(_start("call_1"))
    assert suivi.ouverts == ("call_1",)
    assert suivi.observer(_resultat("call_1")) == []
    assert suivi.ouverts == ()
    assert suivi.observer(_fin()) == []


def test_le_suivi_accepte_un_type_enum() -> None:
    """``ag_ui.core`` peut rendre un ``EventType`` : on lit son ``value``."""

    class _Type:
        value = "TOOL_CALL_START"

    suivi = SuiviAppelsOuverts()
    suivi.observer(SimpleNamespace(type=_Type(), tool_call_id="call_e", parent_message_id=None))
    assert suivi.ouverts == ("call_e",)
