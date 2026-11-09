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

import json
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

try:  # pragma: no cover - pydantic est une dépendance de base
    from pydantic import ValidationError
except ImportError:  # pragma: no cover - environnement dégradé
    ValidationError = ValueError  # type: ignore[assignment,misc]

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


def dedupliquer_appels(message: Any) -> tuple[Any, int]:
    """Retire les appels d'outils **identiques** (même nom, mêmes arguments).

    Certains modèles répètent le même appel dans un message. Le graphe exécute
    alors chaque appel : le second reste sans résultat exploitable, une carte
    reste « en préparation » et le modèle raisonne une fois de plus. Le premier
    appel est conservé (même id) ; les suivants strictement identiques sont
    retirés avant ``ToolNode``.
    """
    appels = getattr(message, "tool_calls", None)
    if not isinstance(appels, list) or len(appels) < 2:
        return message, 0
    vus: set[str] = set()
    uniques: list[Any] = []
    for appel in appels:
        cle = json.dumps(
            [appel.get("name"), appel.get("args")],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        if cle in vus:
            continue
        vus.add(cle)
        uniques.append(appel)
    doublons = len(appels) - len(uniques)
    if not doublons:
        return message, 0
    return message.model_copy(update={"tool_calls": uniques}), doublons


def dedupliquer_sequence(messages: Sequence[Any]) -> tuple[list[Any], int]:
    """Applique ``dedupliquer_appels`` à chaque message d'une séquence."""
    sortie: list[Any] = []
    total = 0
    for message in messages:
        propre, doublons = dedupliquer_appels(message)
        total += doublons
        sortie.append(propre)
    return sortie, total


def _appel_conforme(appel: Any, schemas: dict[str, Any]) -> bool:
    """Vrai si les arguments de l'appel satisfont le schéma de son outil.

    Un outil absent de ``schemas`` (nom inconnu, outil frontend) n'est **pas**
    jugé : l'erreur explicite du ``ToolNode`` est plus instructive que le refus.
    """
    if not isinstance(appel, dict):
        return True
    nom = appel.get("name")
    schema = schemas.get(nom) if isinstance(nom, str) else None
    if schema is None or not hasattr(schema, "model_validate"):
        return True
    args = appel.get("args")
    if not isinstance(args, dict):
        return False
    try:
        schema.model_validate(args)
    except ValidationError:
        return False
    return True


def filtrer_appels_invalides(message: Any, schemas: dict[str, Any]) -> tuple[Any, int]:
    """Retire les appels dont les arguments violent le schéma de l'outil.

    Constat : ``qwen-3.8-27b`` émet parfois, dans le même message qu'un appel
    valide, un second appel **incomplet** (``query_analytics {}``). Cet appel ne
    peut rien produire : il laissait une carte en erreur dans le fil, en plus de
    celle du véritable besoin. Il est donc refusé avant ``ToolNode``.

    **Exception délibérée** : si **aucun** appel du message n'est conforme, ils
    sont tous conservés. Les retirer tous laisserait ``ToolNode`` sans travail et
    laisserait le modèle sans retour sur son erreur — il relancerait à l'aveugle,
    et le tour finirait sur une réponse vide. Dans ce cas, l'appel invalide reçoit
    un ``ToolMessage`` ``status="error"`` qui lui apprend le schéma attendu.

    Returns:
        ``(message, appels_refuses)`` — le message **lui-même** si rien n'est
        écarté, sinon une copie dont ``tool_calls`` est réduit.
    """
    appels = getattr(message, "tool_calls", None)
    if not isinstance(appels, list) or not appels or not schemas:
        return message, 0
    conformes = [appel for appel in appels if _appel_conforme(appel, schemas)]
    refuses = len(appels) - len(conformes)
    if not refuses or not conformes:
        return message, 0
    return message.model_copy(update={"tool_calls": conformes}), refuses


def filtrer_sequence_invalides(
    messages: Sequence[Any], schemas: dict[str, Any]
) -> tuple[list[Any], int]:
    """Applique ``filtrer_appels_invalides`` à chaque message d'une séquence."""
    sortie: list[Any] = []
    total = 0
    for message in messages:
        propre, refuses = filtrer_appels_invalides(message, schemas)
        total += refuses
        sortie.append(propre)
    return sortie, total


def schemas_outils(request: Any) -> dict[str, Any]:
    """Schémas d'arguments des outils offerts au modèle, par nom."""
    schemas: dict[str, Any] = {}
    for outil in getattr(request, "tools", None) or ():
        nom = getattr(outil, "name", None)
        schema = getattr(outil, "args_schema", None)
        if isinstance(nom, str) and nom:
            schemas[nom] = schema
    return schemas


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
    def _reponse_complete(reponse: Any, schemas: dict[str, Any] | None = None) -> Any:
        """Réponse du modèle dont chaque appel porte un id (sinon ToolNode casse).

        Trois passages, dans cet ordre : ids manquants complétés, appels
        strictement identiques dédupliqués, appels dont les arguments violent le
        schéma refusés.

        Le gestionnaire rend un ``ModelResponse`` (dataclass ``result`` + sortie
        structurée), pas un ``AIMessage`` : réparer seulement ce dernier laissait
        passer l'appel sans id, et ``ToolNode`` levait
        ``tool_call_id … input_value=None``.
        """
        schemas = schemas or {}
        resultat = getattr(reponse, "result", None)
        if isinstance(resultat, list):
            repares, ajoutes = completer_identifiants_sequence(resultat)
            repares, doublons = dedupliquer_sequence(repares)
            repares, refuses = filtrer_sequence_invalides(repares, schemas)
            if not (ajoutes or doublons or refuses):
                return reponse
            if ajoutes:
                _logger.info("Appels d'outils sans id complétés : %d", ajoutes)
            if doublons:
                _logger.info("Appels d'outils identiques ignorés : %d", doublons)
            if refuses:
                _logger.info("Appels d'outils non conformes au schéma refusés : %d", refuses)
            return replace(reponse, result=repares)
        repare, ajoutes = completer_identifiants(reponse)
        repare, doublons = dedupliquer_appels(repare)
        repare, refuses = filtrer_appels_invalides(repare, schemas)
        if ajoutes:
            _logger.info("Appels d'outils sans id complétés : %d", ajoutes)
        if doublons:
            _logger.info("Appels d'outils identiques ignorés : %d", doublons)
        if refuses:
            _logger.info("Appels d'outils non conformes au schéma refusés : %d", refuses)
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
        return self._reponse_complete(handler(self._coherent(request)), schemas_outils(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return self._reponse_complete(
            await handler(self._coherent(request)), schemas_outils(request)
        )


__all__ = [
    "PREFIXE_APPEL",
    "REPONSE_ABSENTE",
    "HistoriqueOutilsCoherentMiddleware",
    "assainir_sequence_outils",
    "completer_identifiants",
    "completer_identifiants_sequence",
    "dedupliquer_appels",
    "dedupliquer_sequence",
    "filtrer_appels_invalides",
    "filtrer_sequence_invalides",
    "identifiant_appel",
    "schemas_outils",
]
