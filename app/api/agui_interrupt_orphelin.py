"""Purge d'une interruption HITL restée sans réponse—avant un nouveau run.

**Constat de recette.** Un questionnaire HITL quitté sans réponse (onglet fermé
pendant l'interrupt, fil rouvert plus tard) bloquait le fil : les messages
suivants n'étaient plus traités et l'interface restait sur « L'agent écrit… ».

**Cause.** ``ag-ui-langgraph`` court-circuite tout run **sans ``resume``**
lorsque le fil porte encore une interruption active : il réémet l'interruption
au lieu d'exécuter le graphe. Le nouveau message n'atteint donc jamais
l'agent—et rien ne le signale à l'utilisateur.

**Correctif.** Avant de déléguer un run sans reprise, l'état du fil est lu :

- fil sans tâche en attente → rien à faire, comportement inchangé ;
- fil avec tâches interrompues → chaque action en attente est **refusée**
  (``{"type": "reject"}``, la voie déjà offerte par le questionnaire), le
  graphe repart d'un état cohérent, puis le message de l'utilisateur est
  traité normalement.

**Pourquoi un refus et pas une approbation.** Un orphelin est une question
restée sans réponse humaine : personne n'a validé l'action. Le refus est le
seul choix qui n'exécute rien de sensible (AGENTS.md §2.6, règle 6 [C]). Le
message de refus indique au modèle pourquoi, pour qu'il ne relance pas le même
appel d'outil.

**Un run qui porte un ``resume`` n'est jamais touché** : l'utilisateur répond
au questionnaire, c'est une décision humaine qui doit atteindre l'agent.

La purge est **best-effort** : en cas d'échec (état illisible, base coupée),
l'incident est journalisé et le run continue comme avant—une purge ratée ne
doit pas empêcher de parler à l'agent.
"""

from __future__ import annotations

import logging
from typing import Any

try:  # pragma: no cover - dépend de l'installation de langgraph
    from langgraph.types import Command
except ImportError:  # pragma: no cover
    Command = None  # type: ignore[assignment,misc]

_logger = logging.getLogger(__name__)

#: Motif transmis au modèle pour l'action refusée (affiché dans l'historique).
MOTIF_REFUS = (
    "Questionnaire resté sans réponse (session précédente) : action abandonnée. "
    "Ne relance pas cet appel d'outil sans nouvelle demande de l'utilisateur."
)


def _valeur(objet: Any, *cles: str) -> Any:
    if objet is None:
        return None
    if isinstance(objet, dict):
        for cle in cles:
            if cle in objet:
                return objet[cle]
        return None
    for cle in cles:
        if hasattr(objet, cle):
            return getattr(objet, cle)
    return None


def _texte(valeur: Any) -> str | None:
    return valeur if isinstance(valeur, str) and valeur else None


def _taches(snapshot: Any) -> list[Any]:
    """Tâches du snapshot, quel que soit le conteneur (tuple ou dict)."""
    taches = getattr(snapshot, "tasks", None)
    if not taches:
        return []
    if isinstance(taches, dict):
        return list(taches.values())
    return list(taches)


def _interruptions(tache: Any) -> list[Any]:
    brut = getattr(tache, "interrupts", None)
    if not brut:
        return []
    return list(brut) if not isinstance(brut, dict) else list(brut.values())


def decisions_refus(snapshot: Any) -> list[dict[str, str]]:
    """Une décision de refus par action en attente (0 si fil sans interrupt).

    Le middleware HITL exige **une décision par appel d'outil suspendu** : le
    payload d'une interruption porte ``action_requests`` (parfois plusieurs
    appels dans une seule question). À défaut de payload lisible, on compte une
    décision par interruption—au pire le middleware refuse le lot et l'échec est
    journalisé, jamais silencieux.
    """
    if snapshot is None or not getattr(snapshot, "next", None):
        return []
    decisions: list[dict[str, str]] = []
    for tache in _taches(snapshot):
        for interruption in _interruptions(tache):
            valeur = getattr(interruption, "value", interruption)
            requetes = valeur.get("action_requests") if isinstance(valeur, dict) else None
            nombre = len(requetes) if isinstance(requetes, list) and requetes else 1
            decisions.extend({"type": "reject", "message": MOTIF_REFUS} for _ in range(nombre))
    return decisions


class PurgeInterruptOrphelin:
    """Décorateur : un fil bloqué sur une question sans réponse est libéré."""

    def __init__(self, interne: Any, graph: Any) -> None:
        self._interne = interne
        self._graph = graph

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._interne, nom)

    def clone(self) -> PurgeInterruptOrphelin:
        """Un clone par requête : même graphe (résolu au run), chaîne réisolée."""
        cloner = getattr(self._interne, "clone", None)
        interne = cloner() if callable(cloner) else self._interne
        return PurgeInterruptOrphelin(interne, self._graph)

    async def run(self, input_data: Any):
        await self._purger(input_data)
        async for event in self._interne.run(input_data):
            yield event

    def _config(self, thread_id: str) -> dict[str, Any]:
        """Config de reprise : même ``recursion_limit`` que le run d'origine."""
        base = getattr(self._interne, "config", None)
        config: dict[str, Any] = dict(base) if isinstance(base, dict) else {}
        configurable = dict(config.get("configurable") or {})
        configurable["thread_id"] = thread_id
        config["configurable"] = configurable
        return config

    async def _purger(self, input_data: Any) -> None:
        if Command is None:  # pragma: no cover - extra absent
            return
        if _valeur(input_data, "resume"):
            # L'utilisateur répond au questionnaire : décision humaine, intacte.
            return
        thread_id = _texte(_valeur(input_data, "thread_id", "threadId"))
        if not thread_id:
            return
        config = self._config(thread_id)
        try:
            snapshot = await self._graph.aget_state(config)
            decisions = decisions_refus(snapshot)
            if not decisions:
                return
            _logger.info(
                "Interrupt HITL orphelin purgé (fil %s) : %d action(s) refusée(s)",
                thread_id,
                len(decisions),
            )
            # ``{"decisions": [...]}`` et jamais la liste nue : le middleware
            # HITL (langchain) relit la reprise par ``interrupt(...)['decisions']``
            # — une liste nue leve ``TypeError: list indices must be integers
            # or slices, not str`` (constat 28/10 : la purge echouait a chaque
            # fois et le fil restait bloque). Meme forme que la reprise metier
            # de ``app/agents/generateur_offre.py``.
            await self._graph.ainvoke(
                Command(resume={"decisions": decisions}), config
            )
        except Exception as exc:  # noqa: BLE001 - la purge ne bloque jamais le run
            _logger.warning(
                "Purge d'interrupt orphelin impossible (fil %s) : %s: %s",
                thread_id,
                type(exc).__name__,
                exc,
                exc_info=exc,
            )


__all__ = ["MOTIF_REFUS", "PurgeInterruptOrphelin", "decisions_refus"]
