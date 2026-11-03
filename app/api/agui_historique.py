"""Historique d'entrée AG-UI : arguments d'appels d'outils toujours JSON valide.

**Constat de recette (01/11).** Un run interrompu en plein appel d'outil (flux
SSE coupé par le fournisseur : ``StreamChunkTimeoutError``, ``APIError``) peut
laisser dans l'historique du client un appel d'outil dont les ``arguments``
sont du JSON **cassé** — tronqué au milieu, ou deux fragments concaténés
(``Extra data: line 1 column 11``). Au tour suivant, ``ag-ui-langgraph``
rejoue l'historique du client et fait ``json.loads(tc.function.arguments)``
sur **chaque** appel (``utils.agui_messages_to_langgraph``) : l'exception
``JSONDecodeError`` plante le run **avant même d'appeler le modèle**, et ce à
chaque nouveau message — la conversation reste empoisonnée tant que l'appel
cassé est dans le fil.

**Correctif.** Un décorateur de la chaîne AG-UI (même gabarit que
``AgentFluxIdsUniques`` / ``PurgeInterruptOrphelin``) qui assainit
``input.messages`` **avant** de déléguer :

1. arguments déjà JSON valide → laissés tels quels (aucune copie) ;
2. arguments avec des ordures **après** une première valeur JSON complète
   (fragments collés) → la première valeur est conservée ;
3. arguments irrécupérables (tronqués) → ``{}`` : l'appel devient un appel
   sans paramètre, lu comme tel par le graphe.

Le tout est une **fonction pure** (:func:`assainir_entree_agui`), donc
testable sans extra AG-UI ni modèle. L'historique du client n'est jamais
modifié : seule la copie transmise à ``ag-ui-langgraph`` l'est. Le checkpoint
LangGraph, lui, est déjà assaini par ``HistoriqueOutilsCoherentMiddleware``
(``app/agents/historique_outils.py``) au moment d'appeler le modèle — les
deux couches couvrent des endroits différents de la même défaillance.

Journal : le nombre d'appels réparés, jamais leur contenu (AGENTS.md §9 —
les arguments sont des données métier potentiellement sensibles).
"""

from __future__ import annotations

import json
import logging
from typing import Any

_logger = logging.getLogger(__name__)

_DECODEUR = json.JSONDecoder()

#: Valeur de repli d'arguments illisibles : un appel sans paramètre, jamais
#: un contenu inventé (AGENTS.md §2.2 — ne jamais inventer une donnée).
ARGUMENTS_VIDES = "{}"


def _valeur(objet: Any, *cles: str) -> Any:
    """Lit une clé (dict) ou un attribut (modèle pydantic), sinon ``None``."""
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


def _avec(objet: Any, changements: dict[str, Any]) -> Any:
    """Copie modifiée : modèle pydantic, dict ou objet simple, jamais l'original."""
    if not changements:
        return objet
    copier = getattr(objet, "model_copy", None)
    if callable(copier):
        return copier(update=changements)
    if isinstance(objet, dict):
        return {**objet, **changements}
    for cle, valeur in changements.items():
        setattr(objet, cle, valeur)
    return objet


def reparer_arguments(brut: Any) -> tuple[Any, bool]:
    """Rend des arguments d'appel lisibles par ``json.loads``.

    Returns:
        ``(arguments, change)`` — ``change`` est vrai si une réparation a eu
        lieu. Les arguments déjà valides sont renvoyés **tels quels** (ni
        re-sérialisés, ni normalisés) : un historique sain ne doit pas être
        réécrit.

    Cas traités :

    * ``None`` / chaîne vide : déjà acceptés par ``ag-ui-langgraph``
      (sa garde ``if tc.function.arguments`` rend ``{}``) → inchangés ;
    * chaîne JSON valide → inchangée ;
    * chaîne avec ordures **après** une première valeur complète → la
      première valeur, re-sérialisée ;
    * chaîne tronquée / illisible → ``{}`` ;
    * valeur non-chaîne (dict déjà parsé par un client) → sérialisée.
    """
    if brut is None:
        return brut, False
    if not isinstance(brut, str):
        try:
            return json.dumps(brut, ensure_ascii=False), True
        except (TypeError, ValueError):
            return ARGUMENTS_VIDES, True
    texte = brut.strip()
    if not texte:
        return brut, False
    try:
        json.loads(texte)
        return brut, False
    except ValueError:
        pass
    # Fragments concaténés : garder la première valeur JSON complète.
    try:
        premiere, _ = _DECODEUR.raw_decode(texte)
    except ValueError:
        return ARGUMENTS_VIDES, True
    try:
        return json.dumps(premiere, ensure_ascii=False), True
    except (TypeError, ValueError):
        return ARGUMENTS_VIDES, True


