"""Chaque appel d'outil du flux AG-UI reçoit un résultat — même interrompu.

**Constat de recette (09/11).** Après un tour qui a enchaîné cinq appels
d'outils, la carte ``get_document_structure`` est restée « Préparation » alors
que le run était terminé : le modèle avait déclaré trois appels parallèles, deux
ont reçu leur ``ToolMessage``, le troisième non (run interrompu, appel resté
sans exécution, ou nœud d'outil abandonné en cours de route). L'interface
n'invente rien — elle affiche l'état qu'elle reçoit — mais une carte qui ne se
termine jamais laisse croire que le travail continue.

**Correctif.** ``ag_ui_langgraph`` émet ``TOOL_CALL_START`` puis, pour chaque
appel exécuté, ``TOOL_CALL_RESULT``. Un décorateur de la chaîne AG-UI (même
gabarit que ``AgentFluxIdsUniques`` / ``ResumeInterneMasque``) suit les appels
**démarrés sans résultat** et, juste avant l'événement terminal du run
(``RUN_FINISHED`` ou ``RUN_ERROR``), émet pour chacun un ``TOOL_CALL_RESULT``
porteur d'une erreur explicite ``{ok: false, error: {message}}`` — le contrat que
le rendu d'outil du chat sait déjà afficher (badge « Échec » + message).

**Ce qui n'est jamais clos.** Un run terminé sur une **interruption HITL**
(``RUN_FINISHED`` d'``outcome.type == "interrupt"``) attend une décision humaine :
l'appel n'a pas échoué, il est suspendu. On le laisse donc ouvert, sinon le
questionnaire apparaîtrait à côté d'une carte « Échec ».

**Portée.** Le flux seulement : l'état LangGraph et le checkpoint ne sont pas
réécrits. Côté modèle, la réponse manquante est déjà fabriquée au tour suivant
par ``app/agents/historique_outils.py`` ; ici, c'est l'**affichage** qui est
garanti, pas l'historique.
"""

from __future__ import annotations

import json
from typing import Any

try:  # pragma: no cover - dépend de l'installation de l'extra
    from ag_ui.core.events import ToolCallResultEvent
except ImportError:  # pragma: no cover - extra absent
    ToolCallResultEvent = None  # type: ignore[assignment,misc]

#: Événements qui closent un run ; les résultats manquants partent juste avant.
_TERMINAUX = frozenset({"RUN_FINISHED", "RUN_ERROR"})

#: Message affiché à la place du résultat absent. Il dit ce qui s'est passé et
#: quoi faire — jamais une donnée métier inventée (AGENTS.md §2.2).
MESSAGE_SANS_RESULTAT = (
    "Aucun résultat reçu pour cet appel d'outil : le tour s'est terminé avant son "
    "exécution. Relancez la demande si son résultat est nécessaire."
)

#: Préfixe des identifiants de message fabriqués pour un résultat synthétique.
PREFIXE_MESSAGE = "outil-"


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


def _texte(valeur: Any) -> str | None:
    return valeur if isinstance(valeur, str) and valeur else None


def type_evenement(evenement: Any) -> str:
    """Type d'un événement AG-UI, que ``type`` soit une chaîne ou un ``Enum``."""
    brut = _valeur(evenement, "type")
    if brut is None:
        return ""
    valeur = getattr(brut, "value", brut)
    return str(valeur)


def est_terminal(evenement: Any) -> bool:
    """Vrai pour les événements qui closent le flux du run."""
    return type_evenement(evenement) in _TERMINAUX


def est_interruption(evenement: Any) -> bool:
    """Vrai si le run se termine en attendant une décision humaine (HITL)."""
    if type_evenement(evenement) != "RUN_FINISHED":
        return False
    outcome = _valeur(evenement, "outcome")
    if outcome is None:
        return False
    type_outcome = _valeur(outcome, "type")
    valeur = getattr(type_outcome, "value", type_outcome)
    return valeur == "interrupt"


