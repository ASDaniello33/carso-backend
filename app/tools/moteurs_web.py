"""Moteurs de recherche publics, interrogés **directement** — sans clé, sans ``ddgs``.

Le problème réglé : depuis un hébergeur, les points d'entrée publics répondent
« 403 », « 202 » (accusé de réception, corps vide) ou pas du tout. Ce n'est pas
la requête qui est refusée, c'est l'**empreinte** du client : un TLS de
bibliothèque HTTP ordinaire suffit à faire écarter une adresse de datacenter.
``curl_cffi`` reproduit celle de Chrome, ce qui ramène la page de résultats.

Les moteurs sont interrogés **un par un**, dans l'ordre de ``_MOTEURS`` : un
moteur qui bloque n'emporte ni les résultats déjà obtenus ni la recherche — le
suivant prend le relais, et l'échec est conservé pour être dit. Si **aucun** ne
répond, l'erreur nomme chaque cause ; une liste vide signifie au contraire que
les moteurs ont répondu sans rien trouver — les deux ne se confondent jamais.

Deux usages, une seule implémentation du HTTP :

- ``pages()`` : résultats web (``titre``, ``url``, ``resume``) — alimente la
  recherche fédérée de ``app.tools.web``. C'est cette surface qui honore les
  opérateurs de requête (``filetype:pdf``, ``site:``) : un document ou une image
  se cherchent donc avec la **recherche web classique**, sans tool dédié ;
- ``videos()`` : résultats YouTube (titre, page de lecture, vignette, durée,
  auteur) — alimente ``rechercher_video``.

Contrat identique à ``app.tools.fournisseurs_recherche`` : jamais une exception
de transport brute, qui traverserait le tool et tuerait le run AG-UI.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.core.errors import ValidationError

__all__ = ["MOTEURS", "pages", "videos"]

#: Délai par moteur. Un moteur lent ne doit pas consommer la patience du run
#: AG-UI : le suivant prend le relais.
_DELAI = 12
#: Résultats francophones : CARSO travaille en français (Maroc, France).
_REGION = "fr-fr"

#: Un moteur rend une page de résultats déjà normalisée. Une page suffit : la
#: borne des tools (``web_search_max_results``) tient dans une page de dix.
_Moteur = Callable[[Any, str, int], list[dict[str, Any]]]

#: Détecte un refus, pour ne pas confondre « bloqué » et « rien trouvé ».
_REFUS = ("captcha", "unusual traffic", "trafic inhabituel", "/challenge", "anomaly")


class _Muet(Exception):
    """Un moteur n'a pas répondu (réseau, refus HTTP, page anti-robot)."""


def _propre(texte: str) -> str:
    """Texte replié : espaces multiples et sauts de ligne retirés."""
    return " ".join((texte or "").split())


def _texte(balise: Any) -> str:
    """Texte d'une balise BeautifulSoup, chaîne vide si elle est absente."""
    return _propre(balise.get_text(" ", strip=True)) if balise is not None else ""


