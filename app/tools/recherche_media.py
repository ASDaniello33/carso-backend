"""Recherche média sur le web — images, vidéos, documents.

Trois tools en **lecture pure** pour trouver des données particulières hors de
CARSO — une image, une vidéo, un document (PDF, tableur, présentation…). Ils
complètent ``langsearch_web_search`` (texte) : ici, la réponse est une liste de
ressources localisables, jamais un contenu recopié.

Ce que ces tools ne font pas :

- **aucun téléchargement** de fichier et **aucun scraping** de page — ils rendent
  l'URL et la vignette, la décision d'ouvrir appartient à l'utilisateur ;
- **aucun secret** — les deux fournisseurs de repli sont publics et sans clé ;
- **aucune invention** — un résultat vide est rendu vide, jamais complété.

Deux fournisseurs au plus par nature, essayés **dans l'ordre** :

=================  ============================================  ============================
nature             fournisseur principal                         repli
=================  ============================================  ============================
image              DuckDuckGo (verticale ``/i.js``)              ddgs (Bing), puis Openverse
vidéo              DuckDuckGo (verticale ``/v.js``)              ddgs (verticale vidéo)
document           DuckDuckGo (page de résultats, ``filetype:``)  ddgs (Yahoo, Google, Brave)
=================  ============================================  ============================

Pourquoi un repli : DuckDuckGo ne tient pas la charge depuis un hébergeur. Ses
points d'entrée publics répondent en ``ConnectTimeout`` ou en « 202 » (accusé de
réception, corps vide) aux adresses de datacenters — mesuré le 18/10/2026 depuis
la production Render **et** depuis un poste de développement soumis à des appels
répétés.

Le repli ``ddgs`` rend des **résultats web actuels** : la bibliothèque interroge
Bing, Yahoo, Google… avec une empreinte de navigateur, ce qu'un client ``httpx``
ordinaire ne sait pas faire, et ses moteurs honorent ``filetype:`` (Yahoo a rendu
des PDF récents à l'essai). Ses moteurs sont appelés **un par un** — jamais
``backend="auto"``, qui peut perdre les résultats déjà obtenus quand un moteur
tombe (issue ddgs #427). Openverse, lui, sert des images sous licence libre
(la licence est rendue avec l'image).

La chaîne est la reprise : un fournisseur muet est constaté une fois (délai de
connexion court, pas de nouvelle tentative réseau), le suivant répond, et la
provenance est dite (``sources_utilisees``, ``avertissements``).

Un échec **réel** — aucun fournisseur n'a répondu — est une erreur métier
explicite, fournisseur par fournisseur : une exception brute (``httpx``,
``JSONDecodeError``) traverserait le tool et interromprait **tout le run AG-UI**
(``app/api/agui.py`` ne rattrape que pour dire) au lieu d'être dite à l'agent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from app.core.errors import ValidationError
from app.tools import fournisseurs_ddgs
from app.tools.fournisseurs_recherche import (
    DELAI_RECHERCHE,
    EN_TETES,
    envoyer,
    pages_duckduckgo,
)
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = [
    "EXTENSIONS_DOCUMENT",
    "RechercheDocumentInput",
    "RechercheImageInput",
    "RechercheVideoInput",
    "attacher_recherche_media",
    "build_recherche_media_tool",
]

#: Points d'entrée DuckDuckGo. ``/i.js`` et ``/v.js`` sont les verticales que la
#: page publique interroge elle-même ; le jeton ``vqd`` est lu sur la page HTML.
_PAGE = "https://duckduckgo.com/"
_CHEMIN_IMAGES = "/i.js"
_CHEMIN_VIDEOS = "/v.js"

#: API d'images libres (Creative Commons et domaine public), sans clé.
_OPENVERSE = "https://api.openverse.org/v1/images/"

_TIMEOUT_HTTP = DELAI_RECHERCHE
_CARACTERES_MAX_REQUETE = 300
_MAX_RESULTATS = 10
#: Fenêtre brute lue chez un fournisseur qui **filtre** ensuite : il faut de la
#: marge pour rendre ``nb`` documents après écartement.
_MARGE_FILTRE = 3

#: DuckDuckGo ne refuse pas toujours franchement : une rafale reçoit un « 202 »
#: (accusé de réception, corps vide) avant d'être servie. Deux essais pour ce
#: refus temporaire ; un échec **réseau**, lui, n'est pas rejoué — le fournisseur
#: suivant est la reprise.
_TENTATIVES = 2

#: Extensions d'un **fichier média direct** : ce qu'un lecteur HTML5 peut lire
#: dans l'interface (``<video src>``). Hors de cette liste, seul le lien vers la
#: page de lecture est présenté — le lecteur embarqué ne marchande pas.
_EXTENSIONS_MEDIA = (".mp4", ".m4v", ".webm", ".ogv", ".ogg", ".mov", ".m3u8")

#: Extensions proposées par défaut pour la recherche documentaire.
EXTENSIONS_DOCUMENT = (
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "odt",
    "ods",
    "csv",
)

#: Le jeton ``vqd`` apparaît en JavaScript (`vqd="4-123…"`) et dans les URL
#: d'appel (`vqd=4-123…&`) : les deux formes sont acceptées.
_VQD = re.compile(r"""vqd=(?:"|')?([\d-]+)""")


