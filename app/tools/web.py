"""Recherche web pour agents (Lot 1, décision D4 : LangSearch).

``langsearch_web_search`` est un tool en lecture pure : il interroge l'API
LangSearch et renvoie des résultats **bornés** (``web_search_max_results``).
Aucun scraping ici : l'extraction contrôlée de pages sera un tool séparé,
restreint à une allowlist de domaines à confirmer avec CARSO (hors Lot 1).

La clé d'API est un ``SecretStr`` de ``Settings`` : elle ne fuit ni dans les
logs ni dans les prompts (instruction/08 §6). Si elle est absente, l'erreur
est explicite **à l'invocation** — jamais d'échec silencieux ni de démarrage
bloqué pour une option non utilisée.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.errors import ValidationError
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = ["WebSearchInput", "attacher_web_search", "build_web_search_tool"]

_MAX_QUERY_CHARS = 400
_TIMEOUT_HTTP = 15.0


class WebSearchInput(BaseModel):
    """Entrée typée alignée sur l'API officielle LangSearch (POST /v1/web-search)."""

    requete: str = Field(
        min_length=1,
        max_length=_MAX_QUERY_CHARS,
        description="Requête de recherche en langage naturel.",
    )
    max_results: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Nombre de résultats (serre la borne Settings, jamais l'inverse).",
    )
    freshness: str = Field(
        default="noLimit",
        description="noLimit | oneDay | oneWeek | oneMonth | oneYear | YYYY-MM-DD | plage.",
    )
    include_domains: list[str] | None = Field(
        default=None,
        description="Restreindre aux domaines (ex. service-public.ma).",
    )
    exclude_domains: list[str] | None = Field(
        default=None,
        description="Exclure des domaines.",
    )
    texte_complet: bool = Field(
        default=False,
        description="Si vrai : contents.text (contexte page, pas un scraping libre).",
    )
    max_caracteres: int = Field(
        default=3000,
        ge=200,
        le=5000,
        description="Plafond par résultat en mode texte (défaut API = 5000).",
    )


def build_web_search_tool(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    client: httpx.Client | None = None,
) -> Any:
    """Construit le ``TypedTool`` ``langsearch_web_search`` pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — la capacité ``web_search`` est exigée.
        settings: ``Settings`` (clé LangSearch, base URL, borne de résultats).
        client: ``httpx.Client`` injectable pour les tests (mock réseau) ;
            un client par défaut est créé sinon (timeout borné, pas de retry).

    Returns:
        Un ``TypedTool`` nommé ``langsearch_web_search`` (schéma ``WebSearchInput``).
    """
    cle = settings.langsearch_api_key
    base_url = settings.langsearch_base_url.rstrip("/")
    plafond = settings.web_search_max_results
    client_fourni = client is not None

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "web_search")

        # Clé absente => erreur explicite à l'invocation (pas de None fuyant).
        if cle is None or not cle.get_secret_value().strip():
            msg = (
                "langsearch_web_search indisponible : LANGSEARCH_API_KEY n'est pas "
                "configurée (SecretStr, cf. Settings) — active la recherche web "
                "est une décision explicite"
            )
            raise ValidationError(msg)

        # Plafond de Settings : le tool serre la borne, jamais l'inverse.
        demande = payload.get("max_results") or plafond
        nb = min(int(demande), plafond)

        # SecretStr : la clé est déballée uniquement pour l'en-tête HTTP.
        en_tetes = {
            "Authorization": f"Bearer {cle.get_secret_value()}",
            "Content-Type": "application/json",
        }
        # Endpoint officiel : POST /v1/web-search (tiret, docs.langsearch.com).
        freshness = payload.get("freshness") or "noLimit"
        corps: dict[str, Any] = {
            "query": payload["requete"],
            "count": nb,
            "freshness": freshness,
        }
        if payload.get("include_domains"):
            corps["includeDomains"] = list(payload["include_domains"])
        if payload.get("exclude_domains"):
            corps["excludeDomains"] = list(payload["exclude_domains"])
        if payload.get("texte_complet"):
            plafond_texte = min(
                int(payload.get("max_caracteres") or 3000),
                int(getattr(settings, "web_search_text_max_chars", 3000)),
            )
            corps["contents"] = {"text": {"max_characters": plafond_texte}}

        def _poster(cible: httpx.Client) -> httpx.Response:
            return cible.post(
                f"{base_url}/v1/web-search",
                headers=en_tetes,
                json=corps,
                timeout=_TIMEOUT_HTTP,
            )

        if client_fourni and client is not None:
            reponse = _poster(client)
        else:
            # Client éphémère par appel : pas d'état partagé à fermer, un agent
            # peut enchaîner plusieurs recherches sans ressource orpheline.
            with httpx.Client(timeout=_TIMEOUT_HTTP) as temporaire:
                reponse = _poster(temporaire)
        if reponse.status_code != 200:
            msg = f"LangSearch a répondu {reponse.status_code} (recherche web refusée)"
            raise ValidationError(msg, details={"status": reponse.status_code})

        donnees = json.loads(reponse.text)
        brut = donnees.get("data", {}).get("webPages", {}).get("value", [])
        resultats = []
        for item in brut[:nb]:
            resume = item.get("text") or item.get("snippet") or ""
            resultats.append(
                {
                    "titre": item.get("name") or "",
                    "url": item.get("url") or "",
                    "resume": resume,
                    "date_publication": item.get("datePublished"),
                }
            )
        usage = donnees.get("usage") or {}
        return {
            "requete": payload["requete"],
            "nb_resultats": len(resultats),
            "resultats": resultats,
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
            "log_id": donnees.get("log_id"),
        }

    from app.tools.base import TypedTool

    return TypedTool(
        name="langsearch_web_search",
        description=(
            "Recherche web LangSearch (POST /v1/web-search). Titre, URL, "
            "snippet ou texte de page (texte_complet), fraîcheur et filtres "
            "de domaines. Lecture seule : pas de scraping hors API, pas de "
            "clé dans la sortie. Source complémentaire, jamais la vérité métier."
        ),
        handler=_handler,
        input_schema=WebSearchInput,
        tags=("web", "recherche"),
    )


def attacher_web_search(
    definition: Any,
    extra: tuple[Any, ...],
    *,
    settings: Any | None = None,
) -> tuple[Any, ...]:
    """Ajoute ``langsearch_web_search`` si le contrat le déclare.

    Construction paresseuse : clé absente ⇒ le tool existe quand même et
    échoue explicitement à l'invocation (jamais un agent sans le tool).
    """
    if "langsearch_web_search" not in getattr(definition, "tools", ()):
        return extra
    from app.core.config import get_settings

    tool = build_web_search_tool(
        identite=AgentIdentity(agent_id=definition.agent_id),
        policy=definition.policy,
        settings=settings or get_settings(),
    )
    return extra + (tool,)