def _soupe(page: str) -> Any:
    """Arbre BeautifulSoup d'une page de résultats.

    L'analyseur HTML est le seul paquet dont l'absence est possible hors de
    l'extra « web » : elle est dite, jamais devinée.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as erreur:  # pragma: no cover - dépend de l'environnement
        msg = (
            "Recherche web indisponible : le paquet beautifulsoup4 n'est pas "
            "installé (extra « web » du backend)"
        )
        raise ValidationError(msg) from erreur
    return BeautifulSoup(page, "html.parser")


def _session() -> Any:
    """Une session HTTP à l'empreinte de Chrome.

    Remplacée dans les tests : aucune suite ne doit toucher le réseau.
    """
    try:
        from curl_cffi import requests as http
    except ImportError as erreur:  # pragma: no cover - dépend de l'environnement
        msg = (
            "Recherche web indisponible : le paquet curl_cffi n'est pas installé "
            "(extra « web » du backend) — les moteurs publics ne peuvent pas être "
            "interrogés sans empreinte de navigateur"
        )
        raise ValidationError(msg) from erreur
    session: Any = http.Session(impersonate="chrome")
    session.headers["Accept-Language"] = "fr-FR,fr;q=0.9,en;q=0.5"
    return session


def _demander(session: Any, url: str, *, methode: str = "GET", **options: Any) -> Any:
    """Une requête à un moteur, ou ``_Muet`` nommant la cause.

    L'exception est rattrapée **au sens large** : ``curl_cffi`` laisse fuiter des
    erreurs de son client natif, et chacune signifie la même chose ici — ce
    moteur ne répond pas, le suivant doit être essayé. Elle est toujours
    convertie en constat, jamais avalée en silence.
    """
    try:
        reponse = session.request(methode, url, timeout=_DELAI, **options)
    except Exception as erreur:  # noqa: BLE001 - cf. docstring
        raise _Muet(type(erreur).__name__) from erreur
    if reponse.status_code >= 400:
        raise _Muet(f"HTTP {reponse.status_code}")
    return reponse


def _verifier(page: str, elements: Iterable[Any]) -> None:
    """Lève ``_Muet`` quand une page **vide** porte un marqueur de blocage.

    Une page sans résultat n'est pas nécessairement un refus : seule la marque
    d'un captcha ou d'un filtrage anti-robot fait conclure au blocage.
    """
    if list(elements):
        return
    bas = page.lower()
    if any(marqueur in bas for marqueur in _REFUS):
        raise _Muet("page anti-robot (captcha ou filtrage)")


def _resultat(titre: str, url: str, resume: str = "") -> dict[str, Any] | None:
    """``{"titre", "url", "resume"}`` — ou ``None`` pour un lien inexploitable."""
    propre = (url or "").strip()
    if not propre.startswith("http"):
        return None
    return {"titre": titre, "url": propre, "resume": resume}


# --- Moteurs ----------------------------------------------------------------


def _pages_bing(session: Any, requete: str, nb: int) -> list[dict[str, Any]]:
    """Bing — le moteur qui honore le mieux ``filetype:`` et les dates."""
    reponse = _demander(
        session,
        "https://www.bing.com/search",
        params={"q": requete, "count": max(nb, 10), "setlang": "fr", "cc": "FR"},
    )
    blocs = _soupe(reponse.text).select("li.b_algo")
    _verifier(reponse.text, blocs)
    resultats: list[dict[str, Any]] = []
    for bloc in blocs:
        lien = bloc.select_one("h2 a")
        if lien is None:
            continue
        url = _url_bing(lien.get("href", ""))
        retenu = _resultat(_texte(lien), url, _texte(bloc.select_one("p")))
        if retenu is not None:
            resultats.append(retenu)
    return resultats[:nb]


def _pages_yahoo(session: Any, requete: str, nb: int) -> list[dict[str, Any]]:
    """Yahoo (index Bing) — repli direct quand Bing bloque."""
    reponse = _demander(
        session,
        "https://search.yahoo.com/search",
        params={"p": requete, "n": max(nb, 10)},
    )
    blocs = _soupe(reponse.text).select("div.algo")
    _verifier(reponse.text, blocs)
    resultats: list[dict[str, Any]] = []
    for bloc in blocs:
        lien = bloc.select_one("div.compTitle a, h3 a")
        if lien is None:
            continue
        url = _url_yahoo(lien.get("href", ""))
        retenu = _resultat(_texte(lien), url, _texte(bloc.select_one("div.compText")))
        if retenu is not None:
            resultats.append(retenu)
    return resultats[:nb]


def _url_bing(lien: str) -> str:
    """Adresse visée par un lien Bing (enveloppe ``/ck/a?…&u=a1<base64>``).

    Rendue telle quelle, l'enveloppe ferait croire que le résultat est hébergé
    chez Bing, et l'agent citerait le moteur au lieu de la source.
    """
    if "bing.com/ck/a" not in lien:
        return lien
    vise = parse_qs(urlsplit(lien).query).get("u", [""])[0]
    if not vise.startswith("a1"):
        return lien
    brute = vise[2:]
    try:
        decodee = base64.urlsafe_b64decode(brute + "=" * (-len(brute) % 4))
    except ValueError:
        return lien
    return decodee.decode("utf-8", "ignore") or lien


def _url_yahoo(lien: str) -> str:
    """Adresse visée par un lien Yahoo (``/RU=<adresse encodée>/R…=``)."""
    marqueur = "/RU="
    if marqueur not in lien:
        return lien
    debut = lien.index(marqueur) + len(marqueur)
    fin = lien.find("/R", debut)
    return unquote(lien[debut : fin if fin != -1 else len(lien)])


def _pages_brave(session: Any, requete: str, nb: int) -> list[dict[str, Any]]:
    """Brave Search — index indépendant, refuse rarement une adresse d'hébergeur."""
    reponse = _demander(
        session, "https://search.brave.com/search", params={"q": requete, "source": "web"}
    )
    blocs = _soupe(reponse.text).select('div.snippet[data-type="web"]')
    _verifier(reponse.text, blocs)
    resultats: list[dict[str, Any]] = []
    for bloc in blocs:
        lien = bloc.select_one("a[href^='http']")
        if lien is None:
            continue
        titre = bloc.select_one(".title, .snippet-title")
        resume = bloc.select_one(".snippet-description, .description")
        retenu = _resultat(_texte(titre or lien), lien.get("href", ""), _texte(resume))
        if retenu is not None:
            resultats.append(retenu)
    return resultats[:nb]


