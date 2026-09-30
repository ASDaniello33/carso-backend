"""Configuration multi-provider des modèles de chat (Deep Agents).

Le provider est une décision de **déploiement**, pas de contrat métier : il est
lu dans la configuration runtime (``AGENT_PROVIDER`` / ``AGENT_MODEL`` / …) et
aucun agent ne le connaît. Changer de modèle ne modifie pas une ligne du code
métier (instruction/05 §3 : le contrat de l'agent est indépendant du modèle).

Providers supportés :

| ``AGENT_PROVIDER`` | Implémentation | Variables requises |
| --- | --- | --- |
| ``openai`` | ``langchain-openai`` (``ChatOpenAI``) | ``AGENT_MODEL``, ``AGENT_API_KEY`` |
| ``google_genai`` | ``langchain-google-genai`` | ``AGENT_MODEL``, ``AGENT_API_KEY`` |
| ``openai_compatible`` | ``ChatOpenAI`` + ``base_url`` | idem + ``AGENT_BASE_URL`` |

Les paquets provider sont importés **paresseusement** : le backend démarre sans
eux, et une configuration incomplète produit une erreur explicite au moment de
construire un agent — jamais un modèle silencieusement dégradé.

Sécurité : ``AGENT_API_KEY`` est un ``SecretStr`` et n'apparaît ni dans les
messages d'erreur, ni dans les logs, ni dans une ``AgentTask`` (instruction/08 §6).
"""

from __future__ import annotations

from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import CarsoError

SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"openai", "google_genai", "openai_compatible"})

_ERROR_MISSING_PACKAGE = (
    "Le paquet {paquet} n'est pas installé : installer l'extra agents "
    "(pip install -e '.[agents]')"
)

#: Garde de l'avertissement TLS : émis une seule fois par processus.
_tls_avertissement_emis: bool = False


class AgentConfigurationError(CarsoError):
    """Configuration d'agent inutilisable (provider, modèle ou clé manquants).

    Volontairement une erreur serveur (500) : ce n'est pas une faute de
    l'appelant mais un déploiement incomplet.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)


class ChatModelBuilder(Protocol):
    """Contrat d'un builder de modèle, par provider."""

    def __call__(self, settings: Settings, *, model: str, api_key: str | None) -> Any: ...


def valider_parametres(
    *, provider: str | None, model: str | None, base_url: str | None
) -> tuple[str, str, str | None]:
    """Valide une configuration *sans* construire de modèle.

    Utilisée par les services/API d'administration avant de persister une
    configuration : une valeur refusée ici ne doit jamais atteindre la base.

    Returns:
        Le triplet normalisé ``(provider, model, base_url)``.

    Raises:
        AgentConfigurationError: provider inconnu, modèle absent, ou
            ``base_url`` absente pour un endpoint compatible OpenAI.
    """
    provider_n = (provider or "").strip().lower()
    model_n = (model or "").strip()
    base_url_n = (base_url or "").strip() or None

    if provider_n not in SUPPORTED_PROVIDERS:
        msg = (
            f"AGENT_PROVIDER inconnu : {provider_n!r} "
            f"(providers supportés : {', '.join(sorted(SUPPORTED_PROVIDERS))})"
        )
        raise AgentConfigurationError(
            msg, details={"providers": sorted(SUPPORTED_PROVIDERS)}
        )
    if not model_n:
        raise AgentConfigurationError("AGENT_MODEL obligatoire")
    if provider_n == "openai_compatible" and not base_url_n:
        msg = (
            "AGENT_BASE_URL obligatoire pour un endpoint compatible OpenAI "
            "(ex: https://exemple.ai/v1)"
        )
        raise AgentConfigurationError(msg)

    return provider_n, model_n, base_url_n


