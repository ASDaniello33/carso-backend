"""Client multi-moteurs ``ddgs`` — le repli de la recherche média.

``ddgs`` interroge des moteurs classiques (Bing, Yahoo, Google…) avec une
empreinte de navigateur (``primp``) : c'est précisément ce qu'un blocage par
empreinte TLS contourne, là où un client ``httpx`` ordinaire échoue depuis un
hébergeur.

Les moteurs sont appelés **un par un**, jamais via ``backend="auto"`` : le
mécanisme interne de la bibliothèque peut perdre les résultats déjà obtenus
quand un moteur échoue (issue ddgs #427 ouverte le 11/03/2026, PR #489 non
fusionnée au 18/10/2026). La chaîne de fournisseurs de CARSO est elle-même la
reprise : chaque moteur est intercepté, ses échecs sont conservés pour le dire,
et le premier moteur qui répond gagne.

Moteurs retenus par nature (mesuré le 18/10/2026, ddgs 9.16.0, depuis un poste
de développement — à confirmer depuis l'hébergeur) :

- **images** : ``bing`` (images web actuelles), puis ``duckduckgo`` ;
- **vidéos** : ``duckduckgo`` (seule verticale vidéo de la bibliothèque) ;
- **documents** : ``yahoo`` (seul moteur qui a honoré ``filetype:`` à l'essai),
  puis ``google`` et ``brave``.

Contrat : une fonction rend les résultats **déjà normalisés** (les formes des
tools ``rechercher_*``) ou lève ``ValidationError`` nommant chaque moteur
interrogé — jamais une exception de la bibliothèque, qui traverserait le tool et
tuerait le run AG-UI.

La bibliothèque gère son propre HTTP : le ``httpx.Client`` injecté dans les
tools ne s'applique pas ici. Les tests remplacent ``_DDGS_REEL``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.errors import ValidationError

#: La bibliothèque ``ddgs``, ou ``None`` si l'extra « web » n'est pas installé.
#: Annotée « appelable » : on n'instancie la classe que via ``_client``, jamais
#: directement — le refus explicite du paquet absent reste en un seul endroit.
_DDGS_REEL: Callable[..., Any] | None
try:
    from ddgs import DDGS as _bibliotheque

    _DDGS_REEL = _bibliotheque
except ImportError:  # pragma: no cover - dépend de l'environnement
    _DDGS_REEL = None

__all__ = ["images", "pages", "videos"]

#: Délai par moteur : un moteur lent ne doit pas consommer la patience du run
#: AG-UI ; c'est un repli, pas le chemin principal.
_DELAI = 10
#: Résultats francophones : CARSO travaille en français (Maroc, France).
_REGION = "fr-fr"

#: Ordre = préférence. ``bing`` est la seule verticale d'images non DuckDuckGo
#: de la bibliothèque, et la seule qui répondait à l'essai.
_MOTEURS_IMAGES = ("bing", "duckduckgo")
#: Une seule verticale vidéo : ``duckduckgo`` — le même moteur que le
#: fournisseur principal, mais vu avec l'empreinte navigateur de ``ddgs``.
_MOTEURS_VIDEOS = ("duckduckgo",)
#: ``yahoo`` est le seul moteur qui a rendu des documents pour ``filetype:``
#: à l'essai ; les deux suivants, tentés au cas où l'hébergeur les joint mieux.
_MOTEURS_DOCUMENTS = ("yahoo", "google", "brave")

#: Un moteur ddgs rend ses résultats sous forme de dicts ; le transformateur les
#: ramène à la forme du tool, et rend ``None`` pour un item inexploitable.
_Transformateur = Callable[[dict[str, Any]], dict[str, Any] | None]


def _client() -> Any:
    """Un client ddgs, ou le refus explicite si l'extra « web » est absent."""
    if _DDGS_REEL is None:
        msg = (
            "ddgs indisponible : le paquet ddgs n'est pas installé (extra "
            "« web » du backend) — le repli multi-moteurs ne peut pas répondre"
        )
        raise ValidationError(msg)
    return _DDGS_REEL(timeout=_DELAI)


