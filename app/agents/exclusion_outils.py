"""Exclusion des outils intégrés de Deep Agents que CARSO n'expose pas.

``grep`` est fourni par le ``FilesystemMiddleware`` de Deep Agents : il
n'appartient pas au contrat CARSO. La recherche dans un document métier passe
par ``chercher_dans_document`` (jamais par un balayage de fichier), celle dans
les fichiers de travail par ``glob`` + ``read_file``.

**Pourquoi un middleware maison plutôt qu'un ``HarnessProfile``.**
``create_deep_agent`` ne résout un profil de harnais que si le modèle est passé
**sous forme de chaîne** (``_model_spec = model if isinstance(model, str) else
None``) ; le harnais CARSO construit l'instance dans ``app/agents/providers.py``,
le profil ne serait donc jamais appliqué. Ce middleware fait le même filtrage —
retirer l'outil de la requête modèle et refuser un appel résiduel — dans notre
propre pile, après les middlewares qui ajoutent des outils.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - dépend de l'extra agents
    from langchain.agents.middleware.types import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover - extra agents absent
    _AgentMiddleware = object  # type: ignore[misc,assignment]

#: Outils intégrés retirés à **tous** les agents CARSO.
#:
#: ``grep`` : remplacé par ``chercher_dans_document`` côté documents et par
#: ``glob`` + ``read_file`` côté fichiers de travail (voir le bloc de prompt
#: ``documents_et_fichiers``).
OUTILS_EXCLUS: frozenset[str] = frozenset({"grep"})


def _nom_outil(outil: Any) -> str | None:
    """Nom d'un outil LangChain, ou d'une définition ``dict`` du catalogue."""
    if isinstance(outil, dict):
        nom = outil.get("name")
        return nom if isinstance(nom, str) else None
    nom = getattr(outil, "name", None)
    return nom if isinstance(nom, str) else None


class ExclusionOutilsMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Retire des outils du contrat **effectif** envoyé au modèle.

    La visibilité fait l'exécution : un outil absent de la requête n'est jamais
    appelé. Le filtre porte sur ``request.tools``, donc sur les outils injectés
    par les middlewares précédents (fichiers Deep Agents compris).
    """

    def __init__(self, exclusions: frozenset[str] = OUTILS_EXCLUS) -> None:
        self._exclusions = exclusions

    @property
    def name(self) -> str:
        return "ExclusionOutilsMiddleware"

    def _filtrer(self, request: Any) -> Any:
        """Requête sans les outils exclus (inchangée s'il n'y a rien à retirer)."""
        outils = list(getattr(request, "tools", None) or [])
        if not outils:
            return request
        gardes = [outil for outil in outils if _nom_outil(outil) not in self._exclusions]
        if len(gardes) == len(outils):
            return request
        return request.override(tools=gardes)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._filtrer(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._filtrer(request))


__all__ = ["OUTILS_EXCLUS", "ExclusionOutilsMiddleware"]