async def tester_configuration(
    *,
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    settings: Settings | None = None,
) -> str:
    """Interroge réellement le modèle candidat et rend sa réponse.

    Un administrateur qui change de modèle et de clé doit savoir **tout de suite**
    si l'appel aboutit. Enregistrer une configuration non testée puis découvrir
    l'échec au premier run d'agent serait une perte de temps et une fausse
    impression de réussite.

    Le message envoyé est minimal (aucune donnée métier, aucun secret) et la
    réponse est tronquée : ce test ne coûte qu'un aller-retour.

    Returns:
        Le début de la réponse du modèle (preuve qu'il a répondu).

    Raises:
        AgentConfigurationError: configuration inutilisable ou paquet absent.
    """
    reglages = (settings or get_settings()).model_copy(
        update={
            "agent_provider": provider,
            "agent_model": model,
            "agent_base_url": base_url,
            "agent_temperature": 0.0,
        }
    )
    modele = build_chat_model(reglages, api_key=api_key or None)
    reponse = await modele.ainvoke(
        "Réponds uniquement par le mot : CARSO"
    )
    contenu = getattr(reponse, "content", None)
    if isinstance(contenu, list):  # certains modèles rendent des blocs
        contenu = " ".join(
            str(bloc.get("text", "")) if isinstance(bloc, dict) else str(bloc)
            for bloc in contenu
        )
    return str(contenu or "").strip()[:200]


def is_agent_configured(settings: Settings | None = None) -> bool:
    """Vrai si un modèle d'agent est exploitable avec la configuration courante.

    Sert à choisir entre le chemin *deep agent* (LLM) et le chemin déterministe
    hors ligne, sans jamais échouer au démarrage de l'application.
    """
    reglages = settings or get_settings()
    # Normalisation identique à ``build_chat_model`` pour que les deux
    # fonctions jugent la même configuration (casse/espaces inclus).
    provider = (reglages.agent_provider or "").strip().lower()
    if not (reglages.agent_model or "").strip():
        return False
    if provider == "openai_compatible":
        return bool(reglages.agent_base_url and reglages.agent_api_key)
    if provider in SUPPORTED_PROVIDERS:
        return bool(reglages.agent_api_key)
    return False