class _RechercheBase(BaseModel):
    """Requête commune aux trois recherches média."""

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


class RechercheImageInput(_RechercheBase):
    """Entrée typée de ``rechercher_image``."""


class RechercheVideoInput(_RechercheBase):
    """Entrée typée de ``rechercher_video``."""


class RechercheDocumentInput(_RechercheBase):
    """Entrée typée de ``rechercher_document``."""

    extensions: list[str] | None = Field(
        default=None,
        description=(
            "Types de fichiers visés (pdf, docx, xlsx, pptx, csv…). Par défaut : "
            "tous ceux que le service propose."
        ),
    )


#: Un fournisseur rend les résultats de **sa** nature, déjà normalisés.
_Fournisseur = Callable[[httpx.Client, dict[str, Any], int], list[dict[str, Any]]]


def _borne(payload: dict[str, Any], plafond: int) -> int:
    """Nombre de résultats demandés : borné par la configuration, jamais l'inverse."""
    demande = payload.get("max_resultats") or plafond
    return max(1, min(int(demande), plafond, _MAX_RESULTATS))


def _chemin(url: str) -> str:
    """Chemin d'une URL, sans paramètres ni fragment, en minuscules."""
    return url.split("?")[0].split("#")[0].lower()


def _media_direct(url: Any) -> str:
    """URL d'un **fichier** média, ou chaîne vide si ce n'en est pas un.

    Le moteur rend parfois l'adresse du fichier lui-même (un ``.mp4``) dans
    ``content``, parfois celle d'une page de lecture. Seule la première se lit
    dans l'interface : annoncer l'autre comme un média ferait échouer le lecteur
    au lieu d'ouvrir la page.
    """
    if not isinstance(url, str):
        return ""
    return url if _chemin(url).endswith(_EXTENSIONS_MEDIA) else ""


def _domaine(url: str) -> str:
    """Hôte d'une URL, sans ``www.`` (chaîne vide si l'URL est inexploitable)."""
    try:
        return urlsplit(url).netloc.lower().removeprefix("www.")
    except ValueError:  # pragma: no cover - urlsplit ne lève presque jamais
        return ""


def _extension_autorisee(url: str, extensions: tuple[str, ...]) -> bool:
    """Le lien vise-t-il vraiment un fichier du type demandé ?"""
    return _chemin(url).endswith(extensions)


# --- DuckDuckGo (fournisseur principal) --------------------------------------