def _pages_mojeek(session: Any, requete: str, nb: int) -> list[dict[str, Any]]:
    """Mojeek — petit index indépendant, le plus tolérant des cinq."""
    reponse = _demander(session, "https://www.mojeek.com/search", params={"q": requete})
    blocs = _soupe(reponse.text).select("ul.results-standard li")
    _verifier(reponse.text, blocs)
    resultats: list[dict[str, Any]] = []
    for bloc in blocs:
        lien = bloc.select_one("a.title, h2 a")
        if lien is None:
            continue
        retenu = _resultat(_texte(lien), lien.get("href", ""), _texte(bloc.select_one("p.s")))
        if retenu is not None:
            resultats.append(retenu)
    return resultats[:nb]


def _pages_duckduckgo(session: Any, requete: str, nb: int) -> list[dict[str, Any]]:
    """DuckDuckGo — dernier recours : c'est le plus capricieux depuis un hébergeur."""
    reponse = _demander(
        session,
        "https://html.duckduckgo.com/html/",
        methode="POST",
        data={"q": requete, "kl": _REGION},
    )
    blocs = _soupe(reponse.text).select("div.result:not(.result--ad)")
    _verifier(reponse.text, blocs)
    resultats: list[dict[str, Any]] = []
    for bloc in blocs:
        lien = bloc.select_one("a.result__a")
        if lien is None:
            continue
        url = _url_duckduckgo(lien.get("href", ""))
        retenu = _resultat(_texte(lien), url, _texte(bloc.select_one(".result__snippet")))
        if retenu is not None:
            resultats.append(retenu)
    return resultats[:nb]


def _url_duckduckgo(lien: str) -> str:
    """Adresse visée par un lien DuckDuckGo (enveloppe ``/l/?uddg=…``)."""
    if lien.startswith("//"):
        lien = f"https:{lien}"
    if "uddg=" not in lien:
        return lien
    vise = parse_qs(urlsplit(lien).query).get("uddg")
    return vise[0] if vise else lien


#: Ordre = préférence. Bing tête la liste (opérateurs ``filetype:`` et dates),
#: Mojeek et DuckDuckGo la ferment (index indépendant, puis le plus bloquant).
_MOTEURS: tuple[tuple[str, _Moteur], ...] = (
    ("bing", _pages_bing),
    ("yahoo", _pages_yahoo),
    ("brave", _pages_brave),
    ("mojeek", _pages_mojeek),
    ("duckduckgo", _pages_duckduckgo),
)

#: Noms des moteurs, dans l'ordre d'essai (pour les messages et les tests).
MOTEURS: tuple[str, ...] = tuple(nom for nom, _ in _MOTEURS)


def pages(requete: str, nb: int) -> list[dict[str, Any]]:
    """Résultats web d'un moteur public — ``[{"titre", "url", "resume"}]``.

    Les moteurs sont essayés dans l'ordre : le premier qui rend des résultats
    gagne. Un moteur muet n'arrête pas la recherche ; si **aucun** n'a répondu,
    l'erreur nomme chaque cause. Une liste vide, elle, veut dire « les moteurs
    ont répondu, rien ne correspond ».

    Raises:
        ValidationError: aucun moteur n'a répondu, ou ``curl_cffi`` est absent.
    """
    session = _session()
    repondu = False
    refus: list[str] = []
    try:
        for nom, moteur in _MOTEURS:
            try:
                resultats = moteur(session, requete, nb)
            except _Muet as erreur:
                refus.append(f"{nom} : {erreur}")
                continue
            except ValidationError:
                # Un refus **métier** (paquet d'analyse absent) vaut pour tous les
                # moteurs : le répéter cinq fois n'apprendrait rien à l'agent.
                raise
            except Exception as erreur:  # noqa: BLE001 - HTML tiers imprévisible
                # Une page inattendue peut casser une sélection CSS : c'est un
                # moteur inutilisable **pour cette requête**, pas une panne du
                # serveur — le suivant prend le relais, et le constat est nommé.
                refus.append(f"{nom} : {type(erreur).__name__}")
                continue
            repondu = True
            if resultats:
                return resultats
    finally:
        fermer = getattr(session, "close", None)
        if callable(fermer):
            fermer()

    if not repondu:
        msg = (
            "Aucun moteur public n'a répondu : la recherche web externe est "
            "indisponible — " + " ; ".join(refus)
        )
        raise ValidationError(msg, details={"moteurs": list(MOTEURS)})
    return []


