"""Appels de modèle internes du middleware : jamais dans le flux AG-UI.

**Constat de recette (06/11).** Sur un fil long, l'agent a répondu par un pavé
en anglais (« ``## SESSION INTENT`` / ``## SUMMARY`` / ``## ARTIFACTS`` /
``## NEXT STEPS`` ») collé devant sa vraie réponse. Ce texte n'est pas une
réponse de l'agent : c'est le **résumé de session** produit par le middleware de
summarisation de Deep Agents (``SummarizationMiddleware``, dont le prompt par
défaut est ``DEFAULT_SUMMARY_PROMPT`` de LangChain).

Pourquoi il devient visible : le middleware appelle le modèle **à l'intérieur**
de ``awrap_model_call``, donc dans le même nœud ``model`` du graphe. Comme le
modèle de production diffuse ses tokens, cet appel-là produit lui aussi des
``on_chat_model_stream`` ; ``ag_ui_langgraph`` les traduit en
``TEXT_MESSAGE_START`` / ``TEXT_MESSAGE_CONTENT`` / ``TEXT_MESSAGE_END`` **sans
distinguer l'appel** (il ne filtre que les métadonnées ``emit-messages`` /
``emit-tool-calls``, que le middleware ne pose pas). Le résumé se retrouve donc
dans la bulle de l'assistant.

**Ce que LangChain fournit pour les reconnaître** (public, stable) :

- ``metadata["lc_source"] == "summarization"`` — la source de l'appel ;
- ``metadata["lc_internal_call"]`` — jeton aléatoire par processus, marquant un
  **appel interne de middleware** (``internal_call_metadata()``). Impossibilité
  de le falsifier depuis l'entrée utilisateur, puisqu'il n'est pas connu du
  client.

**Correctif.** Un décorateur de la chaîne AG-UI (même gabarit que
``AgentFluxIdsUniques`` / ``HistoriqueArgumentsSains``) qui **retire** du flux
les événements dont l'événement brut porte l'une de ces deux marques. Rien
d'autre n'est touché : le checkpoint, l'état et le résumé lui-même restent
intacts — le résumé continue d'alimenter le contexte du modèle, il n'est
simplement plus montré à l'utilisateur.

Le test de référence (:mod:`tests.test_agui_resume_interne`) vérifie les deux
marques ; la preuve du mécanisme a été faite sur un graphe LangGraph réel
(``create_agent`` + ``SummarizationMiddleware``) : l'appel de résumé émet bien
des ``on_chat_model_stream`` avec ``lc_source="summarization"`` **avant** ceux
de la réponse.
"""

from __future__ import annotations

from typing import Any

#: Marque d'un appel de modèle interne à un middleware (jeton aléatoire).
CLE_APPEL_INTERNE = "lc_internal_call"

#: Marque de source ; ``summarization`` désigne le middleware de résumé.
CLE_SOURCE = "lc_source"
SOURCE_RESUME = "summarization"


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


def est_appel_interne(evenement: Any) -> bool:
    """Vrai si l'événement AG-UI provient d'un appel de modèle interne.

    L'événement brut (``raw_event``) est la seule source fiable : les
    métadonnées du run interne y sont attachées par LangChain.
    """
    brut = _valeur(evenement, "rawEvent", "raw_event")
    meta = _valeur(brut, "metadata") or {}
    if _valeur(meta, CLE_APPEL_INTERNE) is not None:
        return True
    return _valeur(meta, CLE_SOURCE) == SOURCE_RESUME


class ResumeInterneMasque:
    """Décorateur : le flux AG-UI ne montre aucun appel interne de middleware.

    Monté dans la chaîne AG-UI (``app/api/agui.py``) au plus près de l'agent
    ``ag-ui-langgraph`` : les événements sont filtrés avant d'être relayés aux
    autres couches (ids, erreurs, historique).
    """

    def __init__(self, interne: Any) -> None:
        self._interne = interne

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._interne, nom)

    def clone(self) -> ResumeInterneMasque:
        """Un clone par requête : état isolé, même chaîne de décorateurs."""
        cloner = getattr(self._interne, "clone", None)
        interne = cloner() if callable(cloner) else self._interne
        return ResumeInterneMasque(interne)

    async def run(self, input_data: Any):  # type: ignore[no-untyped-def]
        """Relaye le flux en retirant les événements des appels internes."""
        async for evenement in self._interne.run(input_data):
            if est_appel_interne(evenement):
                continue
            yield evenement


__all__ = [
    "CLE_APPEL_INTERNE",
    "CLE_SOURCE",
    "SOURCE_RESUME",
    "ResumeInterneMasque",
    "est_appel_interne",
]
