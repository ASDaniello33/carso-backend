"""Fournisseurs de recherche : HTTP, reprises et rendu des résultats.

Un **seul propriétaire par fournisseur**. Deux tools composent avec les mêmes
services :

- ``langsearch_web_search`` (``app/tools/web.py``) les fédère ;
- ``rechercher_image`` / ``rechercher_video`` / ``rechercher_document``
  (``app/tools/recherche_media.py``) prennent LangSearch et DuckDuckGo tour à
  tour — DuckDuckGo d'abord (meilleure couverture), LangSearch en repli.

D'où ce module : le contrat HTTP (en-têtes, délais, reprises) et le rendu d'une
page de résultats vivent ici, une fois. Deux implémentations de la même page
finissent toujours par diverger.

Contrat commun à toutes les fonctions ``pages_*`` :

- elles rendent une **liste** de résultats ``{"titre", "url", "resume",
  "date_publication"}``, vide quand le fournisseur répond sans résultat ;
- elles lèvent ``ValidationError`` quand le fournisseur refuse, ne répond pas ou
  répond un corps illisible — jamais une exception ``httpx`` brute, qui
  traverserait le tool, crasherait le graphe LangGraph et tuerait le run AG-UI
  (``TypedTool`` ne rattrape que ``CarsoError``).

Un appelant qui a plusieurs fournisseurs enchaîne donc sans rattraper autre
chose que ``ValidationError``.
"""

from __future__ import annotations

import html
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app.core.errors import ValidationError

__all__ = [
    "EN_TETES",
    "PAGE_DUCKDUCKGO",
    "TIMEOUT_HTTP",
    "attendre",
    "envoyer",
    "pages_duckduckgo",
    "pages_langsearch",
    "soupe",
    "url_reelle",
]

#: Page publique de résultats DuckDuckGo : la seule à honorer ``df`` (fraîcheur)
#: et les opérateurs ``filetype:`` sans exiger de jeton ``vqd``.
PAGE_DUCKDUCKGO = "https://html.duckduckgo.com/html/"

#: Identité déclarée : un agent CARSO qui interroge un service public n'est pas
#: un navigateur anonyme.
EN_TETES = {
    "User-Agent": "CARSO-AI/1.0 (+agent interne CARSO)",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
}
TIMEOUT_HTTP = 15.0
#: Délai de **connexion** court : un hôte qui ne répond pas au SYN (réseau
#: sortant filtré, fournisseur qui ignore les plages d'un hébergeur) doit être
#: constaté vite quand un autre fournisseur peut prendre le relais.
TIMEOUT_CONNEXION = 5.0
#: Délai d'une recherche média : la connexion échoue vite, la lecture garde du
#: temps (une page de résultats est lourde).
DELAI_RECHERCHE = httpx.Timeout(TIMEOUT_HTTP, connect=TIMEOUT_CONNEXION)

#: Refus **temporaires** : ils valent une seconde tentative avant d'être annoncés.
#: « 202 » est l'accusé de réception que DuckDuckGo renvoie aux rafales : la
#: requête est acceptée, pas encore servie.
STATUTS_TEMPORAIRES = frozenset({202, 429, 500, 502, 503, 504})
TENTATIVES = 2
ATTENTE_BASE = 1.5

#: Enveloppe de redirection DuckDuckGo (``/l/?uddg=…``).
_UDDG = "uddg="


def attendre(secondes: float) -> None:
    """Pause entre deux tentatives (remplacée dans les tests : aucune attente)."""
    time.sleep(secondes)


def _injoignable(cible: str, cause: str, tentatives: int) -> ValidationError:
    """Erreur métier d'un fournisseur muet — la cause est nommée, pas devinée."""
    essais = "1 tentative" if tentatives == 1 else f"{tentatives} tentatives"
    msg = (
        f"{cible} injoignable depuis le serveur ({cause}, {essais}) : réseau "
        "sortant indisponible ou fournisseur momentanément inaccessible"
    )
    return ValidationError(msg, details={"cause": cause})


