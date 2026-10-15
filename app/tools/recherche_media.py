"""Recherche média sur le web—images, vidéos, documents (DuckDuckGo).

Trois tools en **lecture pure** pour trouver des données particulières hors de
CARSO—une image, une vidéo, un document (PDF, tableur, présentation…). Ils
complètent ``langsearch_web_search`` (texte)—ici, la réponse est une liste de
ressources localisables, jamais un contenu recopié.

Ce que ces tools ne font pas :

- **aucun téléchargement** de fichier et **aucun scraping** de page—ils rendent
  l'URL et la vignette, la décision d'ouvrir appartient à l'utilisateur ;
- **aucun secret**—l'appel DuckDuckGo ne demande pas de clé, et rien de la
  conversation ne part dans la requête au-delà du texte demandé ;
- **aucune invention**—un résultat vide est rendu vide, jamais complété.

Fournisseur—les points d'entrée publics de DuckDuckGo (page « images » et
« vidéos » + page de résultats pour les documents). Ils sont interrogés avec le
client ``httpx`` du projet, comme LangSearch—un transport est injectable, donc
les tests ne touchent pas au réseau. Le jour où CARSO préfère une API officielle
avec clé, seul ce module change—les tools et leurs schémas restent.

Un refus **temporaire** du fournisseur (le « 202 » qu'il renvoie aux rafales de
requêtes) est retenté brièvement avant d'être annoncé—une recherche qui aurait
abouti une seconde plus tard ne doit pas se solder par un échec.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel, Field

from app.core.errors import ValidationError
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = [
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
#: DuckDuckGo ne publie pas de verticale « documents » : la recherche passe par
#: cette page de résultats, seule à honorer les opérateurs ``filetype:``.
_DOCUMENTS = "https://html.duckduckgo.com/html/"

#: Identité déclarée : un agent CARSO n'est pas un navigateur anonyme.
_EN_TETES = {
    "User-Agent": "CARSO-AI/1.0 (+agent interne CARSO)",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
}
_TIMEOUT_HTTP = 15.0
_CARACTERES_MAX_REQUETE = 300
_MAX_RESULTATS = 10

#: DuckDuckGo ne refuse pas toujours franchement : une rafale de requêtes reçoit
#: un « 202 » (accusé de réception, corps vide) avant d'être servie. Trois
#: recherches média d'affilée ne doivent donc pas se solder par un échec : on
#: retente brièvement, puis on dit le refus — jamais une liste vide silencieuse.
_STATUTS_TEMPORAIRES = frozenset({202, 429, 503})
_TENTATIVES = 3
_ATTENTE_BASE = 0.8

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

#: Le jeton apparaît en JavaScript (`vqd="4-123…"`) et dans les URL d'appel
#: (`vqd=4-123…&`) : les deux formes sont acceptées, un jeton par verticale.
_VQD = re.compile(r"""vqd=(?:"|')?([\d-]+)""")
#: Résultat de la page « documents » : une ancre ``result__a`` (l'ordre de ses
#: attributs n'est pas garanti, l'ancre est donc lue puis son ``href`` extrait).
_ANCRE = re.compile(r'<a\b([^>]*class="result__a[^"]*"[^>]*)>(.*?)</a>', re.S)
_HREF = re.compile(r'href="([^"]+)"')
_BALISES = re.compile(r"<[^>]+>")
_PROTOCOLE_RELATIF = re.compile(r"^//")


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


def _texte_brut(fragment: str) -> str:
    """Titre HTML → texte : balises retirées, entités décodées, espaces repliés."""
    return " ".join(html.unescape(_BALISES.sub("", fragment)).split())


def _url_absolue(brut: str) -> str:
    """URL rendue absolue (le moteur répond parfois en ``//domaine/chemin``)."""
    return _PROTOCOLE_RELATIF.sub("https://", brut)


def _url_reelle(brut: str) -> str:
    """URL visée par un résultat : le moteur enveloppe ses liens (``uddg=``).

    Rendre l'enveloppe ferait croire que le document est hébergé chez le moteur ;
    c'est l'adresse réelle qui est présentée à l'utilisateur.
    """
    absolue = _url_absolue(html.unescape(brut))
    if "uddg=" not in absolue:
        return absolue
    vise = parse_qs(urlsplit(absolue).query).get("uddg")
    return vise[0] if vise else absolue


def _liens_documents(page: str) -> list[tuple[str, str]]:
    """Ancres de résultats ``(href, titre)`` d'une page de recherche."""
    ancres: list[tuple[str, str]] = []
    for balise, titre in _ANCRE.findall(page):
        trouve = _HREF.search(balise)
        if trouve:
            ancres.append((trouve.group(1), titre))
    return ancres


