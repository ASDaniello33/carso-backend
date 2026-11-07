"""Historique de messages rendu conforme à la grammaire `tool_calls` / `tool`.

**Constat de recette (31/10).** Un fil de conversation s'est bloqué : chaque
nouveau message échouait avec

    Invalid parameter: messages with role 'tool' must be a response to a
    preceeding message with 'tool_calls'.

L'erreur ne vient pas du frontend (CopilotKit ne fait que relayer l'événement
``agent_run_error_event``) : c'est le fournisseur de modèle qui refuse **toute
la requête**. Le protocole OpenAI impose une grammaire stricte :

    assistant { tool_calls: [A, B] }   ← l'assistant demande les outils
    tool      { tool_call_id: A }      ← réponse de A
    tool      { tool_call_id: B }      ← réponse de B
    user / assistant …                 ← la suite

Un message ``tool`` **sans** message assistant porteur du ``tool_calls``
correspondant, ou un ``tool_calls`` resté sans réponse, viole cette grammaire —
et le fournisseur rejette le run entier, pas seulement le message fautif.

**Pourquoi l'historique se casse.** Il est persisté dans le checkpoint LangGraph
du fil (ADR 0009) et rejoué à chaque tour. Un run interrompu au milieu d'un
appel d'outil (panneau fermé, navigation, coupure réseau, échec du fournisseur —
dont l'erreur ``function.name`` du 31/10) peut laisser l'une sans l'autre.
``ag-ui-langgraph`` fusionne en outre les messages du client dans l'état par
identifiant : un ``ToolMessage`` dont le message assistant n'a jamais été
persisté entre alors comme orphelin. ``PatchToolCallsMiddleware`` (Deep Agents)
complète les appels sans réponse mais **ne retire jamais** un orphelin.

**Correctif.** Un middleware de dernier passage, appliqué à la requête du
modèle — donc après la troncature d'historique et avant le fournisseur :

1. un message assistant porteur d'appels est immédiatement suivi d'**une**
   réponse par appel, dans l'ordre déclaré ; une réponse réelle est replacée
   auprès de son appel même si elle se trouvait plus loin, sinon une réponse
   synthétique est produite ;
2. tout message ``tool`` non déclaré par un message assistant est écarté ;
3. les doublons de réponse pour un même ``tool_call_id`` sont réduits à un.

L'assainissement est **une fonction pure** (:func:`assainir_sequence_outils`),
donc testable sans modèle. Il s'applique à la requête, jamais à l'état : le
checkpoint n'est pas réécrit, l'historique utilisateur n'est pas modifié, et un
fil déjà cassé redevient utilisable au tour suivant — sans migration ni purge.

**Ce qui n'est pas touché.** Un historique sain revient **inchangé** (le
middleware n'appelle alors pas ``override``). Les noms des outils frontend ne
sont jamais réécrits : ils sont le contrat d'affichage passé par le client.

**Ids d'appels absents (constat du 06/11).** ``AIMessage`` accepte
``tool_calls=[{"id": None, …}]`` — certains fournisseurs OpenAI-compatibles
émettent des appels sans identifiant — mais ``ToolMessage`` refuse ``None``::

    1 validation error for ToolMessage
    tool_call_id
      Input should be a valid string [type=string_type, input_value=None…]

Le run entier échoue alors au premier outil exécuté. Le complément d'id
(:func:`completer_identifiants`) est donc appliqué à trois endroits : l'état au
début du run (fil checkpointé ou messages fusionnés par le client), la requête
du modèle (dernier filet), et la **réponse** du modèle — sinon ``ToolNode``
construit son ``ToolMessage`` avec ``None``. Un id déclaré n'est jamais réécrit.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

try:  # pragma: no cover - dépend de l'extra agents
    from langchain.agents.middleware.types import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover - extra agents absent
    _AgentMiddleware = object  # type: ignore[misc,assignment]

try:  # pragma: no cover - dépend de l'extra agents
    from langchain_core.messages import AIMessage, ToolMessage
except ImportError:  # pragma: no cover - extra agents absent
    AIMessage = None  # type: ignore[assignment,misc]
    ToolMessage = None  # type: ignore[assignment,misc]

_logger = logging.getLogger(__name__)

#: Contenu d'une réponse fabriquée pour un appel resté sans résultat. Le modèle
#: doit savoir qu'il ne s'agit pas d'une donnée métier — jamais d'invention.
REPONSE_ABSENTE = (
    "Aucun résultat n'a été enregistré pour cet appel d'outil : le tour a été "
    "interrompu ou l'historique est incomplet. Ne réutilise aucune donnée de cet "
    "appel et ne le relance pas de ta propre initiative ; demande confirmation à "
    "l'utilisateur si son résultat est nécessaire."
)

#: Préfixe des ids d'appels complétés par CARSO : permet de distinguer un id
#: fabriqué d'un id fournisseur, sans jamais réécrire ce dernier.
PREFIXE_APPEL = "call_"


def _identifiant_neuf() -> str:
    return f"{PREFIXE_APPEL}{uuid.uuid4().hex[:24]}"


def identifiant_appel(declaration: Any) -> str:
    """Id exploitable pour un appel d'outil : déclaré s'il existe, sinon neuf.

    ``ToolMessage`` exige une chaîne non ``None`` **et** le fournisseur exige
    l'id exact de l'appel : on ne réutilise donc un id que s'il est déjà une
    chaîne non vide. Utilisé comme dernier filet par les réponses de refus.
    """
    if isinstance(declaration, dict):
        ident = declaration.get("id")
        if isinstance(ident, str) and ident.strip():
            return ident
    return _identifiant_neuf()


def _completer_appels(appels: Sequence[Any]) -> tuple[list[Any], int]:
    """Appels dont chacun porte un id non vide ; compte les ids ajoutés."""
    complets: list[Any] = []
    ajoutes = 0
    for appel in appels:
        if isinstance(appel, dict):
            ident = appel.get("id")
            if isinstance(ident, str) and ident.strip():
                complets.append(appel)
                continue
            complets.append({**appel, "id": _identifiant_neuf()})
            ajoutes += 1
            continue
        complets.append(appel)
    return complets, ajoutes


def completer_identifiants(message: Any) -> tuple[Any, int]:
    """Message assistant dont chaque appel d'outil porte un id non vide.

    Returns:
        ``(message, ids_ajoutes)`` — le message **lui-même** si aucun id ne
        manquait, sinon une copie (même ``id`` de message, contenu intact).
    """
    if AIMessage is None or not isinstance(message, AIMessage):
        return message, 0
    mises: dict[str, Any] = {}
    total = 0
    for nom in ("tool_calls", "invalid_tool_calls"):
        appels = getattr(message, nom, None) or []
        complets, ajoutes = _completer_appels(appels)
        if ajoutes:
            mises[nom] = complets
            total += ajoutes
    if not total:
        return message, 0
    return message.model_copy(update=mises), total


def completer_identifiants_sequence(messages: Sequence[Any]) -> tuple[list[Any], int]:
    """Complète les ids d'appels d'une liste, en préservant l'identité.

    Returns:
        ``(messages, ids_ajoutes)`` ; les messages inchangés sont **les mêmes
        objets** que ceux de l'entrée, ce qui permet de ne rien réécrire en
        aval quand aucun id ne manquait.
    """
    complets: list[Any] = []
    total = 0
    for message in messages:
        repare, ajoutes = completer_identifiants(message)
        complets.append(repare)
        total += ajoutes
    return complets, total


def _appels(message: Any) -> list[dict[str, Any]]:
    """Appels d'outils déclarés par un message assistant (valides ou non).

    ``invalid_tool_calls`` compte : le fournisseur exige **aussi** une réponse
    pour un appel aux arguments mal formés (c'est ce que fait Deep Agents).
    """
    if AIMessage is None or not isinstance(message, AIMessage):
        return []
    return [*getattr(message, "tool_calls", ()), *getattr(message, "invalid_tool_calls", ())]


def _identifiants(appels: Sequence[dict[str, Any]]) -> list[str]:
    """Identifiants déclarés, dans l'ordre, sans répétition."""
    vus: list[str] = []
    for appel in appels:
        ident = appel.get("id")
        if isinstance(ident, str) and ident and ident not in vus:
            vus.append(ident)
    return vus


def assainir_sequence_outils(messages: Sequence[Any]) -> list[Any]:
    """Liste de messages conforme à la grammaire ``tool_calls`` / ``tool``.

    Ne modifie ni les messages ni leur contenu : seuls l'ordre des réponses et
    les messages réellement inconciliables changent.

    Returns:
        Une nouvelle liste ; ses éléments sont **les mêmes objets** que ceux de
        l'entrée (hors réponses synthétiques). L'appelant peut donc comparer par
        identité pour ne rien réécrire quand rien ne change.
    """
    if AIMessage is None or ToolMessage is None:  # pragma: no cover - extra absent
        return list(messages)
    # 0. Un appel sans id ne peut pas recevoir de réponse : on en fabrique un
    #    avant de raisonner sur les paires (``ToolMessage`` refuse ``None``).
    liste, _ = completer_identifiants_sequence(list(messages))

    # 1. Ce que chaque message assistant a demandé (l'appel fait foi).
    declarations: dict[str, str] = {}
    for message in liste:
        for appel in _appels(message):
            ident = appel.get("id")
            if isinstance(ident, str) and ident and ident not in declarations:
                declarations[ident] = str(appel.get("name") or "")

    # 2. Réponses réelles, une par identifiant ; le reste est orphelin.
    reponses: dict[str, Any] = {}
    ecartes = 0
    for message in liste:
        if not isinstance(message, ToolMessage):
            continue
        ident = getattr(message, "tool_call_id", None)
        if isinstance(ident, str) and ident in declarations and ident not in reponses:
            reponses[ident] = message
        else:
            ecartes += 1

    # 3. Reconstruction : chaque appel est suivi de sa réponse.
    resultat: list[Any] = []
    synthetisees = 0
    for message in liste:
        if isinstance(message, ToolMessage):
            continue  # replacée auprès de son appel, ou écartée
        resultat.append(message)
        for ident in _identifiants(_appels(message)):
            reelle = reponses.pop(ident, None)
            if reelle is not None:
                resultat.append(reelle)
                continue
            synthetisees += 1
            resultat.append(
                ToolMessage(
                    content=REPONSE_ABSENTE,
                    tool_call_id=ident,
                    name=declarations.get(ident) or None,
                    status="error",
                )
            )

    if ecartes or synthetisees:
        # Jamais le contenu : seulement le compte, pour diagnostiquer un fil
        # cassé sans journaliser de donnée métier (AGENTS.md §9).
        _logger.info(
            "Historique d'outils assaini : %d réponse(s) orpheline(s) écartée(s), "
            "%d réponse(s) synthétisée(s)",
            ecartes,
            synthetisees,
        )
    return resultat


def _inchange(avant: Sequence[Any], apres: Sequence[Any]) -> bool:
    """Vrai si la liste assainie est identique, élément par élément (identité)."""
    return len(avant) == len(apres) and all(a is b for a, b in zip(avant, apres, strict=True))


class HistoriqueOutilsCoherentMiddleware(_AgentMiddleware):
    """Dernier passage avant le modèle : l'historique d'outils est cohérent.

    Deep Agents insère les middlewares utilisateur **après**
    ``SummarizationMiddleware`` et ``PatchToolCallsMiddleware`` : ce contrôle
    voit donc les messages effectivement envoyés au fournisseur, y compris
    après une troncature d'historique.
    """

    @property
    def name(self) -> str:
        return "HistoriqueOutilsCoherentMiddleware"

    @staticmethod
    def _coherent(request: Any) -> Any:
        messages = getattr(request, "messages", None)
        if not messages:
            return request
        propre = assainir_sequence_outils(messages)
        if _inchange(messages, propre):
            return request
        return request.override(messages=propre)

    @staticmethod
    def _reponse_complete(reponse: Any) -> Any:
        """Réponse du modèle dont chaque appel porte un id (sinon ToolNode casse).

        Le gestionnaire rend un ``ModelResponse`` (dataclass ``result`` + sortie
        structurée), pas un ``AIMessage`` : réparer seulement ce dernier laissait
        passer l'appel sans id, et ``ToolNode`` levait
        ``tool_call_id … input_value=None``.
        """
        resultat = getattr(reponse, "result", None)
        if isinstance(resultat, list):
            repares, ajoutes = completer_identifiants_sequence(resultat)
            if not ajoutes:
                return reponse
            _logger.info("Appels d'outils sans id complétés : %d", ajoutes)
            return replace(reponse, result=repares)
        repare, ajoutes = completer_identifiants(reponse)
        if ajoutes:
            _logger.info("Appels d'outils sans id complétés : %d", ajoutes)
        return repare

    def _etat_complet(self, state: Any) -> dict[str, Any] | None:
        """État du fil rendu conforme (checkpoint ou messages fusionnés)."""
        messages = state.get("messages") if isinstance(state, dict) else None
        if not messages:
            return None
        repares, ajoutes = completer_identifiants_sequence(messages)
        if not ajoutes:
            return None
        _logger.info("Historique : %d appel(s) d'outil sans id complété(s)", ajoutes)
        return {"messages": repares}

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._etat_complet(state)

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._etat_complet(state)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return self._reponse_complete(handler(self._coherent(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return self._reponse_complete(await handler(self._coherent(request)))


__all__ = [
    "PREFIXE_APPEL",
    "REPONSE_ABSENTE",
    "HistoriqueOutilsCoherentMiddleware",
    "assainir_sequence_outils",
    "completer_identifiants",
    "completer_identifiants_sequence",
    "identifiant_appel",
]
