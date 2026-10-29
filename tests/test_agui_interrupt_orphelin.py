"""Tests de la purge d'interrupt HITL orphelin (constat de recette)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.api.agui_interrupt_orphelin import (
    MOTIF_REFUS,
    PurgeInterruptOrphelin,
    decisions_refus,
)


def _snapshot(*, nexts: tuple[str, ...], action_requests: int = 0) -> Any:
    interruptions = ()
    if action_requests:
        payload = {"action_requests": [{"name": f"outil_{i}"} for i in range(action_requests)]}
        interruptions = (SimpleNamespace(value=payload),)
    return SimpleNamespace(
        next=nexts,
        tasks=(SimpleNamespace(interrupts=interruptions),) if interruptions else (),
    )


class FauxGraphe:
    def __init__(self, snapshot: Any = None, *, echoue: bool = False) -> None:
        self.snapshot = snapshot
        self.echoue = echoue
        self.appels_etat: list[dict[str, Any]] = []
        self.appels_reprise: list[tuple[Any, dict[str, Any]]] = []

    async def aget_state(self, config: dict[str, Any]) -> Any:
        self.appels_etat.append(config)
        if self.echoue:
            raise RuntimeError("base injoignable")
        return self.snapshot

    async def ainvoke(self, command: Any, config: dict[str, Any]) -> None:
        if self.echoue:
            raise RuntimeError("base injoignable")
        self.appels_reprise.append((command, config))

    @property
    def repris(self) -> list[Any]:
        return [command.resume for command, _ in self.appels_reprise]


class FauxAgent:
    """Agent interne minimal : émet deux événements, garde sa config."""

    def __init__(self) -> None:
        self.config = {"recursion_limit": 42}
        self.entrees: list[Any] = []

    async def run(self, input_data: Any):
        self.entrees.append(input_data)
        yield {"type": "RUN_STARTED"}
        yield {"type": "RUN_FINISHED"}


def _entree(*, thread_id: str = "fil-1", resume: Any = None) -> dict[str, Any]:
    return {"thread_id": thread_id, "run_id": "run-1", "messages": [], "resume": resume}


def _executer(purge: PurgeInterruptOrphelin, entree: Any) -> list[dict[str, Any]]:
    """Le projet teste l'asynchrone via ``asyncio.run`` (pas de pytest-asyncio)."""

    async def _collecter() -> list[dict[str, Any]]:
        return [event async for event in purge.run(entree)]

    return asyncio.run(_collecter())


def test_decisions_refus_une_par_action_en_attente() -> None:
    assert decisions_refus(_snapshot(nexts=("model",), action_requests=2)) == [
        {"type": "reject", "message": MOTIF_REFUS},
        {"type": "reject", "message": MOTIF_REFUS},
    ]


def test_decisions_refus_fil_termine_ne_rien_refuser() -> None:
    assert decisions_refus(_snapshot(nexts=())) == []
    assert decisions_refus(None) == []


def test_decisions_refus_sans_payload_compte_une_par_interruption() -> None:
    snapshot = SimpleNamespace(
        next=("tools",),
        tasks=(SimpleNamespace(interrupts=(SimpleNamespace(value=None),)),),
    )
    assert decisions_refus(snapshot) == [{"type": "reject", "message": MOTIF_REFUS}]


def test_run_sans_resume_purge_puis_traite_le_message() -> None:
    graphe = FauxGraphe(_snapshot(nexts=("model",), action_requests=1))
    agent = FauxAgent()

    evenements = _executer(PurgeInterruptOrphelin(agent, graphe), _entree())

    assert [e["type"] for e in evenements] == ["RUN_STARTED", "RUN_FINISHED"]
    # Une reprise, un refus, le fil du run, et la même borne d'itérations.
    # ``{"decisions": [...]}`` et jamais la liste nue : le middleware HITL
    # relit la reprise par ``interrupt(...)['decisions']`` (constat 28/10 —
    # une liste nue plantait la purge à chaque fil orphelin).
    assert graphe.repris == [{"decisions": [{"type": "reject", "message": MOTIF_REFUS}]}]
    assert graphe.appels_reprise[0][1] == {
        "recursion_limit": 42,
        "configurable": {"thread_id": "fil-1"},
    }
    # Le message part bien vers l'agent, après la purge.
    assert agent.entrees == [_entree()]


def test_run_avec_resume_ne_purge_pas() -> None:
    graphe = FauxGraphe(_snapshot(nexts=("model",), action_requests=1))
    entree = _entree(resume=[{"type": "approve", "interruptId": "i-1"}])

    evenements = _executer(PurgeInterruptOrphelin(FauxAgent(), graphe), entree)

    assert len(evenements) == 2
    assert graphe.appels_reprise == []
    assert graphe.appels_etat == []


def test_run_fil_sans_interrupt_ne_purge_pas() -> None:
    graphe = FauxGraphe(_snapshot(nexts=()))

    evenements = _executer(PurgeInterruptOrphelin(FauxAgent(), graphe), _entree())

    assert len(evenements) == 2
    assert graphe.appels_reprise == []


def test_purge_en_echec_laisse_le_run_continuer() -> None:
    graphe = FauxGraphe(_snapshot(nexts=("model",), action_requests=1), echoue=True)

    evenements = _executer(PurgeInterruptOrphelin(FauxAgent(), graphe), _entree())

    assert [e["type"] for e in evenements] == ["RUN_STARTED", "RUN_FINISHED"]


def test_run_sans_thread_id_ne_touche_pas_au_graphe() -> None:
    graphe = FauxGraphe(_snapshot(nexts=("model",), action_requests=1))

    _executer(PurgeInterruptOrphelin(FauxAgent(), graphe), {"threadId": None, "messages": []})

    assert graphe.appels_etat == []


def test_clone_conserve_le_graphe_et_reutilise_l_agent() -> None:
    graphe = FauxGraphe(_snapshot(nexts=()))
    agent = FauxAgent()
    clone = PurgeInterruptOrphelin(agent, graphe).clone()

    assert clone._graph is graphe  # noqa: SLF001 - contrat du clone
    assert clone._interne is agent  # noqa: SLF001 - agent sans clone propre