def _jeton_vqd(client: httpx.Client, requete: str, verticale: str) -> str:
    """Jeton ``vqd`` exigé par les verticales JSON de DuckDuckGo.

    Il est publié dans la page HTML de recherche ; sans lui, ``/i.js`` répond une
    erreur. Un jeton illisible est une erreur **explicite** : mieux vaut le dire
    que renvoyer une liste vide qui ferait croire à une absence de résultat.
    """
    reponse = envoyer(
        client,
        "GET",
        _PAGE,
        cible="DuckDuckGo",
        params={"q": requete, "iax": verticale, "ia": verticale},
        timeout=_TIMEOUT_HTTP,
        tentatives=_TENTATIVES,
        reessayer_reseau=False,
    )
    if reponse.status_code != 200:
        msg = f"DuckDuckGo a répondu {reponse.status_code} (recherche {verticale} refusée)"
        raise ValidationError(msg, details={"status": reponse.status_code})
    trouve = _VQD.search(reponse.text)
    if not trouve:
        msg = (
            "DuckDuckGo n'a pas publié de jeton de recherche (vqd) : "
            "la recherche média est indisponible, aucun résultat n'est inventé"
        )
        raise ValidationError(msg)
    return trouve.group(1)


def _verticale(
    client: httpx.Client,
    *,
    requete: str,
    verticale: str,
    chemin: str,
    nb: int,
) -> list[dict[str, Any]]:
    """Résultats bruts d'une verticale JSON (images ou vidéos), bornés."""
    jeton = _jeton_vqd(client, requete, verticale)
    reponse = envoyer(
        client,
        "GET",
        f"{_PAGE.rstrip('/')}{chemin}",
        cible="DuckDuckGo",
        params={"l": "fr-fr", "o": "json", "q": requete, "vqd": jeton},
        timeout=_TIMEOUT_HTTP,
        tentatives=_TENTATIVES,
        reessayer_reseau=False,
    )
    if reponse.status_code != 200:
        msg = f"DuckDuckGo a répondu {reponse.status_code} (recherche {verticale} refusée)"
        raise ValidationError(msg, details={"status": reponse.status_code})
    try:
        donnees = reponse.json()
    except ValueError as erreur:
        msg = (
            "DuckDuckGo a répondu un corps illisible (JSON attendu) : la recherche "
            "est abandonnée, aucun résultat n'est inventé"
        )
        raise ValidationError(msg) from erreur
    return list(donnees.get("results") or [])[:nb]