def _assainir_appel(appel: Any) -> tuple[Any, bool]:
    """Répare les ``function.arguments`` d'un appel d'outil, s'il en porte."""
    fonction = _valeur(appel, "function")
    if fonction is None:
        return appel, False
    arguments = _valeur(fonction, "arguments")
    repares, change = reparer_arguments(arguments)
    if not change:
        return appel, False
    return (
        _avec(appel, {"function": _avec(fonction, {"arguments": repares})}),
        True,
    )


def _assainir_message(message: Any) -> tuple[Any, int]:
    """Répare les appels d'outils d'un message d'historique AG-UI."""
    appels = _valeur(message, "tool_calls", "toolCalls")
    if not appels:
        return message, 0
    reparations = 0
    nouveaux: list[Any] = []
    for appel in appels:
        repares, change = _assainir_appel(appel)
        reparations += 1 if change else 0
        nouveaux.append(repares)
    if not reparations:
        return message, 0
    # Même clé que l'entrée (snake_case ou camelCase) : écrire l'autre
    # variante ajouterait un champ ignoré par le lecteur.
    cle = "tool_calls" if _valeur(message, "tool_calls") is not None else "toolCalls"
    return _avec(message, {cle: nouveaux}), reparations


def assainir_entree_agui(entree: Any) -> tuple[Any, int]:
    """Assainit ``messages`` d'une entrée de run AG-UI.

    Returns:
        ``(entree, reparations)`` — l'entrée **elle-même** (identité
        conservée) si aucun appel n'a dû être réparé, sinon une copie avec
        les messages réparés. Les autres champs (``thread_id``, ``resume``,
        pièces jointes…) sont portés tels quels.
    """
    messages = _valeur(entree, "messages")
    if not messages:
        return entree, 0
    nouveaux: list[Any] = []
    reparations = 0
    for message in messages:
        repare, nombre = _assainir_message(message)
        reparations += nombre
        nouveaux.append(repare)
    if not reparations:
        return entree, 0
    return _avec(entree, {"messages": nouveaux}), reparations


class HistoriqueArgumentsSains:
    """Décorateur : l'entrée du run n'emporte jamais d'arguments illisibles.

    Monté dans la chaîne AG-UI (``app/api/agui.py``) autour de l'agent
    ``ag-ui-langgraph`` : le graphe, le checkpoint et l'historique du client
    ne sont pas touchés — seule l'entrée **relayée** l'est.
    """

    def __init__(self, interne: Any) -> None:
        self._interne = interne

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._interne, nom)

    def clone(self) -> HistoriqueArgumentsSains:
        """Un clone par requête : état isolé, même chaîne de décorateurs."""
        cloner = getattr(self._interne, "clone", None)
        interne = cloner() if callable(cloner) else self._interne
        return HistoriqueArgumentsSains(interne)

    async def run(self, input_data: Any):  # type: ignore[no-untyped-def]
        """Assainit l'entrée, puis réexpédie le flux tel quel."""
        entree, reparations = assainir_entree_agui(input_data)
        if reparations:
            # Le compte, jamais le contenu : diagnostiquer un fil cassé sans
            # journaliser de donnée métier (AGENTS.md §9).
            _logger.warning(
                "Historique AG-UI : %d appel(s) d'outil aux arguments JSON "
                "invalides réparé(s) avant relecture par ag-ui-langgraph",
                reparations,
            )
        async for event in self._interne.run(entree):
            yield event


__all__ = [
    "ARGUMENTS_VIDES",
    "HistoriqueArgumentsSains",
    "assainir_entree_agui",
    "reparer_arguments",
]
