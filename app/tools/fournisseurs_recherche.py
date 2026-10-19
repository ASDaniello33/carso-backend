"""Fournisseurs de recherche : HTTP, reprises et rendu des résultats.

Un **seul propriétaire par fournisseur**. Deux tools composent avec les mêmes
services :

- ``langsearch_web_search`` (``app/tools/web.py``) les fédère ;
- ``rechercher_video`` (``app/tools/recherche_video.py``) cherche une vidéo.

Le HTTP des moteurs publics a **son** propriétaire (``app/tools/moteurs_web``) :
leur session imite l'empreinte de Chrome, ce qu'un client ``httpx`` ne sait pas
faire. Ce module ne porte donc que ce qui se partage : les en-têtes, les délais,
l'analyse HTML, et la conversion d'un échec de fournisseur en erreur métier.

Contrat commun :

- les fonctions ``pages_*`` rendent une **liste** de résultats
  ``{"titre", "url", "resume", "date_publication"}``, vide quand le fournisseur
  répond sans résultat ;
- elles lèvent ``ValidationError`` quand le fournisseur refuse, ne répond pas ou
  répond un corps illisible — jamais une exception ``httpx`` brute, qui
  traverserait le tool, crasherait le graphe LangGraph et tuerait le run AG-UI
  (``TypedTool`` ne rattrape que ``CarsoError``).

Un appelant qui a plusieurs fournisseurs enchaîne donc sans rattraper autre
chose que ``ValidationError``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.errors import ValidationError

__all__ = [
    "EN_TETES",
    "TIMEOUT_HTTP",
    "attendre",
    "envoyer",
    "pages_langsearch",
    "soupe",
]

#: Identité déclarée : un agent CARSO qui interroge un service public n'est pas
#: un navigateur anonyme.
EN_TETES = {
    "User-Agent": "CARSO-AI/1.0 (+agent interne CARSO)",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
}
TIMEOUT_HTTP = 15.0

#: Refus **temporaires** : ils valent une seconde tentative avant d'être annoncés.
STATUTS_TEMPORAIRES = frozenset({202, 429, 500, 502, 503, 504})
TENTATIVES = 2
ATTENTE_BASE = 1.5


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
    url: str,
    *,
    cible: str,
    corps: dict[str, Any],
    entetes: dict[str, str],
    timeout: Any = TIMEOUT_HTTP,
    tentatives: int = TENTATIVES,
) -> httpx.Response:
    """``POST`` JSON à un fournisseur d'API, avec reprise brève.

    Deux formes du même problème sont retentées : le refus HTTP d'une rafale
    (``202``/``429``/``503``) et l'échec réseau (``ConnectTimeout``, DNS,
    connexion réinitialisée). Après des reprises vaines, l'échec devient une
    erreur **métier** explicite.

    Args:
        cible: nom du fournisseur, cité dans le message d'erreur.

    Raises:
        ValidationError: le fournisseur n'a jamais répondu.
    """
    reponse: httpx.Response | None = None
    dernier_echec: httpx.RequestError | None = None
    for essai in range(tentatives):
        if essai:
            attendre(ATTENTE_BASE * essai)
        try:
            reponse = client.post(
                url,
                json=corps,
                headers=entetes,
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.RequestError as erreur:
            dernier_echec = erreur
            continue
        if reponse.status_code not in STATUTS_TEMPORAIRES:
            return reponse
    if reponse is not None:
        return reponse
    cause = type(dernier_echec).__name__ if dernier_echec else "réseau"
    raise _injoignable(cible, cause, tentatives) from dernier_echec


def soupe(page: str) -> Any:
    """Arbre BeautifulSoup d'une page, ou refus explicite si la lib est absente."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as erreur:  # pragma: no cover - dépend de l'environnement
        msg = (
            "Lecture HTML indisponible : le paquet beautifulsoup4 n'est pas "
            "installé (extra « web » du backend) — aucun texte n'est deviné"
        )
        raise ValidationError(msg) from erreur
    return BeautifulSoup(page, "html.parser")


def _refus(cible: str, statut: int) -> ValidationError:
    """Refus HTTP dit tel quel — jamais une liste vide qui ferait croire à rien."""
    msg = f"{cible} a répondu {statut} (recherche refusée)"
    return ValidationError(msg, details={"status": statut})


def pages_langsearch(
    client: httpx.Client,
    *,
    cle: str,
    base_url: str,
    corps: dict[str, Any],
    nb: int,
    timeout: Any = TIMEOUT_HTTP,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """Résultats de l'API LangSearch — ``(resultats, usage, log_id)``.

    Args:
        corps: corps JSON de ``POST /v1/web-search``.

    Raises:
        ValidationError: appel refusé (statut non 200, corps illisible).
    """
    reponse = envoyer(
        client,
        f"{base_url.rstrip('/')}/v1/web-search",
        cible="LangSearch",
        corps=corps,
        # SecretStr : la clé est déballée uniquement pour cet en-tête.
        entetes={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
        timeout=timeout,
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