def _images_duckduckgo(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Images : titre, page d'origine, **fichier image**, vignette, dimensions.

    ``url`` reste la page d'origine (le contexte du document) ; ``image`` est le
    fichier lui-même, seul affichable en grand.
    """
    return [
        {
            "titre": item.get("title") or "",
            "url": item.get("url") or item.get("image") or "",
            "image": item.get("image") or item.get("thumbnail") or "",
            "vignette": item.get("thumbnail") or item.get("image") or "",
            "largeur": item.get("width"),
            "hauteur": item.get("height"),
            "source": item.get("source") or "",
            "licence": "",
        }
        for item in _verticale(
            client,
            requete=payload["requete"],
            verticale="images",
            chemin=_CHEMIN_IMAGES,
            nb=nb,
        )
    ]


def _videos_duckduckgo(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Vidéos : titre, page de lecture, **fichier vidéo** s'il est direct, durée, auteur.

    ``url`` est la page de lecture (là où l'utilisateur retrouve la vidéo chez
    son hébergeur) ; ``media`` n'est rempli que si le moteur a rendu l'adresse du
    fichier lui-même — c'est la seule qui se joue dans l'interface.
    """
    return [
        {
            "titre": item.get("title") or "",
            "url": item.get("url") or item.get("content") or "",
            "media": _media_direct(item.get("content")),
            "vignette": (item.get("images") or {}).get("medium") or item.get("image") or "",
            "duree": item.get("duration") or "",
            "auteur": item.get("uploader") or "",
            "date": item.get("published") or "",
        }
        for item in _verticale(
            client,
            requete=payload["requete"],
            verticale="videos",
            chemin=_CHEMIN_VIDEOS,
            nb=nb,
        )
    ]


def _extensions_demandees(payload: dict[str, Any]) -> tuple[str, ...]:
    """Extensions visées par une recherche documentaire (jamais vide)."""
    demandees = tuple(
        str(extension).strip().lstrip(".").lower()
        for extension in (payload.get("extensions") or EXTENSIONS_DOCUMENT)
        if str(extension).strip()
    )
    return demandees or EXTENSIONS_DOCUMENT


def _document(titre: str, url: str) -> dict[str, Any]:
    """Un document retenu : titre, URL du **fichier**, type et domaine."""
    return {
        "titre": titre,
        "url": url,
        "type_fichier": _chemin(url).rsplit(".", 1)[-1],
        "domaine": _domaine(url),
    }


def _documents(
    pages: list[dict[str, Any]], nb: int, extensions: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Documents retenus : seuls les liens qui pointent vers un fichier visé.

    Le moteur peut ignorer ``filetype:`` : chaque lien est donc **vérifié**, et
    un résultat qui ne mène pas à l'extension demandée est écarté plutôt que
    présenté comme un document.
    """
    visees = tuple(f".{extension}" for extension in extensions)
    resultats: list[dict[str, Any]] = []
    vues: set[str] = set()
    for item in pages:
        url = item["url"]
        if url in vues or not _extension_autorisee(url, visees):
            continue
        vues.add(url)
        resultats.append(_document(item["titre"], url))
        if len(resultats) >= nb:
            break
    return resultats


def _documents_duckduckgo(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Documents : la page de résultats est la seule à honorer ``filetype:``."""
    extensions = _extensions_demandees(payload)
    operateurs = " OR ".join(f"filetype:{extension}" for extension in extensions)
    pages = pages_duckduckgo(
        client,
        requete=f"{payload['requete']} ({operateurs})",
        nb=nb * _MARGE_FILTRE,
        timeout=_TIMEOUT_HTTP,
        tentatives=_TENTATIVES,
        reessayer_reseau=False,
    )
    return _documents(pages, nb, extensions)


# --- Openverse (repli images) ------------------------------------------------


def _json(reponse: httpx.Response, cible: str) -> dict[str, Any]:
    """Corps JSON d'une réponse, ou refus explicite s'il est illisible.

    Un 200 non-JSON (page d'erreur, corps tronqué) ne doit pas sortir en
    ``JSONDecodeError`` : ce serait un run AG-UI interrompu au lieu d'une erreur
    dite à l'agent.
    """
    try:
        return json.loads(reponse.text)
    except ValueError as erreur:
        msg = (
            f"{cible} a répondu un corps illisible (JSON attendu) : la recherche "
            "est abandonnée, aucun résultat n'est inventé"
        )
        raise ValidationError(msg) from erreur


def _images_openverse(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Images libres (Creative Commons, domaine public) — le repli.

    La licence est rendue avec l'image : une illustration reprise dans un document
    CARSO n'a pas les mêmes obligations selon qu'elle est CC0 ou CC BY-NC.
    """
    reponse = envoyer(
        client,
        "GET",
        _OPENVERSE,
        cible="Openverse",
        params={"q": payload["requete"], "page_size": nb},
        timeout=_TIMEOUT_HTTP,
        tentatives=1,
        reessayer_reseau=False,
    )
    if reponse.status_code != 200:
        msg = f"Openverse a répondu {reponse.status_code} (recherche d'images refusée)"
        raise ValidationError(msg, details={"status": reponse.status_code})
    donnees = _json(reponse, "Openverse")

    resultats: list[dict[str, Any]] = []
    for item in donnees.get("results") or []:
        fichier = item.get("url") or ""
        if not fichier:
            continue
        licence = " ".join(
            str(partie)
            for partie in (item.get("license"), item.get("license_version"))
            if partie
        )
        resultats.append(
            {
                "titre": item.get("title") or "",
                "url": item.get("foreign_landing_url") or fichier,
                "image": fichier,
                "vignette": item.get("thumbnail") or fichier,
                "largeur": item.get("width"),
                "hauteur": item.get("height"),
                "source": item.get("source") or item.get("provider") or "",
                "licence": licence,
            }
        )
        if len(resultats) >= nb:
            break
    return resultats


# --- Repli ddgs : résultats web actuels, moteur par moteur --------------------


def _images_ddgs(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Images par ddgs (Bing d'abord) — les mêmes champs que DuckDuckGo."""
    return fournisseurs_ddgs.images(payload["requete"], nb)


def _videos_ddgs(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Vidéos par ddgs : ``media`` n'est rempli que pour un fichier direct."""
    return [
        {**item, "media": _media_direct(item["url"])}
        for item in fournisseurs_ddgs.videos(payload["requete"], nb)
    ]


def _documents_ddgs(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> list[dict[str, Any]]:
    """Documents par ddgs — même requête ``filetype:``, même vérification des liens.

    Les moteurs peuvent ignorer l'opérateur : chaque lien est donc vérifié, et un
    résultat qui ne mène pas à l'extension demandée est écarté plutôt que
    présenté comme un document. L'opérateur est passé **sans parenthèses** — la
    forme parenthésée ne rend aucun résultat chez Yahoo (mesuré le 18/10/2026).
    """
    extensions = _extensions_demandees(payload)
    operateurs = " OR ".join(f"filetype:{extension}" for extension in extensions)
    pages = fournisseurs_ddgs.pages(
        f"{payload['requete']} {operateurs}", nb * _MARGE_FILTRE
    )
    return _documents(pages, nb, extensions)


# --- Chaîne de fournisseurs --------------------------------------------------

#: Les trois natures de ce module (aussi le vocabulaire du catalogue d'outils).
_Nature = Literal["image", "video", "document"]

#: Chaîne par nature : le premier fournisseur qui rend des résultats gagne.
_CHAINES: dict[_Nature, tuple[tuple[str, _Fournisseur], ...]] = {
    "image": (
        ("DuckDuckGo", _images_duckduckgo),
        ("ddgs", _images_ddgs),
        ("Openverse", _images_openverse),
    ),
    "video": (("DuckDuckGo", _videos_duckduckgo), ("ddgs", _videos_ddgs)),
    "document": (
        ("DuckDuckGo", _documents_duckduckgo),
        ("ddgs", _documents_ddgs),
    ),
}

#: Nature en clair, pour un message que l'agent peut répéter à l'utilisateur.
_NATURES: dict[_Nature, str] = {
    "image": "d'images",
    "video": "de vidéos",
    "document": "de documents",
}

_CANDIDATS: dict[_Nature, tuple[str, type[_RechercheBase]]] = {
    "image": ("rechercher_image", RechercheImageInput),
    "video": ("rechercher_video", RechercheVideoInput),
    "document": ("rechercher_document", RechercheDocumentInput),
}

#: Ce que chaque tool cherche, dit à l'utilisateur (jamais de promesse vague).
_DESCRIPTIONS = {
    "image": (
        "Recherche des images sur le web et rend titre, page d'origine (url), URL du "
        "fichier image (image), vignette, dimensions, source et licence quand elle est "
        "annoncée. Lecture seule : aucun fichier n'est téléchargé, aucune page n'est "
        "recopiée. Les résultats sont des liens à ouvrir et à vérifier."
    ),
    "video": (
        "Recherche des vidéos sur le web et rend titre, page de lecture (url), URL du "
        "fichier vidéo (media, vide si le résultat n'est qu'une page), vignette, "
        "durée et auteur. Lecture seule : aucun média n'est téléchargé. Les résultats "
        "sont des liens à ouvrir et à vérifier."
    ),
    "document": (
        "Recherche des documents en ligne (PDF, DOCX, XLSX, PPTX, CSV…) et rend titre, "
        "URL du fichier, type de fichier et domaine. Lecture seule : aucun fichier "
        "n'est téléchargé ni indexé. Ne remplace pas les documents CARSO — c'est une "
        "source externe à vérifier."
    ),
}


def _chercher(
    client: httpx.Client, payload: dict[str, Any], nature: _Nature, nb: int
) -> dict[str, Any]:
    """Enchaîne les fournisseurs de la nature demandée jusqu'au premier qui répond.

    Un fournisseur muet ou vide ne fait pas échouer la recherche : son échec est
    conservé pour le dire. Si **aucun** n'a répondu, l'erreur nomme chaque cause
    — jamais une liste vide qui ferait croire à une absence de résultats.
    """
    avertissements: list[str] = []
    repondu = False
    for nom, fournisseur in _CHAINES[nature]:
        try:
            resultats = fournisseur(client, payload, nb)
        except (ValidationError, httpx.HTTPError) as erreur:
            avertissements.append(str(erreur))
            continue
        repondu = True
        if resultats:
            return {
                "requete": payload["requete"],
                "nb_resultats": len(resultats),
                "resultats": resultats,
                "sources_utilisees": [nom],
                "avertissements": avertissements,
            }

    if not repondu:
        msg = (
            f"Recherche {_NATURES[nature]} impossible : aucun fournisseur n'a "
            "répondu — " + " ; ".join(avertissements)
        )
        raise ValidationError(
            msg, details={"fournisseurs": [nom for nom, _ in _CHAINES[nature]]}
        )

    return {
        "requete": payload["requete"],
        "nb_resultats": 0,
        "resultats": [],
        "sources_utilisees": [],
        "avertissements": avertissements,
    }


def build_recherche_media_tool(
    *,
    nature: _Nature,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    client: httpx.Client | None = None,
) -> Any:
    """Construit un ``TypedTool`` de recherche média pour UN agent.

    Args:
        nature: verticale cherchée (``image``, ``video`` ou ``document``).
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — la capacité ``web_search`` est exigée :
            chercher sur le web ne s'ouvre pas à un agent qui n'a pas déjà le
            droit d'y chercher du texte.
        settings: ``Settings`` (borne ``web_search_max_results``).
        client: ``httpx.Client`` injectable (tests hors réseau).

    Returns:
        Un ``TypedTool`` nommé ``rechercher_<nature>``.
    """
    nom, schema = _CANDIDATS[nature]
    plafond = int(getattr(settings, "web_search_max_results", 5) or 5)
    client_fourni = client is not None

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "web_search")
        nb = _borne(payload, plafond)
        if client_fourni and client is not None:
            return _chercher(client, payload, nature, nb)
        # Client éphémère par appel : aucun état partagé entre deux agents.
        with httpx.Client(timeout=_TIMEOUT_HTTP, headers=EN_TETES) as temporaire:
            return _chercher(temporaire, payload, nature, nb)

    from app.tools.base import TypedTool

    return TypedTool(
        name=nom,
        description=_DESCRIPTIONS[nature],
        handler=_handler,
        input_schema=schema,
        tags=("web", "recherche", nature),
    )


def attacher_recherche_media(
    definition: Any,
    extra: tuple[Any, ...],
    *,
    settings: Any | None = None,
) -> tuple[Any, ...]:
    """Ajoute les tools média que le contrat de l'agent déclare.

    Construction paresseuse comme pour la recherche texte : le tool existe dès
    qu'il est déclaré, et le réseau n'est touché qu'à l'invocation.
    """
    demandes = [
        nature for nature in _CANDIDATS if _CANDIDATS[nature][0] in getattr(definition, "tools", ())
    ]
    if not demandes:
        return extra
    from app.core.config import get_settings

    reglages = settings or get_settings()
    outils = tuple(
        build_recherche_media_tool(
            nature=nature,
            identite=AgentIdentity(agent_id=definition.agent_id),
            policy=definition.policy,
            settings=reglages,
        )
        for nature in demandes
    )
    return extra + tuple(outils)
