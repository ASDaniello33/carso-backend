"""Configuration multi-provider des modèles de chat (Deep Agents).

Le provider est une décision de **déploiement**, pas de contrat métier—il est
lu dans la configuration runtime (``AGENT_PROVIDER`` / ``AGENT_MODEL`` / …) et
aucun agent ne le connaît. Changer de modèle ne modifie pas une ligne du code
métier (instruction/05 §3—le contrat de l'agent est indépendant du modèle).

Providers supportés :

| ``AGENT_PROVIDER`` | Implémentation | Variables requises |
| --- | --- | --- |
| ``openai`` | ``langchain-openai`` (``ChatOpenAI``) | ``AGENT_MODEL``, ``AGENT_API_KEY`` |
| ``google_genai`` | ``langchain-google-genai`` | ``AGENT_MODEL``, ``AGENT_API_KEY`` |
| ``openai_compatible`` | ``ChatOpenAI`` + ``base_url`` | idem + ``AGENT_BASE_URL`` |

Les paquets provider sont importés **paresseusement**—le backend démarre sans
eux, et une configuration incomplète produit une erreur explicite au moment de
construire un agent—jamais un modèle silencieusement dégradé.

Sécurité—``AGENT_API_KEY`` est un ``SecretStr`` et n'apparaît ni dans les
messages d'erreur, ni dans les logs, ni dans une ``AgentTask`` (instruction/08 §6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import CarsoError

_logger_agents = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

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



def _texte_des_blocs(contenu: list[Any]) -> str:
    """Texte d'un contenu multimodal, blocs non textuels ignorés.

    Le raisonnement (``reasoning``/``thinking``) n'est jamais renvoyé au
    fournisseur ; seuls les textes sont conservés, concaténés en une chaîne —
    l'API compatible OpenAI refuse un contenu en tableau.
    """
    textes: list[str] = []
    for bloc in contenu:
        if isinstance(bloc, str):
            textes.append(bloc)
            continue
        if not isinstance(bloc, dict):
            continue
        type_bloc = bloc.get("type")
        if type_bloc in ("reasoning", "thinking", "reasoning_content"):
            continue
        valeur = bloc.get("text")
        if not isinstance(valeur, str):
            valeur = bloc.get("content") if isinstance(bloc.get("content"), str) else None
        if isinstance(valeur, str):
            textes.append(valeur)
    return "".join(textes)


def _a_des_images(contenu: Any) -> bool:
    """Vrai si un contenu porte un bloc image."""
    return isinstance(contenu, list) and any(
        isinstance(bloc, dict) and bloc.get("type") in ("image_url", "image")
        for bloc in contenu
    )


def _contenu_sans_raisonnement(contenu: list[Any]) -> list[Any] | None:
    """Contenu multimodal conservé, débarrassé des blocs de raisonnement.

    Le raisonnement rejoué faisait répondre le modèle deux fois
    (``docs/validation/INC-140-MODE-RAISONNEMENT.md``) ; les images, elles, sont
    la raison d'être du contenu en tableau et doivent rester en place.

    Returns:
        Le contenu nettoyé, ou ``None`` s'il ne reste rien d'exploitable (auquel
        cas l'appelant retombe sur le texte).
    """
    types_ignores = ("reasoning", "thinking", "reasoning_content")
    gardes = [
        bloc
        for bloc in contenu
        if not (isinstance(bloc, dict) and bloc.get("type") in types_ignores)
    ]
    return gardes or None


#: Classes dérivées déjà construites, par classe de base : une sous-classe par
#: provider réellement importé, créée une seule fois par processus.
_CLASSES_NORMALISEES: dict[str, type] = {}


def _classe_contenu_texte(chat_openai: Any) -> type:
    """``ChatOpenAI`` qui retire le raisonnement rejoué sans perdre les images.

    Les passerelles compatibles OpenAI (logfare, vLLM…) refusent un contenu en
    tableau **porteur de blocs de raisonnement** : ``messages[].content`` partait
    alors en tableau et la passerelle répondait ``Expected string, received
    array`` — run en erreur (``docs/validation/INC-140-MODE-RAISONNEMENT.md``).

    Depuis le 09/11, la règle est plus fine : un contenu **avec image** reste un
    tableau (les blocs de raisonnement seuls sont retirés) — aplatir en chaîne
    supprimait l'image, et ``lire_image`` annonçait alors une vision que le
    modèle n'avait pas. Un contenu **sans image** est aplati en chaîne, comme
    avant : c'est la forme que ces passerelles acceptent.

    Le raisonnement n'est **ni demandé ni interprété** : le paramètre
    ``reasoning`` faisait répondre le modèle deux fois sur un même message
    (une salutation, puis une seconde réponse après un appel d'outil), sur tous
    les agents. Voir ``docs/validation/INC-142-RAISONNEMENT-MASQUE.md`` §6.
    """
    base = chat_openai.ChatOpenAI
    cle = f"{base.__module__}.{base.__qualname__}"
    existante = _CLASSES_NORMALISEES.get(cle)
    if existante is not None:
        return existante

    class ModeleContenuTexte(base):
        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            messages = payload.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    contenu = message.get("content")
                    if not isinstance(contenu, list):
                        continue
                    if _a_des_images(contenu):
                        # Tableau conservé : l'image est le motif du contenu
                        # multimodal. Seuls les blocs de raisonnement partent.
                        nettoye = _contenu_sans_raisonnement(contenu)
                        if nettoye is None:
                            message["content"] = _texte_des_blocs(contenu)
                        else:
                            message["content"] = nettoye
                        continue
                    message["content"] = _texte_des_blocs(contenu)
            return payload

    _CLASSES_NORMALISEES[cle] = ModeleContenuTexte
    return ModeleContenuTexte


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


#: Fenêtres **définitives** seulement, par ``(base_url, modèle)``. ``None`` y signifie
#: « le fournisseur a répondu, et ne publie pas de fenêtre pour ce modèle ».
#: Un échec de lecture n'y entre **jamais** : un blip réseau ne doit pas figer un
#: repli pour toute la vie du processus (voir ``_LectureFenetre``).
_FENETRES_PUBLIEES: dict[tuple[str, str], int | None] = {}

#: Délai de la lecture ``/models``. Court : c'est une métadonnée, pas un appel de
#: production — un fournisseur muet ne doit pas retarder la construction d'un
#: agent de plusieurs dizaines de secondes.
_TIMEOUT_METADONNEES = 6.0


@dataclass(frozen=True)
class _LectureFenetre:
    """Résultat d'une lecture de fenêtre : la valeur, et **sa solidité**.

    ``definitive`` distingue deux choses que le code confondait :

    - ``True`` — le fournisseur a répondu sur ce modèle : il publie
      ``context_length`` (``valeur``) ou ne le publie pas (``valeur=None``).
      Cela peut être mémorisé : relire ne donnera pas autre chose.
    - ``False`` — la lecture n'a pas abouti (réseau, HTTP, réponse illisible).
      Cela **ne doit pas** être mémorisé : un incident passager ferait croire
      à une absence définitive, et le modèle resterait sur le repli jusqu'au
      prochain redémarrage.

    ``raison`` porte le détail pour le journal, jamais la clé du fournisseur.
    """

    valeur: int | None
    definitive: bool
    raison: str | None = None


def _fenetre_publiee(settings: Settings, model: str) -> _LectureFenetre:
    """Fenêtre d'entrée annoncée par le fournisseur pour ce modèle.

    La passerelle compatible OpenAI publie ``context_length`` dans ``/models``
    (constaté le 15/11 : ``deepseek-v4.1-flash:free`` → 1 048 576 jetons,
    ``qwen3.8-27b`` → 1 000 000). C'est la **seule** source qui ne soit pas une
    supposition : un chiffre écrit à la main dans la configuration est une
    hypothèse sur un modèle qui peut changer.

    Seules les réponses **définitives** sont mises en cache ; une lecture qui
    échoue est retentée à la construction suivante. Un modèle absent de la liste
    n'est pas non plus mémorisé : la liste peut être partielle ou filtrée par
    l'abonnement, donc ce n'est pas une réponse définitive.

    Est définitive : une valeur publiée, un ``context_length`` explicitement
    ``null``, ou un point d'entrée ``/models`` absent (404/405/501) — un
    fournisseur qui n'expose pas ce chemin ne l'exposera pas davantage au
    prochain run.
    """
    if not settings.agent_base_url:
        return _LectureFenetre(None, False, "aucune AGENT_BASE_URL")
    cache = (settings.agent_base_url, model)
    if cache in _FENETRES_PUBLIEES:
        return _LectureFenetre(_FENETRES_PUBLIEES[cache], True)

    try:
        import httpx
    except ImportError as erreur:  # pragma: no cover - httpx vient avec langchain-openai
        return _LectureFenetre(None, False, type(erreur).__name__)

    cle = settings.agent_api_key.get_secret_value() if settings.agent_api_key else ""
    try:
        reponse = httpx.get(
            settings.agent_base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {cle}"},
            timeout=_TIMEOUT_METADONNEES,
            verify=settings.agent_ssl_verify,
        )
        reponse.raise_for_status()
        donnees = reponse.json()
    except httpx.HTTPStatusError as erreur:
        statut = erreur.response.status_code
        if statut in {404, 405, 501}:
            # Le fournisseur n'expose pas ce point d'entrée : définitif.
            _FENETRES_PUBLIEES[cache] = None
            return _LectureFenetre(None, True, f"/models absent (HTTP {statut})")
        return _LectureFenetre(None, False, f"HTTP {statut}")
    except Exception as erreur:  # noqa: BLE001 - métadonnée optionnelle
        return _LectureFenetre(None, False, type(erreur).__name__)

    liste = donnees.get("data") if isinstance(donnees, dict) else donnees
    for entree in liste if isinstance(liste, list) else []:
        if not isinstance(entree, dict) or entree.get("id") != model:
            continue
        publiee = entree.get("context_length")
        if isinstance(publiee, int) and not isinstance(publiee, bool):
            _FENETRES_PUBLIEES[cache] = publiee
            return _LectureFenetre(publiee, True)
        # Le modèle est listé et ne dit rien de sa fenêtre : définitif.
        _FENETRES_PUBLIEES[cache] = None
        return _LectureFenetre(None, True, "context_length absent ou null")

    # Modèle absent de la liste : non mémorisé (liste peut-être partielle).
    return _LectureFenetre(None, False, "modèle absent de la liste /models")


#: Provenances de la fenêtre retenue, telles que l'interface les distingue.
PROVENANCE_PUBLIEE = "publiee"
PROVENANCE_NON_PUBLIEE = "non_publiee"
PROVENANCE_NON_LUE = "non_lue"


def fenetre_effective(settings: Settings, model: str) -> dict[str, object]:
    """Fenêtre d'entrée **retenue** pour ce modèle, et d'où elle vient.

    C'est le seul endroit qui décide de la valeur utilisée : le calcul du profil
    et l'affichage en Paramètres lisent tous deux ce résultat, donc l'écran ne
    peut pas décrire autre chose que ce que le harnais applique.

    Returns:
        ``{"jetons": int, "provenance": str, "raison": str | None,
        "seuil_resume": int, "garde_resume": int}``. ``provenance`` vaut
        :data:`PROVENANCE_PUBLIEE` (le fournisseur annonce la fenêtre),
        :data:`PROVENANCE_NON_PUBLIEE` (il a répondu, il n'annonce rien) ou
        :data:`PROVENANCE_NON_LUE` (la lecture a échoué : l'absence de valeur
        ne prouve rien). ``seuil_resume`` et ``garde_resume`` sont les seuils
        effectifs du résumé d'historique, en jetons.
    """
    lecture = _fenetre_publiee(settings, model)
    if lecture.valeur is not None:
        provenance = PROVENANCE_PUBLIEE
        jetons = lecture.valeur
    elif lecture.definitive:
        provenance = PROVENANCE_NON_PUBLIEE
        jetons = settings.agent_max_input_tokens
    else:
        provenance = PROVENANCE_NON_LUE
        jetons = settings.agent_max_input_tokens
    return {
        "jetons": jetons,
        "provenance": provenance,
        "raison": lecture.raison,
        "seuil_resume": int(jetons * settings.agent_resume_historique_fraction),
        "garde_resume": int(
            jetons * settings.agent_resume_historique_fraction_gardee
        ),
    }


def _profil_modele(settings: Settings, model: str) -> dict[str, int]:
    """Profil de modèle : la fenêtre d'entrée, publiée par le fournisseur.

    Sans ``max_input_tokens``, LangChain **refuse** un seuil exprimé en fraction
    (``ValueError`` à la construction) et Deep Agents retombe sur son défaut
    absolu (170 000 jetons). La valeur vient de ``/models`` quand le fournisseur
    la publie ; ``AGENT_MAX_INPUT_TOKENS`` n'est qu'un **repli**, que le journal
    doit dire exactement pour ce qu'il est.

    Deux replis, deux messages — jamais confondus :

    - **non publiée** : le fournisseur a répondu, il n'annonce pas de fenêtre ;
    - **non lue** : la lecture a échoué (réseau, HTTP, réponse illisible), donc
      l'absence de valeur ne prouve rien sur le modèle et la lecture sera
      retentée au prochain montage d'agent.

    Les autres champs du profil (`tool_calling`, vision…) ne sont pas déclarés :
    les inventer ferait dire au harnais des capacités que le modèle n'a pas
    forcément — seule la fenêtre est nécessaire ici.
    """
    lecture = _fenetre_publiee(settings, model)
    if lecture.valeur is not None:
        _logger_agents.info(
            "Fenêtre d'entrée lue chez %s pour %r : %d jetons",
            settings.agent_base_url,
            model,
            lecture.valeur,
        )
        return {"max_input_tokens": lecture.valeur}

    if lecture.definitive:
        _logger_agents.warning(
            "Fenêtre d'entrée non publiée par %s pour %r (%s) : repli sur "
            "AGENT_MAX_INPUT_TOKENS=%d (hypothèse à vérifier)",
            settings.agent_base_url,
            model,
            lecture.raison or "aucune valeur annoncée",
            settings.agent_max_input_tokens,
        )
    else:
        _logger_agents.warning(
            "Fenêtre d'entrée NON LUE chez %s pour %r (%s) : repli sur "
            "AGENT_MAX_INPUT_TOKENS=%d ; la lecture sera retentée (ce repli "
            "n'est pas une valeur annoncée par le fournisseur)",
            settings.agent_base_url,
            model,
            lecture.raison or "cause inconnue",
            settings.agent_max_input_tokens,
        )
    return {"max_input_tokens": settings.agent_max_input_tokens}


def _build_openai(settings: Settings, *, model: str, api_key: str | None) -> Any:
    chat_openai = _require_package("langchain_openai", paquet="langchain-openai")
    classe = _classe_contenu_texte(chat_openai)
    # max_retries=2 : les agrégateurs renvoient parfois un flux SSE tronqué ou
    # non-JSON (JSONDecodeError dans openai._streaming) — un incident réseau, pas
    # une erreur métier. Le SDK OpenAI retente alors (2 essais, backoff expo),
    # ce qui résorbe la plupart de ces micro-coupures avant d'exposer l'utilisateur.
    modele = classe(
        max_retries=2,
        model=model,
        api_key=api_key,
        temperature=settings.agent_temperature,
        # Fenêtre d'entrée : les fractions du résumé d'historique (seuil 75 %,
        # conservation 10 %) en dépendent. Lue chez le fournisseur.
        profile=_profil_modele(settings, model),
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
    return modele


def _build_openai_compatible(settings: Settings, *, model: str, api_key: str | None) -> Any:
    """Endpoint compatible OpenAI (vLLM, passerelles maison, agrégateurs…)."""
    if not settings.agent_base_url:
        msg = (
            "AGENT_BASE_URL absent : un endpoint compatible OpenAI doit exposer "
            "son URL de base (ex: https://exemple.ai/v1)"
        )
        raise AgentConfigurationError(msg)

    chat_openai = _require_package("langchain_openai", paquet="langchain-openai")
    classe = _classe_contenu_texte(chat_openai)
    # Même justification que _build_openai : les passerelles compatibles OpenAI
    # coupent parfois le flux SSE en cours de réponse (JSONDecodeError côté SDK) —
    # 2 tentatives réseau absorbent l'incident sans exposer l'utilisateur.
    modele = classe(
        max_retries=2,
        model=model,
        base_url=settings.agent_base_url,
        api_key=api_key,
        temperature=settings.agent_temperature,
        # Fenêtre d'entrée : c'est elle qui donne un sens aux fractions du résumé
        # d'historique (75 % / 10 %) — sans profil, la bibliothèque refuse ces
        # seuils et retombe sur un défaut absolu. Lue chez le fournisseur.
        profile=_profil_modele(settings, model),
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
    return modele


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