def _borne(payload: dict[str, Any], plafond: int) -> int:
    """Nombre de résultats demandés : borné par la configuration, jamais l'inverse."""
    demande = payload.get("max_resultats") or plafond
    return max(1, min(int(demande), plafond, _MAX_RESULTATS))


def _client_de_secours(client: httpx.Client | None) -> httpx.Client:
    """Client éphémère par appel : aucun état partagé entre deux agents."""
    if client is not None:
        return client
    return httpx.Client(timeout=_TIMEOUT_HTTP, headers=_EN_TETES)


def _attendre(secondes: float) -> None:
    """Pause entre deux tentatives (remplacée dans les tests : aucune attente réelle)."""
    time.sleep(secondes)


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> httpx.Response:
    """GET avec reprise brève sur un refus **temporaire** du fournisseur."""
    reponse = client.get(url, params=params, timeout=_TIMEOUT_HTTP)
    for essai in range(1, _TENTATIVES):
        if reponse.status_code not in _STATUTS_TEMPORAIRES:
            return reponse
        _attendre(_ATTENTE_BASE * (2 ** (essai - 1)))
        reponse = client.get(url, params=params, timeout=_TIMEOUT_HTTP)
    return reponse


def _message_refus(reponse: httpx.Response, recherche: str) -> str:
    """Refus du fournisseur, dit exactement (et différemment s'il est temporaire)."""
    if reponse.status_code in _STATUTS_TEMPORAIRES:
        return (
            f"DuckDuckGo a répondu {reponse.status_code} après {_TENTATIVES} tentatives "
            f"({recherche} refusée pour l'instant)"
        )
    return f"DuckDuckGo a répondu {reponse.status_code} ({recherche} refusée)"


def _jeton_vqd(client: httpx.Client, requete: str, verticale: str) -> str:
    """Jeton ``vqd`` exigé par les verticales JSON de DuckDuckGo.

    Il est publié dans la page HTML de recherche ; sans lui, ``/i.js`` répond une
    erreur. Un jeton illisible est une erreur **explicite** : mieux vaut le dire
    que renvoyer une liste vide qui ferait croire à une absence de résultat.
    """
    reponse = _get(client, _PAGE, {"q": requete, "iax": verticale, "ia": verticale})
    if reponse.status_code != 200:
        msg = _message_refus(reponse, f"recherche {verticale}")
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
    reponse = _get(
        client,
        f"{_PAGE.rstrip('/')}{chemin}",
        {"l": "fr-fr", "o": "json", "q": requete, "vqd": jeton},
    )
    if reponse.status_code != 200:
        msg = _message_refus(reponse, f"recherche {verticale}")
        raise ValidationError(msg, details={"status": reponse.status_code})
    donnees = reponse.json()
    return list(donnees.get("results") or [])[:nb]


def _media_direct(url: Any) -> str:
    """URL d'un **fichier** média, ou chaîne vide si ce n'en est pas un.

    Le moteur rend parfois l'adresse du fichier lui-même (un ``.mp4``) dans
    ``content``, parfois celle d'une page de lecture. Seule la première se lit
    dans l'interface : annoncer l'autre comme un média ferait échouer le lecteur
    au lieu d'ouvrir la page.
    """
    if not isinstance(url, str):
        return ""
    chemin = url.split("?")[0].split("#")[0].lower()
    return url if chemin.endswith(_EXTENSIONS_MEDIA) else ""