def build_chat_model(
    settings: Settings | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> Any:
    """Construit le modèle de chat selon le provider configuré.

    Args:
        settings: configuration (par défaut : ``get_settings()``).
        model: surcharge du nom de modèle (utile pour un test ou un agent
            utilisant un modèle différent).
        api_key: clé explicite (clé administrée déchiffrée). Sans elle, la clé du
            ``.env`` est utilisée. Elle n'est jamais journalisée ni renvoyée.

    Returns:
        Une instance ``BaseChatModel`` prête pour ``create_deep_agent``.

    Raises:
        AgentConfigurationError: provider inconnu, modèle absent, clé absente,
            ``base_url`` absente pour un endpoint compatible, ou paquet provider
            non installé.
    """
    reglages = settings or get_settings()
    provider = (reglages.agent_provider or "").strip().lower()
    nom_modele = (model or reglages.agent_model or "").strip()
    cle: str | None = api_key or (
        reglages.agent_api_key.get_secret_value() if reglages.agent_api_key else None
    )

    if provider not in SUPPORTED_PROVIDERS:
        msg = (
            f"AGENT_PROVIDER inconnu : {provider!r} "
            f"(providers supportés : {', '.join(sorted(SUPPORTED_PROVIDERS))})"
        )
        raise AgentConfigurationError(msg, details={"providers": sorted(SUPPORTED_PROVIDERS)})

    if not nom_modele:
        msg = "AGENT_MODEL absent : renseigner le modèle dans backend/.env"
        raise AgentConfigurationError(msg)

    if not cle:
        msg = (
            "Clé du provider absente : la renseigner dans backend/.env "
            "(AGENT_API_KEY) ou depuis la page Paramètres"
        )
        raise AgentConfigurationError(msg)

    constructeurs: dict[str, ChatModelBuilder] = {
        "openai": _build_openai,
        "openai_compatible": _build_openai_compatible,
        "google_genai": _build_google_genai,
    }
    return constructeurs[provider](reglages, model=nom_modele, api_key=cle)


# --- Providers ---------------------------------------------------------------


def _client_httpx(verifier_ssl: bool, *, asynchrone: bool = False) -> Any:
    """Client httpx pour le provider, avec la politique TLS configurée.

    Un seul client par appel (durée de vie = celle du modèle construit) : les
    agents sont construits une fois par run, le coût est négligeable.
    """
    import httpx

    if verifier_ssl:
        return httpx.AsyncClient() if asynchrone else httpx.Client()
    # AGENT_SSL_VERIFY=false : certificat du provider non vérifié. L'avertis-
    # sement est émis **une fois par processus** (il accompagne chaque client
    # httpx, soit deux par modèle construit) — visible, jamais silencieux,
    # mais sans noyer les logs au fil des rechargements du runtime.
    global _tls_avertissement_emis
    if not _tls_avertissement_emis:
        _tls_avertissement_emis = True
        import logging

        logging.getLogger(__name__).warning(
            "AGENT_SSL_VERIFY=false : le certificat TLS du provider LLM n'est "
            "pas vérifié (environnements de développement uniquement)."
        )
    return (
        httpx.AsyncClient(verify=False)
        if asynchrone
        else httpx.Client(verify=False)
    )


def _build_openai(settings: Settings, *, model: str, api_key: str | None) -> Any:
    chat_openai = _require_package("langchain_openai", paquet="langchain-openai")
    # max_retries=2 : les agrégateurs renvoient parfois un flux SSE tronqué ou
    # non-JSON (JSONDecodeError dans openai._streaming) — un incident réseau, pas
    # une erreur métier. Le SDK OpenAI retente alors (2 essais, backoff expo),
    # ce qui résorbe la plupart de ces micro-coupures avant d'exposer l'utilisateur.
    return chat_openai.ChatOpenAI(
        max_retries=2,
        model=model,
        api_key=api_key,
        temperature=settings.agent_temperature,
        # Modèles raisonneurs (grok, o1…) : de longues pauses sans chunk
        # pendant le raisonnement interne — le défaut de la lib (120 s) est
        # trop court et interrompt des runs valides.
        stream_chunk_timeout=settings.agent_stream_chunk_timeout,
        # Vérification TLS du provider (AGENT_SSL_VERIFY) via le client httpx :
        # défaut sécurisé, désactivable explicitement pour un agrégateur à
        # certificat non valide (expiré ou auto-signé).
        http_client=_client_httpx(settings.agent_ssl_verify),
        http_async_client=_client_httpx(settings.agent_ssl_verify, asynchrone=True),
    )


def _build_openai_compatible(settings: Settings, *, model: str, api_key: str | None) -> Any:
    """Endpoint compatible OpenAI (vLLM, passerelles maison, agrégateurs…)."""
    if not settings.agent_base_url:
        msg = (
            "AGENT_BASE_URL absent : un endpoint compatible OpenAI doit exposer "
            "son URL de base (ex: https://exemple.ai/v1)"
        )
        raise AgentConfigurationError(msg)

    chat_openai = _require_package("langchain_openai", paquet="langchain-openai")
    # Même justification que _build_openai : les passerelles compatibles OpenAI
    # coupent parfois le flux SSE en cours de réponse (JSONDecodeError côté SDK) —
    # 2 tentatives réseau absorbent l'incident sans exposer l'utilisateur.
    return chat_openai.ChatOpenAI(
        max_retries=2,
        model=model,
        base_url=settings.agent_base_url,
        api_key=api_key,
        temperature=settings.agent_temperature,
        # Modèles raisonneurs (grok, o1…) : de longues pauses sans chunk
        # pendant le raisonnement interne — le défaut de la lib (120 s) est
        # trop court et interrompt des runs valides.
        stream_chunk_timeout=settings.agent_stream_chunk_timeout,
        # Vérification TLS du provider (AGENT_SSL_VERIFY) via le client httpx :
        # défaut sécurisé, désactivable explicitement pour un agrégateur à
        # certificat non valide (expiré ou auto-signé).
        http_client=_client_httpx(settings.agent_ssl_verify),
        http_async_client=_client_httpx(settings.agent_ssl_verify, asynchrone=True),
    )


def _build_google_genai(settings: Settings, *, model: str, api_key: str | None) -> Any:
    google = _require_package("langchain_google_genai", paquet="langchain-google-genai")
    return google.ChatGoogleGenerativeAI(
        model=model,
        api_key=api_key,
        temperature=settings.agent_temperature,
    )


def _require_package(module_name: str, *, paquet: str) -> Any:
    """Importe un paquet provider ou échoue explicitement."""
    from importlib import import_module

    try:
        return import_module(module_name)
    except ImportError as exc:
        raise AgentConfigurationError(
            _ERROR_MISSING_PACKAGE.format(paquet=paquet), details={"paquet": paquet}
        ) from exc


__all__ = [
    "SUPPORTED_PROVIDERS",
    "AgentConfigurationError",
    "build_chat_model",
    "is_agent_configured",
    "tester_configuration",
    "valider_parametres",
]
