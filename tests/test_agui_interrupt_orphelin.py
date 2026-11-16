"""Tests de la purge d'interrupt HITL orphelin (constat de recette)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.agui import StreamingRunError, _code_erreur
from app.api.agui_interrupt_orphelin import (
    MOTIF_REFUS,
    MOTIF_SANS_REPONSE,
    PurgeInterruptOrphelin,
    decisions_refus,
)


def _snapshot(
    *,
    nexts: tuple[str, ...],
    action_requests: int = 0,
    decisions_autorisees: tuple[str, ...] = ("approve", "edit", "reject"),
    nom_outil: str = "outil_sensible",
) -> Any:
    interruptions = ()
    if action_requests:
        payload = {
            "action_requests": [{"name": nom_outil} for _ in range(action_requests)],
            "review_configs": [
                {
                    "action_name": nom_outil,
                    "allowed_decisions": list(decisions_autorisees),
                }
                for _ in range(action_requests)
            ],
        }
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


def _executer(decorateur: Any, entree: Any) -> list[Any]:
    """Le projet teste l'asynchrone via ``asyncio.run`` (pas de pytest-asyncio)."""

    async def _collecter() -> list[Any]:
        return [event async for event in decorateur.run(entree)]

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


def test_questionnaire_orphelin_recoit_respond_pas_reject() -> None:
    """Constat 09/11 : ``poser_questionnaire`` n'accepte que ``respond``.

    Un ``reject`` y levait ``ValueError: Decision type 'reject' is not allowed
    for tool 'poser_questionnaire'`` : la purge échouait, l'interruption
    survivait, et le message suivant ne partait jamais.
    """
    snapshot = _snapshot(
        nexts=("model",),
        action_requests=1,
        decisions_autorisees=("respond",),
        nom_outil="poser_questionnaire",
    )
    assert decisions_refus(snapshot) == [{"type": "respond", "message": MOTIF_SANS_REPONSE}]


def test_decision_par_outil_dans_un_meme_tour() -> None:
    """Un questionnaire et un outil sensible dans le même lot : deux décisions."""
    payload = {
        "action_requests": [{"name": "poser_questionnaire"}, {"name": "envoyer_email"}],
        "review_configs": [
            {"action_name": "poser_questionnaire", "allowed_decisions": ["respond"]},
            {"action_name": "envoyer_email", "allowed_decisions": ["approve", "reject"]},
        ],
    }
    snapshot = SimpleNamespace(
        next=("model",), tasks=(SimpleNamespace(interrupts=(SimpleNamespace(value=payload),)),)
    )
    assert decisions_refus(snapshot) == [
        {"type": "respond", "message": MOTIF_SANS_REPONSE},
        {"type": "reject", "message": MOTIF_REFUS},
    ]


def test_outil_sans_decision_sure_n_envoie_rien() -> None:
    """Ni ``reject`` ni ``respond`` : rien n'est décidé à la place de l'humain."""
    snapshot = _snapshot(
        nexts=("model",), action_requests=1, decisions_autorisees=("approve", "edit")
    )
    assert decisions_refus(snapshot) == []


def test_configuration_non_alignee_retombe_sur_le_refus() -> None:
    """``review_configs`` absente : le refus reste la voie sûre."""
    payload = {"action_requests": [{"name": "outil_sensible"}]}
    snapshot = SimpleNamespace(
        next=("model",), tasks=(SimpleNamespace(interrupts=(SimpleNamespace(value=payload),)),)
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


def _refus(statut: int, corps: str) -> Exception:
    """Erreur de fournisseur telle que les SDK la lèvent (statut + corps)."""
    exc = RuntimeError(corps)
    exc.status_code = statut  # type: ignore[attr-defined]
    return exc


class FauxGrapheQuiEchoueAuRun(FauxGraphe):
    """L'état se lit, la **reprise** échoue : panne de run, pas d'accès."""

    def __init__(self, erreur: BaseException) -> None:
        super().__init__(_snapshot(nexts=("model",), action_requests=1))
        self.erreur = erreur

    async def ainvoke(self, command: Any, config: dict[str, Any]) -> None:
        raise self.erreur


def test_panne_de_run_dans_la_purge_remonte_au_client() -> None:
    """Constat 16/11 : un 503 pendant la reprise était avalé, tour « réussi ».

    La purge consomme un appel modèle qui appartient à l'utilisateur. Une panne
    classée autrement que ``erreur_run`` ne doit donc pas disparaître.
    """
    echec = _refus(503, "Internal server error")
    graphe = FauxGrapheQuiEchoueAuRun(echec)
    agent = FauxAgent()
    purge = PurgeInterruptOrphelin(agent, graphe, classer_erreur=_code_erreur)

    with pytest.raises(RuntimeError, match="Internal server error"):
        _executer(purge, _entree())

    # Rien n'a été traité : le message ne part pas sur un fil dans un état douteux.
    assert agent.entrees == []


def test_echec_interne_de_la_purge_reste_best_effort() -> None:
    """Une cause non classée (``erreur_run``) ne bloque toujours pas le chat."""
    graphe = FauxGrapheQuiEchoueAuRun(TypeError("reprise mal formée"))
    agent = FauxAgent()
    purge = PurgeInterruptOrphelin(agent, graphe, classer_erreur=_code_erreur)

    evenements = _executer(purge, _entree())

    assert [e["type"] for e in evenements] == ["RUN_STARTED", "RUN_FINISHED"]
    assert agent.entrees == [_entree()]


def test_la_panne_de_la_purge_devient_un_run_error() -> None:
    """Ordre de montage de production : pont **autour** de la purge."""
    graphe = FauxGrapheQuiEchoueAuRun(_refus(503, "Internal server error"))
    purge = PurgeInterruptOrphelin(FauxAgent(), graphe, classer_erreur=_code_erreur)
    pont = StreamingRunError(purge)

    evenements = _executer(pont, _entree())

    types = [e["type"] if isinstance(e, dict) else e.type for e in evenements]
    assert types == ["RUN_ERROR"]
    assert "503" in evenements[-1].message
    assert evenements[-1].code == "fournisseur_en_erreur"


def test_clone_conserve_le_classificateur() -> None:
    """Un clone par requête garde la politique d'erreur de la production."""
    purge = PurgeInterruptOrphelin(
        FauxAgent(), FauxGraphe(), classer_erreur=_code_erreur
    ).clone()

    assert purge._classer_erreur is _code_erreur  # noqa: SLF001 - contrat du clone


def test_clone_conserve_le_graphe_et_reutilise_l_agent() -> None:
    graphe = FauxGraphe(_snapshot(nexts=()))
    agent = FauxAgent()
    clone = PurgeInterruptOrphelin(agent, graphe).clone()

    assert clone._graph is graphe  # noqa: SLF001 - contrat du clone
    assert clone._interne is agent  # noqa: SLF001 - agent sans clone propre