def _rechercher(
    methode: str,
    moteurs: tuple[str, ...],
    requete: str,
    nb: int,
    transformer: _Transformateur,
) -> list[dict[str, Any]]:
    """Interroge les moteurs **un par un**, jusqu'au premier qui répond.

    Les échecs de chaque moteur sont conservés : si tous échouent, l'erreur les
    nomme — un ``RatelimitException`` de Bing ne ressemble pas à une absence de
    résultat, et l'agent doit pouvoir dire la différence.

    Note : l'exception est rattrapée **au sens large** car la bibliothèque peut
    laisser fuiter des erreurs de son client HTTP ; elle est toujours convertie
    en constat nommé, jamais avalée en silence.
    """
    constats: list[str] = []
    for moteur in moteurs:
        try:
            resultats = getattr(_client(), methode)(
                requete, backend=moteur, max_results=nb, region=_REGION
            )
        except ValidationError:
            # Un refus de notre part (paquet absent) vaut pour tous les moteurs :
            # inutile de répéter trois fois le même constat.
            raise
        except Exception as erreur:  # noqa: BLE001 - cf. note ci-dessus
            constats.append(f"{moteur} ({type(erreur).__name__})")
            continue
        if resultats:
            retenus = [
                transforme for item in resultats[:nb] if (transforme := transformer(item))
            ]
            if retenus:
                return retenus
        constats.append(f"{moteur} (aucun résultat)")
    msg = "ddgs : aucun moteur n'a répondu — " + " ; ".join(constats)
    raise ValidationError(msg, details={"moteurs": list(moteurs)})


def _image(item: dict[str, Any]) -> dict[str, Any] | None:
    """Résultat d'image : page d'origine, **fichier**, vignette, dimensions."""
    fichier = str(item.get("image") or "")
    if not fichier:
        return None
    return {
        "titre": str(item.get("title") or ""),
        "url": str(item.get("url") or fichier),
        "image": fichier,
        "vignette": str(item.get("thumbnail") or fichier),
        "largeur": item.get("width"),
        "hauteur": item.get("height"),
        "source": str(item.get("source") or ""),
        "licence": "",
    }


def _video(item: dict[str, Any]) -> dict[str, Any] | None:
    """Résultat de vidéo : titre, page de lecture, vignette, durée, auteur."""
    url = str(item.get("content") or item.get("embed_url") or "")
    if not url:
        return None
    return {
        "titre": str(item.get("title") or ""),
        "url": url,
        "vignette": str(item.get("image") or item.get("thumbnail") or ""),
        "duree": str(item.get("duration") or ""),
        "auteur": str(item.get("uploader") or item.get("publisher") or ""),
        "date": str(item.get("published") or ""),
    }


def _page(item: dict[str, Any]) -> dict[str, Any] | None:
    """Résultat textuel : titre et URL — le filtrage par nature reste ailleurs."""
    url = str(item.get("href") or "")
    if not url:
        return None
    return {"titre": str(item.get("title") or ""), "url": url}


def images(requete: str, nb: int) -> list[dict[str, Any]]:
    """Images web actuelles (Bing d'abord)."""
    return _rechercher("images", _MOTEURS_IMAGES, requete, nb, _image)


def videos(requete: str, nb: int) -> list[dict[str, Any]]:
    """Vidéos (verticale DuckDuckGo vue avec l'empreinte navigateur)."""
    return _rechercher("videos", _MOTEURS_VIDEOS, requete, nb, _video)


def pages(requete: str, nb: int) -> list[dict[str, Any]]:
    """Pages web (Yahoo d'abord) — pour la recherche de documents."""
    return _rechercher("text", _MOTEURS_DOCUMENTS, requete, nb, _page)