def _chercher_images(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> dict[str, Any]:
    """Images : titre, page d'origine, **fichier image**, vignette, dimensions.

    ``url`` reste la page d'origine (le contexte du document) ; ``image`` est le
    fichier lui-même, seul affichable en grand. Les deux sont rendus : la page
    situe l'image, le fichier la montre.
    """
    resultats = [
        {
            "titre": item.get("title") or "",
            "url": item.get("url") or item.get("image") or "",
            "image": item.get("image") or item.get("thumbnail") or "",
            "vignette": item.get("thumbnail") or item.get("image") or "",
            "largeur": item.get("width"),
            "hauteur": item.get("height"),
            "source": item.get("source") or "",
        }
        for item in _verticale(
            client,
            requete=payload["requete"],
            verticale="images",
            chemin=_CHEMIN_IMAGES,
            nb=nb,
        )
    ]
    return {"requete": payload["requete"], "nb_resultats": len(resultats), "resultats": resultats}


def _chercher_videos(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> dict[str, Any]:
    """Vidéos : titre, page de lecture, **fichier vidéo** s'il est direct, durée, auteur.

    ``url`` est la page de lecture (là où l'utilisateur retrouve la vidéo chez
    son hébergeur) ; ``media`` n'est rempli que si le moteur a rendu l'adresse du
    fichier lui-même — c'est la seule qui se joue dans l'interface.
    """
    resultats = [
        {
            "titre": item.get("title") or "",
            "url": item.get("url") or item.get("content") or "",
            "media": _media_direct(item.get("content")),
            "vignette": (item.get("images") or {}).get("medium")
            or item.get("image")
            or "",
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
    return {"requete": payload["requete"], "nb_resultats": len(resultats), "resultats": resultats}


def _extension_autorisee(url: str, extensions: tuple[str, ...]) -> bool:
    """Le lien vise-t-il vraiment un fichier du type demandé ?"""
    chemin = url.split("?")[0].split("#")[0].lower()
    return any(chemin.endswith(f".{extension}") for extension in extensions)


def _chercher_documents(
    client: httpx.Client, payload: dict[str, Any], nb: int
) -> dict[str, Any]:
    """Documents : la requête est restreinte aux fichiers réels (``filetype:``).

    La page de résultats est interrogée avec un opérateur par extension demandée,
    puis chaque lien est **vérifié** : un résultat qui ne pointe pas vers
    l'extension demandée est écarté plutôt que présenté comme un document — le
    moteur peut toujours ignorer l'opérateur, jamais mentir à sa place.
    """
    demandees = tuple(
        str(extension).strip().lstrip(".").lower()
        for extension in (payload.get("extensions") or EXTENSIONS_DOCUMENT)
        if str(extension).strip()
    )
    if not demandees:
        demandees = EXTENSIONS_DOCUMENT
    operateurs = " OR ".join(f"filetype:{extension}" for extension in demandees)
    reponse = _get(client, _DOCUMENTS, {"q": f"{payload['requete']} ({operateurs})"})
    if reponse.status_code != 200:
        msg = _message_refus(reponse, "recherche de documents")
        raise ValidationError(msg, details={"status": reponse.status_code})

    resultats: list[dict[str, Any]] = []
    vues: set[str] = set()
    for lien, titre in _liens_documents(reponse.text):
        url = _url_reelle(lien)
        if url in vues or not _extension_autorisee(url, demandees):
            continue
        vues.add(url)
        chemin = url.split("?")[0].split("#")[0]
        resultats.append(
            {
                "titre": _texte_brut(titre),
                "url": url,
                "type_fichier": chemin.rsplit(".", 1)[-1].lower(),
                "domaine": url.split("/")[2] if "//" in url else "",
            }
        )
        if len(resultats) >= nb:
            break
    return {"requete": payload["requete"], "nb_resultats": len(resultats), "resultats": resultats}


_CANDIDATS: dict[str, tuple[str, type[_RechercheBase], Any]] = {
    "image": (
        "rechercher_image",
        RechercheImageInput,
        _chercher_images,
    ),
    "video": (
        "rechercher_video",
        RechercheVideoInput,
        _chercher_videos,
    ),
    "document": (
        "rechercher_document",
        RechercheDocumentInput,
        _chercher_documents,
    ),
}

#: Ce que chaque tool cherche, dit à l'utilisateur (jamais de promesse vague).
_DESCRIPTIONS = {
    "image": (
        "Recherche des images sur le web et rend titre, page d'origine (url), URL du "
        "fichier image (image), vignette, dimensions et source. Lecture seule : aucun "
        "fichier n'est téléchargé, aucune page n'est recopiée. Les résultats sont des "
        "liens à ouvrir et à vérifier."
    ),
    "video": (
        "Recherche des vidéos sur le web et rend titre, page de lecture (url), URL du "
        "fichier vidéo (media, vide si le moteur n'a rendu qu'une page), vignette, "
        "durée et auteur. Lecture seule : aucun média n'est téléchargé. Les résultats "
        "sont des liens à ouvrir et à vérifier."
    ),
    "document": (
        "Recherche des documents en ligne (PDF, DOCX, XLSX, PPTX, CSV…) et rend titre, "
        "URL, type de fichier et domaine. Lecture seule : aucun fichier n'est téléchargé "
        "ni indexé. Ne remplace pas les documents CARSO — c'est une source externe à "
        "vérifier."
    ),
}


def build_recherche_media_tool(
    *,
    nature: Literal["image", "video", "document"],
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
    nom, schema, chercher = _CANDIDATS[nature]
    plafond = int(getattr(settings, "web_search_max_results", 5) or 5)
    client_fourni = client is not None

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "web_search")
        nb = _borne(payload, plafond)
        if client_fourni and client is not None:
            return chercher(client, payload, nb)
        with _client_de_secours(None) as temporaire:
            return chercher(temporaire, payload, nb)

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
    return extra + outils
