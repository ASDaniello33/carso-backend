"""Découverte des modèles proposés par un fournisseur d'IA.

**Pourquoi un module séparé.** Chaque fournisseur n'expose pas la même API : il
serait faux d'imposer un unique protocole. La découverte est donc **par
protocole**, et un protocole sans découverte connue le dit explicitement au lieu
d'inventer une liste.

Protocoles pris en charge aujourd'hui :

| Protocole | Découverte | Méthode |
|---|---|---|
| ``openai`` | oui | ``GET {base_url ou https://api.openai.com/v1}/models`` |
| ``openai_compatible`` | oui | même route, sur la ``base_url`` du fournisseur (vLLM, passerelles) |
| ``google_genai`` | oui | ``GET {base_url ou défaut}/models``, en-tête ``x-goog-api-key`` |

**Aucune URL n'est jamais exigée par la découverte** : chaque protocole connu a
son endpoint public par défaut (``BASE_PAR_DEFAUT``). Un fournisseur qui n'
expose pas cette route (404) n'est pas une panne : on remonte
``DecouverteIndisponible`` pour que l'interface propose l'ajout manuel.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Délai d'une découverte : un listage de modèles est rapide ; au-delà, le
#: fournisseur est injoignable et l'interface doit le dire vite.
DELAI_S = 20.0

#: Endpoint public de chaque protocole, utilisé quand le fournisseur n'en donne
#: pas. C'est ce qui permet de découvrir les modèles sans exiger d'URL.
BASE_PAR_DEFAUT = {
    "openai": "https://api.openai.com/v1",
    "google_genai": "https://generativelanguage.googleapis.com/v1beta",
}


class DecouverteIndisponible(Exception):
    """La découverte automatique n'existe pas (ou pas) pour ce fournisseur.

    L'interface doit alors proposer l'**ajout manuel** : ce n'est pas une erreur
    fatale, c'est une limite du fournisseur.
    """


def _url_modeles(protocole: str, base_url: str | None) -> str | None:
    """URL de listage des modèles, ou ``None`` si le protocole ne le permet pas.

    L'URL du fournisseur l'emporte ; à défaut, l'endpoint public du protocole
    s'applique (``BASE_PAR_DEFAUT``), donc l'URL n'est jamais obligatoire.
    """
    base = (base_url or BASE_PAR_DEFAUT.get(protocole) or "").strip()
    if not base:
        return None
    return f"{base.rstrip('/')}/models"


def _base_google(base_url: str | None) -> str:
    """Base Gemini, avec la version d'API si l'URL enregistrée n'en porte pas.

    ``https://generativelanguage.googleapis.com`` sans ``/v1beta`` renverrait
    404 : on complète plutôt que d'échouer sur une URL presque juste.
    """
    base = (base_url or BASE_PAR_DEFAUT["google_genai"]).strip().rstrip("/")
    if "/v1" not in base and "/v1beta" not in base:
        base = f"{base}/v1beta"
    return base


def _identifiants_google(donnees: Any) -> list[str]:
    """Identifiants de modèles d'une réponse Gemini (``{"models": [...]}``).

    Seuls les modèles de **conversation** sont retenus (``generateContent``) :
    l'API liste aussi les modèles d'embedding, qu'un agent de chat ne peut pas
    utiliser. Le préfixe ``models/`` est retiré, c'est le nom que le SDK attend.
    """
    if not isinstance(donnees, dict) or not isinstance(donnees.get("models"), list):
        raise DecouverteIndisponible(
            "La réponse du fournisseur ne contient pas de liste de modèles "
            "(forme inattendue). Ajoutez les modèles à la main."
        )
    noms: list[str] = []
    for entree in donnees["models"]:
        if not isinstance(entree, dict):
            continue
        identifiant = entree.get("name")
        if not isinstance(identifiant, str) or not identifiant.strip():
            continue
        methodes = entree.get("supportedGenerationMethods")
        if isinstance(methodes, list) and "generateContent" not in methodes:
            continue
        noms.append(identifiant.strip().removeprefix("models/"))
    return list(dict.fromkeys(noms))


def _identifiants(donnees: Any) -> list[str]:
    """Identifiants de modèles d'une réponse compatible OpenAI.

    Forme attendue : ``{"data": [{"id": "..."}, ...]}``. Une réponse d'une autre
    forme est refusée plutôt que devinée — mieux vaut l'ajout manuel qu'une
    liste inventée.
    """
    if not isinstance(donnees, dict) or not isinstance(donnees.get("data"), list):
        raise DecouverteIndisponible(
            "La réponse du fournisseur ne contient pas de liste de modèles "
            "(forme inattendue). Ajoutez les modèles à la main."
        )
    noms: list[str] = []
    for entree in donnees["data"]:
        identifiant = entree.get("id") if isinstance(entree, dict) else None
        if isinstance(identifiant, str) and identifiant.strip():
            noms.append(identifiant.strip())
    # Doublons possibles entre pages/entrées : on garde l'ordre, une seule fois.
    return list(dict.fromkeys(noms))


async def decouvrir_modeles(
    *,
    protocole: str,
    base_url: str | None,
    api_key: str | None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Liste les identifiants de modèles proposés par le fournisseur.

    Args:
        protocole: protocole du fournisseur (voir le tableau du module).
        base_url: URL enregistrée sur le fournisseur ; facultative partout, un
            endpoint public par défaut existe pour chaque protocole connu.
        api_key: clé **en clair**, utilisée le temps de l'appel ; jamais journalisée.

    Returns:
        Identifiants de modèles, dans l'ordre du fournisseur, sans doublon.

    Raises:
        DecouverteIndisponible: protocole sans découverte connue, réponse d'une
            forme inattendue, ou route absente du fournisseur (404/405).
        AgentConfigurationError: clé refusée (401/403) ou fournisseur injoignable
            — ce sont de vraies erreurs de configuration, pas une limite.
    """
    from app.agents.providers import AgentConfigurationError

    google = protocole == "google_genai"
    url = f"{_base_google(base_url)}/models" if google else _url_modeles(protocole, base_url)
    if url is None:
        raise DecouverteIndisponible(
            "La découverte automatique n'est pas disponible pour ce protocole : "
            "ajoutez les modèles à la main."
        )

    entetes = {"Accept": "application/json"}
    if api_key:
        # Gemini attend sa propre en-tête, pas un jeton Bearer (401 sinon).
        entetes["x-goog-api-key" if google else "Authorization"] = (
            api_key if google else f"Bearer {api_key}"
        )

    propre = client is None
    client = client or httpx.AsyncClient(timeout=DELAI_S)
    try:
        reponse = await client.get(url, headers=entetes)
    except httpx.HTTPError as exc:
        raise AgentConfigurationError(
            f"Fournisseur injoignable pour la découverte ({url}) : {type(exc).__name__}",
            details={"url": url},
        ) from exc
    finally:
        if propre:
            await client.aclose()

    if reponse.status_code in (404, 405):
        raise DecouverteIndisponible(
            f"Ce fournisseur n'expose pas de route de listage des modèles "
            f"({reponse.status_code} sur {url}). Ajoutez les modèles à la main."
        )
    if reponse.status_code in (401, 403):
        raise AgentConfigurationError(
            "Clé refusée par le fournisseur pour la découverte des modèles "
            f"({reponse.status_code}).",
            details={"url": url, "statut": reponse.status_code},
        )
    if reponse.status_code >= 400:
        raise AgentConfigurationError(
            f"Échec de la découverte des modèles ({reponse.status_code} sur {url}).",
            details={"url": url, "statut": reponse.status_code},
        )
    try:
        donnees = reponse.json()
    except ValueError as exc:
        raise DecouverteIndisponible(
            "La réponse du fournisseur n'est pas du JSON : la découverte n'est "
            "pas disponible sur cet endpoint. Ajoutez les modèles à la main."
        ) from exc
    return _identifiants_google(donnees) if google else _identifiants(donnees)


__all__ = ["DELAI_S", "DecouverteIndisponible", "decouvrir_modeles"]
