"""Recherche de **vidéos** — ``rechercher_video``, YouTube, sans clé.

Pourquoi un tool à part alors qu'une image ou un document se cherchent avec la
recherche web classique : une vidéo se trouve chez un hébergeur, pas dans un
index de pages. La recherche web rendrait la page de lecture *et rien de plus* ;
ici la réponse porte ce qui permet de choisir — durée, chaîne, date, vignette —
et l'interface lit la vidéo chez l'hébergeur (iframe), sans rien télécharger.

Le fournisseur est ``moteurs_web.videos`` (page de résultats YouTube, empreinte
de navigateur) : le même client que la recherche web, donc le même comportement
depuis un hébergeur.

Un échec **réel** est une erreur métier explicite : une exception de transport
brute traverserait le tool et interromprait tout le run AG-UI (``app/api/agui.py``
ne rattrape que pour dire) au lieu d'être dite à l'agent.

Une recherche identique (même requête, même nombre de résultats) est mémorisée
**30 minutes** en mémoire (``app/tools/cache_ttl``) : la répéter ne repart pas
chez YouTube. Un échec n'est jamais mémorisé.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.tools import moteurs_web
from app.tools.cache_ttl import CacheTTL
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = [
    "CACHE_RECHERCHE_VIDEO",
    "RechercheVideoInput",
    "attacher_recherche_video",
    "build_recherche_video_tool",
]

_CARACTERES_MAX_REQUETE = 300
_MAX_RESULTATS = 10

#: Recherches vidéo mémorisées 30 minutes, partagées par tous les agents.
CACHE_RECHERCHE_VIDEO: CacheTTL[dict[str, Any]] = CacheTTL()


class RechercheVideoInput(BaseModel):
    """Entrée typée de ``rechercher_video``."""

    requete: str = Field(
        min_length=2,
        max_length=_CARACTERES_MAX_REQUETE,
        description="Requête en langage naturel (mots-clés, pas une URL).",
    )
    max_resultats: int | None = Field(
        default=None,
        ge=1,
        le=_MAX_RESULTATS,
        description="Nombre de résultats (serre la borne de configuration, jamais l'inverse).",
    )


def _borne(payload: dict[str, Any], plafond: int) -> int:
    """Nombre de résultats demandés : borné par la configuration, jamais l'inverse."""
    demande = payload.get("max_resultats") or plafond
    return max(1, min(int(demande), plafond, _MAX_RESULTATS))


def build_recherche_video_tool(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
) -> Any:
    """Construit le ``TypedTool`` ``rechercher_video`` pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — la capacité ``web_search`` est exigée :
            chercher une vidéo sur le web ne s'ouvre pas à un agent qui n'a pas
            déjà le droit d'y chercher du texte.
        settings: ``Settings`` (borne ``web_search_max_results``).

    Returns:
        Un ``TypedTool`` nommé ``rechercher_video``.
    """
    plafond = int(getattr(settings, "web_search_max_results", 5) or 5)

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        # La permission est vérifiée **avant** le cache : un agent sans
        # ``web_search`` ne doit pas récupérer le résultat d'un autre.
        policy.require(identite.agent_id, "web_search")
        requete = payload["requete"]
        nb = _borne(payload, plafond)

        cle_cache = (requete, nb)
        en_cache = CACHE_RECHERCHE_VIDEO.obtenir(cle_cache)
        if en_cache is not None:
            return en_cache

        resultats = moteurs_web.videos(requete, nb)
        reponse = {
            "requete": requete,
            "nb_resultats": len(resultats),
            "resultats": resultats,
            "sources_utilisees": ["youtube"],
            "avertissements": [],
        }
        # Seul un résultat réussi est mémorisé (une panne se retente).
        CACHE_RECHERCHE_VIDEO.memoriser(cle_cache, reponse)
        return reponse

    from app.tools.base import TypedTool

    return TypedTool(
        name="rechercher_video",
        description=(
            "Recherche des vidéos sur le web (YouTube) et rend titre, page de "
            "lecture (url), vignette, durée, chaîne et date. Lecture seule : aucun "
            "média n'est téléchargé ; les résultats sont des liens externes à "
            "ouvrir et à vérifier. Pour un document ou une image, utilise "
            "langsearch_web_search avec l'opérateur filetype:."
        ),
        handler=_handler,
        input_schema=RechercheVideoInput,
        tags=("web", "recherche", "video"),
    )


def attacher_recherche_video(
    definition: Any,
    extra: tuple[Any, ...],
    *,
    settings: Any | None = None,
) -> tuple[Any, ...]:
    """Ajoute ``rechercher_video`` si le contrat de l'agent le déclare.

    Construction paresseuse comme pour la recherche web : le tool existe dès
    qu'il est déclaré, et le réseau n'est touché qu'à l'invocation.
    """
    if "rechercher_video" not in getattr(definition, "tools", ()):
        return extra
    from app.core.config import get_settings

    return extra + (
        build_recherche_video_tool(
            identite=AgentIdentity(agent_id=definition.agent_id),
            policy=definition.policy,
            settings=settings or get_settings(),
        ),
    )