# --- Vidéos (YouTube) --------------------------------------------------------

#: Façons dont YouTube publie son état initial, selon la page servie.
_MARQUEURS = (
    "var ytInitialData = ",
    "window['ytInitialData'] = ",
    'window["ytInitialData"] = ',
    "ytInitialData = ",
)


def _etat_initial(page: str) -> Any:
    """Objet ``ytInitialData`` d'une page YouTube, ou ``None``.

    Décodé par le décodeur JSON (jamais une expression régulière) : l'objet est
    gros et contient des chaînes qui ressemblent à du code.
    """
    decodeur = json.JSONDecoder()
    for marqueur in _MARQUEURS:
        position = page.find(marqueur)
        if position == -1:
            continue
        debut = page.find("{", position)
        if debut == -1:
            continue
        try:
            donnees, _ = decodeur.raw_decode(page, debut)
        except ValueError:
            continue
        return donnees
    return None


def _descendre(objet: Any, cle: str) -> Iterable[Any]:
    """Toutes les valeurs d'une clé, à n'importe quelle profondeur."""
    if isinstance(objet, dict):
        for nom, valeur in objet.items():
            if nom == cle:
                yield valeur
            else:
                yield from _descendre(valeur, cle)
    elif isinstance(objet, list):
        for element in objet:
            yield from _descendre(element, cle)


def _texte_riche(valeur: Any) -> str:
    """Texte d'un champ YouTube (``simpleText`` ou ``runs``)."""
    if not valeur:
        return ""
    if "simpleText" in valeur:
        return _propre(valeur["simpleText"])
    return _propre("".join(partie.get("text", "") for partie in valeur.get("runs", [])))


def videos(requete: str, nb: int) -> list[dict[str, Any]]:
    """Vidéos YouTube — ``[{"titre", "url", "vignette", "duree", "auteur", "date"}]``.

    ``url`` est la page de lecture, jamais un fichier : l'interface lit la vidéo
    chez l'hébergeur (iframe), et rien n'est téléchargé.

    Raises:
        ValidationError: YouTube n'a pas répondu ou n'a pas publié ses résultats.
    """
    session = _session()
    try:
        try:
            reponse = _demander(
                session,
                "https://www.youtube.com/results",
                params={"search_query": requete, "hl": "fr", "gl": "FR"},
            )
        except _Muet as erreur:
            # Un seul fournisseur : son échec est dit tel quel, jamais laissé
            # traverser en exception de transport (qui tuerait le run AG-UI).
            msg = f"YouTube n'a pas répondu ({erreur}) : recherche de vidéos abandonnée"
            raise ValidationError(msg) from erreur
    finally:
        fermer = getattr(session, "close", None)
        if callable(fermer):
            fermer()

    donnees = _etat_initial(reponse.text)
    if donnees is None:
        bas = reponse.text.lower()
        cause = (
            "page anti-robot (captcha ou consentement)"
            if any(marqueur in bas for marqueur in _REFUS)
            else "page inattendue"
        )
        msg = (
            f"YouTube n'a pas publié ses résultats ({cause}) : la recherche de "
            "vidéos est abandonnée, aucun résultat n'est inventé"
        )
        raise ValidationError(msg)

    resultats: list[dict[str, Any]] = []
    for bloc in _descendre(donnees, "videoRenderer"):
        if not isinstance(bloc, dict):
            continue
        identifiant = bloc.get("videoId")
        if not identifiant:
            continue
        vignettes = (bloc.get("thumbnail") or {}).get("thumbnails") or [{}]
        resultats.append(
            {
                "titre": _texte_riche(bloc.get("title")),
                "url": f"https://www.youtube.com/watch?v={identifiant}",
                "vignette": vignettes[-1].get("url", ""),
                "duree": _texte_riche(bloc.get("lengthText")),
                "auteur": _texte_riche(bloc.get("ownerText")),
                "date": _texte_riche(bloc.get("publishedTimeText")),
            }
        )
        if len(resultats) >= nb:
            break
    return resultats