class SuiviAppelsOuverts:
    """Appels démarrés et pas encore répondus, dans l'ordre de démarrage.

    Pur : ne dépend d'aucun graphe — les tests le pilotent avec des événements
    factices. Une seule instance par run.
    """

    def __init__(self) -> None:
        #: ``tool_call_id`` → id du message assistant qui l'a demandé (facultatif).
        self._ouverts: dict[str, str | None] = {}

    def observer(self, evenement: Any) -> list[tuple[str, str | None]]:
        """Met le suivi à jour ; renvoie les appels à clore **avant** cet événement.

        Returns:
            ``[(tool_call_id, message_id)]``, vide sauf sur un événement terminal
            qui n'est pas une interruption HITL.
        """
        type_ev = type_evenement(evenement)
        if type_ev == "TOOL_CALL_START":
            ident = _texte(_valeur(evenement, "tool_call_id", "toolCallId"))
            if ident:
                self._ouverts[ident] = _texte(
                    _valeur(evenement, "parent_message_id", "parentMessageId")
                )
            return []
        if type_ev == "TOOL_CALL_RESULT":
            ident = _texte(_valeur(evenement, "tool_call_id", "toolCallId"))
            if ident:
                self._ouverts.pop(ident, None)
            return []
        if not est_terminal(evenement) or est_interruption(evenement):
            return []
        manquants = list(self._ouverts.items())
        self._ouverts.clear()
        return manquants

    @property
    def ouverts(self) -> tuple[str, ...]:
        """Ids encore en attente de résultat (lecture seule, pour les tests)."""
        return tuple(self._ouverts)


def resultat_absent(tool_call_id: str, message_id: str | None = None) -> Any:
    """Événement ``TOOL_CALL_RESULT`` d'erreur pour un appel resté sans réponse.

    Returns:
        L'événement AG-UI, ou ``None`` si l'extra ``ag-ui-core`` est absent
        (l'appelant relaie alors le flux tel quel — comportement d'avant).
    """
    if ToolCallResultEvent is None:  # pragma: no cover - extra absent
        return None
    contenu = json.dumps(
        {"ok": False, "error": {"message": MESSAGE_SANS_RESULTAT}},
        ensure_ascii=False,
    )
    return ToolCallResultEvent(
        toolCallId=tool_call_id,
        messageId=message_id or f"{PREFIXE_MESSAGE}{tool_call_id}",
        content=contenu,
        role="tool",
    )


class ResultatsOutilsComplets:
    """Décorateur : aucun appel d'outil ne finit sans résultat dans le flux.

    Monté en **dernier** dans la chaîne AG-UI (``app/api/agui.py``) : les ids
    sont déjà publics (``AgentFluxIdsUniques``) et l'erreur de run déjà convertie
    en ``RUN_ERROR`` (``StreamingRunError``). Les résultats synthétiques portent
    donc les ids que le client affiche, et précèdent l'événement terminal.
    """

    def __init__(self, interne: Any) -> None:
        self._interne = interne

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._interne, nom)

    def clone(self) -> ResultatsOutilsComplets:
        """Un clone par requête : suivi isolé, même chaîne de décorateurs."""
        cloner = getattr(self._interne, "clone", None)
        interne = cloner() if callable(cloner) else self._interne
        return ResultatsOutilsComplets(interne)

    async def run(self, input_data: Any):  # type: ignore[no-untyped-def]
        """Relaye le flux en closant les appels restés sans résultat."""
        suivi = SuiviAppelsOuverts()
        async for evenement in self._interne.run(input_data):
            for ident, message in suivi.observer(evenement):
                resultat = resultat_absent(ident, message)
                if resultat is not None:
                    yield resultat
            yield evenement


__all__ = [
    "MESSAGE_SANS_RESULTAT",
    "ResultatsOutilsComplets",
    "SuiviAppelsOuverts",
    "est_interruption",
    "est_terminal",
    "resultat_absent",
    "type_evenement",
]