def envoyer(
    client: httpx.Client,
    methode: str,
    url: str,
    *,
    cible: str,
    params: dict[str, Any] | None = None,
    donnees: dict[str, Any] | None = None,
    corps: dict[str, Any] | None = None,
    entetes: dict[str, str] | None = None,
    timeout: Any = TIMEOUT_HTTP,
    tentatives: int = TENTATIVES,
    reessayer_reseau: bool = True,
) -> httpx.Response:
    """Une requête HTTP, avec reprise brève sur un échec **transitoire**.

    Deux formes du même problème sont retentées : le refus HTTP d'une rafale
    (``202``/``429``/``503``) et l'échec réseau (``ConnectTimeout``, DNS,
    connexion réinitialisée). Après des reprises vaines, l'échec devient une
    erreur **métier** explicite.

    Args:
        cible: nom du fournisseur, cité dans le message d'erreur.
        tentatives: nombre d'essais, refus temporaires compris.
        reessayer_reseau: quand un autre fournisseur peut prendre le relais
            (recherche média), un échec réseau ne vaut pas la peine d'être
            rejoué : il est constaté une fois, la chaîne de fournisseurs est
            elle-même la reprise. Un refus temporaire reste retenté — la
            requête est acceptée, il suffit d'attendre.

    Raises:
        ValidationError: le fournisseur n'a jamais répondu.
    """
    reponse: httpx.Response | None = None
    dernier_echec: httpx.RequestError | None = None
    for essai in range(tentatives):
        if essai:
            attendre(ATTENTE_BASE * essai)
        try:
            reponse = client.request(
                methode,
                url,
                params=params,
                data=donnees,
                json=corps,
                headers=entetes,
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.RequestError as erreur:
            if not reessayer_reseau:
                raise _injoignable(cible, type(erreur).__name__, 1) from erreur
            dernier_echec = erreur
            continue
        if reponse.status_code not in STATUTS_TEMPORAIRES:
            return reponse
    if reponse is not None:
        return reponse
    cause = type(dernier_echec).__name__ if dernier_echec else "réseau"
    raise _injoignable(cible, cause, tentatives) from dernier_echec


def url_reelle(brut: str) -> str:
    """Adresse visée par un résultat : l'enveloppe ``uddg=`` est retirée.

    Rendre l'enveloppe ferait croire que le document est hébergé chez le moteur ;
    c'est l'adresse réelle qui est présentée à l'agent et à l'utilisateur.
    """
    absolue = html.unescape((brut or "").strip())
    if absolue.startswith("//"):
        absolue = f"https:{absolue}"
    if _UDDG not in absolue:
        return absolue
    vise = parse_qs(urlsplit(absolue).query).get("uddg")
    return vise[0] if vise else absolue


def soupe(page: str) -> Any:
    """Arbre BeautifulSoup d'une page, ou refus explicite si la lib est absente."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        msg = (
            "Lecture HTML indisponible : le paquet beautifulsoup4 n'est pas "
            "installé (extra « web » du backend) — aucun texte n'est deviné"
        )
        raise ValidationError(msg) from exc
    return BeautifulSoup(page, "html.parser")


def _refus(cible: str, statut: int) -> ValidationError:
    """Refus HTTP dit tel quel — jamais une liste vide qui ferait croire à rien."""
    msg = f"{cible} a répondu {statut} (recherche refusée)"
    return ValidationError(msg, details={"status": statut})


def pages_duckduckgo(
    client: httpx.Client,
    *,
    requete: str,
    nb: int,
    date_filtre: str | None = None,
    timeout: Any = DELAI_RECHERCHE,
    tentatives: int = TENTATIVES,
    reessayer_reseau: bool = True,
) -> list[dict[str, Any]]:
    """Résultats de la page publique DuckDuckGo, rendus normalisés.

    Args:
        nb: nombre de résultats **bruts** lus. Un appelant qui filtre ensuite
            (une extension de fichier, un hébergeur de vidéos) demande une
            fenêtre plus large que le nombre qu'il rendra.
        date_filtre: code ``df`` (``d``, ``w``, ``m``, ``y``).

    Raises:
        ValidationError: refus du fournisseur, page illisible.
    """
    donnees: dict[str, Any] = {"q": requete}
    if date_filtre:
        donnees["df"] = date_filtre

    reponse = envoyer(
        client,
        "POST",
        PAGE_DUCKDUCKGO,
        cible="DuckDuckGo",
        donnees=donnees,
        timeout=timeout,
        tentatives=tentatives,
        reessayer_reseau=reessayer_reseau,
    )
    if reponse.status_code != 200:
        raise _refus("DuckDuckGo", reponse.status_code)

    resultats: list[dict[str, Any]] = []
    for bloc in soupe(reponse.text).select("div.result"):
        lien = bloc.select_one("a.result__a")
        if lien is None:
            continue
        extrait = bloc.select_one(".result__snippet")
        resultats.append(
            {
                "titre": " ".join(lien.get_text(" ", strip=True).split()),
                "url": url_reelle(lien.get("href", "")),
                "resume": (" ".join(extrait.get_text(" ", strip=True).split()) if extrait else ""),
                "date_publication": None,
            }
        )
        if len(resultats) >= nb:
            break
    return resultats


def pages_langsearch(
    client: httpx.Client,
    *,
    cle: str,
    base_url: str,
    corps: dict[str, Any],
    nb: int,
    timeout: Any = TIMEOUT_HTTP,
    tentatives: int = TENTATIVES,
    reessayer_reseau: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """Résultats de l'API LangSearch — ``(resultats, usage, log_id)``.

    Args:
        corps: corps JSON de ``POST /v1/web-search`` (le même endpoint sert la
            recherche de pages, de documents et de vidéos : seule la requête
            change, et le filtrage par nature revient à l'appelant).

    Raises:
        ValidationError: appel refusé (statut non 200, corps illisible).
    """
    reponse = envoyer(
        client,
        "POST",
        f"{base_url.rstrip('/')}/v1/web-search",
        cible="LangSearch",
        corps=corps,
        # SecretStr : la clé est déballée uniquement pour cet en-tête.
        entetes={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
        timeout=timeout,
        tentatives=tentatives,
        reessayer_reseau=reessayer_reseau,
    )
    if reponse.status_code != 200:
        raise _refus("LangSearch", reponse.status_code)

    try:
        donnees = json.loads(reponse.text)
    except ValueError as erreur:
        # Un 200 non-JSON (mur anti-bot, corps tronqué) ne doit pas sortir en
        # JSONDecodeError : ce serait un run AG-UI interrompu au lieu d'une
        # erreur dite à l'agent.
        msg = (
            "LangSearch a répondu un corps illisible (JSON attendu) : recherche "
            "abandonnée, aucun résultat n'est inventé"
        )
        raise ValidationError(msg) from erreur

    brut = (donnees.get("data") or {}).get("webPages", {}).get("value", []) or []
    resultats = [
        {
            "titre": item.get("name") or "",
            "url": item.get("url") or "",
            "resume": item.get("text") or item.get("snippet") or "",
            "date_publication": item.get("datePublished"),
        }
        for item in brut[:nb]
    ]
    usage = donnees.get("usage") or {}
    return (
        resultats,
        {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
        donnees.get("log_id"),
    )
