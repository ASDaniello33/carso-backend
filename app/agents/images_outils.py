"""Livraison au modèle des images rendues par un outil.

**Constat de recette (09/11).** Le modèle configuré voit réellement les
images : sur une image blanche portant le mot ``ZORBLAK-7742``, il répond le mot
exact 3 fois sur 3 dans un message **utilisateur**. Mais le même contenu placé
dans un message **d'outil** (``role: "tool"``, contenu ``[text, image_url]``) est
ignoré : « Je ne vois aucune image jointe », 3 fois sur 3 — y compris en
interrogeant la passerelle en HTTP brut, hors de l'application. La limitation
n'est donc pas celle de CARSO, c'est celle de l'API compatible OpenAI : une
image ne voyage de façon fiable que dans un message **utilisateur**.

**Conséquence pour ``lire_image``.** Le tool rend son image en contenu
multimodal (``app/tools/base.py``) : le ``ToolMessage`` porte la fiche en texte
et l'image en bloc ``image_url``. Sans ce module, l'image est perdue en route —
l'outil annonce ``vision_activee: true``, et le modèle, qui ne voit rien,
**décrit de mémoire**. C'est exactement le mensonge que l'INC-144 avait mis au
jour (un carré rouge décrit « Inconnue »).

**Correctif.** Une fonction pure : le ``ToolMessage`` garde sa fiche en texte
(rien n'est retiré du résultat, l'audit reste complet) et les blocs d'image sont
replacés dans un message **utilisateur** immédiatement après, avec une légende
qui dit de quel outil vient l'image. Le modèle reçoit donc l'image dans la seule
forme qu'il lit réellement.

**Ce qui n'est pas touché.** Aucun message sans image ne change (l'appelant
compare par identité) ; l'ordre des appels et de leurs réponses reste celui
qu'impose la grammaire ``tool_calls`` / ``tool`` ; le contenu métier du résultat
n'est jamais altéré, seulement déplacé du multimodal vers le texte + l'image.

**Coût.** Le message utilisateur reste dans l'historique : l'image est donc
rejouée aux tours suivants tant que l'historique la porte. C'est ce qui rend la
réponse possible ; la réduction de taille (``app/documents/images.py``, 1 600 px)
borne le volume, et la troncature d'historique de Deep Agents s'applique comme
pour le reste.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

try:  # pragma: no cover - dépend de l'extra agents
    from langchain.agents.middleware.types import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover - extra agents absent
    _AgentMiddleware = object  # type: ignore[misc,assignment]

try:  # pragma: no cover - dépend de l'extra agents
    from langchain_core.messages import HumanMessage, ToolMessage
except ImportError:  # pragma: no cover - extra agents absent
    HumanMessage = None  # type: ignore[assignment,misc]
    ToolMessage = None  # type: ignore[assignment,misc]

#: Types de blocs qui portent une image dans un contenu multimodal.
TYPES_IMAGE: frozenset[str] = frozenset({"image_url", "image"})

#: Texte de remplacement si un résultat d'outil ne contenait **que** des images
#: (le ``ToolMessage`` ne doit jamais partir vide).
TEXTE_SANS_FICHE = "Ce résultat d'outil est une image : voir l'image jointe ci-dessous."


def _est_image(bloc: Any) -> bool:
    return isinstance(bloc, dict) and bloc.get("type") in TYPES_IMAGE


def _texte_des_blocs(blocs: Sequence[Any]) -> str:
    """Texte d'une liste de blocs, sans les images."""
    textes: list[str] = []
    for bloc in blocs:
        if isinstance(bloc, str):
            textes.append(bloc)
            continue
        if not isinstance(bloc, dict):
            continue
        valeur = bloc.get("text")
        if isinstance(valeur, str):
            textes.append(valeur)
    return "".join(textes)


def _legende(message: Any, nombre: int) -> str:
    """Légende du message utilisateur porteur d'images d'outil."""
    nom = getattr(message, "name", None)
    origine = f"l'outil `{nom}`" if isinstance(nom, str) and nom else "un outil"
    if nombre > 1:
        return f"{nombre} images rendues par {origine} : observer le document réel."
    return f"Image rendue par {origine} : observer le document réel."


def deplacer_images_outils(messages: Sequence[Any]) -> list[Any]:
    """Séquence où les images d'outil vivent dans un message utilisateur.

    Chaque ``ToolMessage`` multimodal est scindé en deux messages adjacents :

    1. le ``ToolMessage`` d'origine, contenu textuel intact (fiche, message,
       avertissements) — la grammaire ``tool_calls`` / ``tool`` est préservée ;
    2. un ``HumanMessage`` portant les blocs d'image et une légende qui nomme
       l'outil d'origine.

    Returns:
        Une nouvelle liste ; ses éléments sont **les mêmes objets** que ceux de
        l'entrée quand aucune image d'outil n'est à déplacer, ce qui permet à
        l'appelant de ne rien réécrire.
    """
    if HumanMessage is None or ToolMessage is None:  # pragma: no cover - extra absent
        return list(messages)

    resultat: list[Any] = []
    for message in messages:
        contenu = getattr(message, "content", None) if isinstance(message, ToolMessage) else None
        if not isinstance(contenu, list):
            resultat.append(message)
            continue
        images = [bloc for bloc in contenu if _est_image(bloc)]
        if not images:
            resultat.append(message)
            continue

        fiche = _texte_des_blocs(contenu)
        resultat.append(
            message.model_copy(update={"content": fiche or TEXTE_SANS_FICHE}),
        )
        resultat.append(
            HumanMessage(
                content=[
                    {"type": "text", "text": _legende(message, len(images))},
                    *images,
                ]
            )
        )
    return resultat


def _inchange(avant: Sequence[Any], apres: Sequence[Any]) -> bool:
    return len(avant) == len(apres) and all(a is b for a, b in zip(avant, apres, strict=True))


class ImagesOutilsMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Dernier passage avant le modèle : les images d'outil sont lisibles.

    Placé **après** ``HistoriqueOutilsCoherentMiddleware`` dans la pile du
    harnais, donc en dernier avant le fournisseur : l'historique assaini est
    déjà conforme quand les images sont déplacées.
    """

    @property
    def name(self) -> str:
        return "ImagesOutilsMiddleware"

    @staticmethod
    def _deplacer(request: Any) -> Any:
        messages = getattr(request, "messages", None)
        if not messages:
            return request
        propre = deplacer_images_outils(messages)
        if _inchange(messages, propre):
            return request
        return request.override(messages=propre)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._deplacer(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._deplacer(request))


__all__ = [
    "TEXTE_SANS_FICHE",
    "TYPES_IMAGE",
    "ImagesOutilsMiddleware",
    "deplacer_images_outils",
]
