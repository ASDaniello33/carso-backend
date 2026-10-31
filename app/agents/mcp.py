"""Façade synchrone des connecteurs MCP pour les agents (``langchain-mcp-adapters``).

Pourquoi une façade et pas un simple appel—la construction des graphes
d'agents est **synchrone** (``create_deep_agent`` est appelé au montage du
runtime, pas dans une coroutine), alors que le client MCP est **asynchrone**. On
ne peut ni bloquer la boucle d'événements de FastAPI, ni demander au harnais de
devenir asynchrone pour un connecteur optionnel. Un fil dédié porte donc une
boucle d'événements persistante—``get_tools()`` y est exécuté et le résultat
est rendu au fil appelant.

Ce que le module garantit :

- **par agent**—seuls les connecteurs actifs qui autorisent l'agent sont
  chargés. Un agent ne reçoit jamais les outils d'un serveur qu'il n'a pas au
  contrat de connecteurs ;
- **paquet absent ⇒ dégradation explicite**—sans ``langchain-mcp-adapters``,
  le backend démarre normalement et l'agent fonctionne sans outils MCP ; le
  manque est journalisé, jamais silencieux ;
- **échec d'un connecteur ⇒ les autres survivent**—un serveur injoignable ne
  prive pas l'agent de ses autres outils distants ;
- **en-têtes déchiffrés ici seulement**—ils ne quittent jamais ce module sous
  forme lisible (ni logs, ni prompts, ni ``AgentTask``) ;
- **panne d'un outil distant = résultat d'outil, jamais exception**—les
  pannes de transport, de session et de conversion remontent en exception chez
  ``langchain-mcp-adapters`` (seule une erreur ``isError=True`` du serveur est
  convertie en Tool Message). Non traitées, elles interrompent le workflow
  entier—d'où l'enveloppe ``proteger_outil_mcp``—le modèle reçoit le détail
  de l'échec et poursuit sa tâche en le signalant ;
- **nom d'outil conforme à la grammaire du fournisseur**—le préfixe ajouté par
  l'adaptateur reprend le nom **affiché** du connecteur (« Parallel Search »),
  espaces compris. Or une API OpenAI-compatible exige
  ``^[a-zA-Z0-9_.-]+$`` et rejette alors **toute la requête** (donc tout le
  run), pas seulement l'outil fautif. Le nom exposé au modèle est normalisé ici
  (:func:`nom_outil_conforme`), le nom lisible restant seul utilisé dans les
  journaux et les payloads d'erreur.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.domain.mcp import ServeurMcp

_logger = logging.getLogger(__name__)

#: Cache des outils par (agent, signature des connecteurs) : reconstruire les
#: graphes ne doit pas relancer un serveur ``stdio`` à chaque fois.
_CACHE: dict[tuple[str, str], tuple[Any, ...]] = {}
_VERROU = threading.RLock()

#: Délais du transport MCP repris à l'identique quand une politique TLS impose
#: un client httpx maison (30 s écriture, 300 s lecture — valeurs documentées
#: par le SDK ; tant qu'aucune politique n'est posée, c'est la fabrique du SDK
#: qui est rendue telle quelle).
_TIMEOUT_MCP = 30.0
_TIMEOUT_MCP_LECTURE = 300.0
#: Une seule fois par processus (cf. ``_avertir_tls_desactive``).
_tls_avertissement_emis = False

_boucle: asyncio.AbstractEventLoop | None = None
_fil: threading.Thread | None = None
_verrou_boucle = threading.Lock()

#: Grammaire imposée aux noms d'outils par les API OpenAI-compatibles. Un nom
#: hors motif fait rejeter **la requête entière** (donc le run), pas seulement
#: l'outil : la normalisation est une garantie de disponibilité, pas du confort.
MOTIF_NOM_OUTIL = re.compile(r"[^a-zA-Z0-9_.-]")

#: Longueur maximale d'un nom de fonction côté fournisseur.
LIMITE_NOM_OUTIL = 64

#: Nom de repli quand un libellé ne contient aucun caractère exploitable.
NOM_OUTIL_DEFAUT = "outil_mcp"


def paquet_disponible() -> bool:
    """Vrai si ``langchain-mcp-adapters`` est installé."""
    try:
        import langchain_mcp_adapters.client  # noqa: F401
    except ImportError:
        return False
    return True


def _boucle_dediee() -> asyncio.AbstractEventLoop:
    """Boucle d'événements persistante sur un fil dédié (créée une fois)."""
    global _boucle, _fil
    with _verrou_boucle:
        if _boucle is not None and _boucle.is_running():
            return _boucle

        pret = threading.Event()

        def _servir() -> None:
            global _boucle
            boucle = asyncio.new_event_loop()
            asyncio.set_event_loop(boucle)
            _boucle = boucle
            pret.set()
            boucle.run_forever()

        _fil = threading.Thread(
            target=_servir, name="carso-mcp", daemon=True
        )
        _fil.start()
        pret.wait(timeout=5)
        if _boucle is None:  # pragma: no cover - dépend de l'OS
            raise RuntimeError("Boucle MCP indisponible")
        return _boucle


def _executer(coroutine: Any) -> Any:
    """Exécute une coroutine sur la boucle dédiée et rend son résultat."""
    return asyncio.run_coroutine_threadsafe(coroutine, _boucle_dediee()).result()


def _texte_court(valeur: Any, limite: int = 300) -> str:
    """Texte d'une valeur, espaces repliés et longueur bornée (log, payload)."""
    texte = " ".join(str(valeur).split())
    return texte[:limite] + ("…" if len(texte) > limite else "")


def _causes(exc: BaseException, limite: int = 3) -> list[str]:
    """Cause(s) embarquée(s) d'une exception, de la plus proche à la plus loin.

    Les pannes de transport (``httpx``, ``mcp``) enveloppent souvent la cause
    réelle (DNS, TLS, connexion refusée, session expirée) : le modèle reçoit la
    chaîne, pas seulement le type de surface.
    """
    vues: list[str] = []
    courant = exc.__cause__ or exc.__context__
    while courant is not None and len(vues) < limite:
        vues.append(f"{type(courant).__name__}: {_texte_court(courant, 200)}")
        courant = courant.__cause__ or courant.__context__
    return vues


def nom_outil_conforme(nom: str, *, defaut: str = NOM_OUTIL_DEFAUT) -> str:
    """Nom d'outil exposable à un fournisseur de modèle (``^[a-zA-Z0-9_.-]+$``).

    Un nom déjà conforme est rendu **inchangé** : la normalisation ne renomme
    jamais un outil sain (contrat des tests et des graphes existants).

    - tout caractère hors motif devient ``_`` (espace, accent, parenthèse, « / ») ;
    - les séries de ``_`` sont repliées et les points de bord retirés — le nom
      reste lisible (``Parallel Search_web_search`` → ``Parallel_Search_web_search``) ;
    - un libellé entièrement consommé retombe sur ``defaut`` plutôt que de
      produire un nom vide ;
    - au-delà de :data:`LIMITE_NOM_OUTIL`, troncature **avec empreinte courte** :
      deux noms longs différents ne se confondent pas, et le résultat est stable
      (même entrée, même nom — condition du cache des outils).
    """
    # Accents retirés plutôt que remplacés : un connecteur « Recherche météo »
    # doit rester lisible (`Recherche_meteo`), pas devenir `Recherche_m_t_o`.
    sans_accents = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", str(nom))
        if not unicodedata.combining(caractere)
    )
    propre = MOTIF_NOM_OUTIL.sub("_", sans_accents.strip())
    propre = re.sub(r"_{2,}", "_", propre).strip(".")
    if not propre:
        return defaut
    if len(propre) > LIMITE_NOM_OUTIL:
        empreinte = sha256(propre.encode("utf-8")).hexdigest()[:8]
        propre = f"{propre[: LIMITE_NOM_OUTIL - 9]}_{empreinte}"
    return propre


def nom_outil_unique(nom: str, pris: Iterable[str]) -> str:
    """Nom conforme libre dans ``pris``, suffixé si nécessaire.

    Deux outils distincts peuvent se normaliser pareil (« web search » et
    « web_search »), deux connecteurs porter des noms qui ne diffèrent que par
    un espace. LangGraph refuse deux outils de même nom : le suffixe garde les
    deux capacités au lieu d'en perdre une.
    """
    occupes = set(pris)
    if nom not in occupes:
        return nom
    troncature = LIMITE_NOM_OUTIL - 5  # place pour « _999 »
    base = nom[:troncature] if len(nom) > troncature else nom
    for rang in range(2, 1000):
        candidat = f"{base}_{rang}"
        if candidat not in occupes:
            return candidat
    empreinte = sha256(nom.encode("utf-8")).hexdigest()[:8]
    return f"{base[: LIMITE_NOM_OUTIL - 9]}_{empreinte}"


def payload_erreur_mcp(outil: str, serveur: str, exc: BaseException) -> str:
    """Échec d'un outil MCP rendu au modèle — **jamais** une exception.

    Même forme que ``formater_erreur_outil`` (``{ok: false, error: …}``) : le
    modèle reconnaît un résultat d'outil en erreur, en tient compte dans sa
    réponse et ne réessaie pas indéfiniment un connecteur mort. Aucun secret
    n'est inclus (ni en-tête, ni jeton) : seulement le type et le message.
    """
    charge: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": "mcp_tool_error",
            "message": (
                f"L'outil MCP « {outil} » (connecteur « {serveur} ») a échoué : "
                f"{type(exc).__name__} : {_texte_court(exc, 400)}"
            ),
            "details": {
                "outil": outil,
                "serveur": serveur,
                "type_erreur": type(exc).__name__,
                "causes": _causes(exc),
                "conseil": (
                    "Connecteur distant injoignable ou appel refusé : poursuis la "
                    "tâche sans cet outil, signale explicitement l'échec et "
                    "n'invente aucune donnée qu'il devait fournir. Ne le rappelle "
                    "pas en boucle."
                ),
            },
        },
    }
    return json.dumps(charge, ensure_ascii=False)


def _contenu_outil(resultat: Any) -> str:
    """Contenu textuel d'un outil MCP (ils sont ``content_and_artifact``)."""
    if isinstance(resultat, tuple) and len(resultat) == 2:
        resultat = resultat[0]
    if isinstance(resultat, str):
        return resultat
    return json.dumps(resultat, ensure_ascii=False, default=str)


def proteger_outil_mcp(outil: Any, *, serveur: str, nom: str | None = None) -> Any:
    """Enveloppe un outil MCP : toute exception devient un résultat pour le modèle.

    Trois garanties, chacune corrige une panne constatée :

    1. **Aucune panne distante ne casse le run.** ``langchain-mcp-adapters`` ne
       convertit en Tool Message que les erreurs d'exécution déclarées par le
       serveur (``CallToolResult(isError=True)``) ; les pannes de transport, de
       session et de conversion **remontent toujours en exception**. LangGraph
       interrompt alors le workflow : l'utilisateur perd le run en cours pour un
       connecteur optionnel. Ici, l'échec devient un payload lisible que le
       modèle lit et signale.
    2. **Invocation synchrone possible.** Les outils MCP sont *async-only* ; un
       graphe invoqué hors coroutine levait ``NotImplementedError:
       StructuredTool does not support sync invocation``. Le pont synchrone
       réutilise la boucle MCP dédiée (mêmes garde-fous, mêmes payloads).
    3. **Nom exposable au fournisseur.** Le préfixe de l'adaptateur reprend le
       libellé du connecteur ; un espace y suffit pour que l'API rejette la
       requête entière (``Invalid 'tools[N].function.name'``, constaté le
       31/10). Le nom effectif passe par :func:`nom_outil_conforme` — y compris
       quand l'appelant le fournit (``nom``), pour qu'aucun chemin ne puisse
       réinjecter un nom invalide.

    La description et le schéma d'entrée de l'outil d'origine sont conservés :
    le modèle voit le même contrat, sous un nom que le fournisseur accepte.
    """
    from langchain_core.tools import StructuredTool

    nom = nom_outil_conforme(nom or str(getattr(outil, "name", NOM_OUTIL_DEFAUT)))
    description = str(getattr(outil, "description", "") or "")
    schema = getattr(outil, "args_schema", None) or {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }

    async def _appeler(**arguments: Any) -> str:
        try:
            resultat = await outil.ainvoke(arguments)
        except Exception as exc:  # noqa: BLE001 - le run ne doit pas en mourir
            _logger.warning(
                "Outil MCP %s (connecteur %s) en échec : %s: %s",
                nom,
                serveur,
                type(exc).__name__,
                _texte_court(exc, 200),
            )
            return payload_erreur_mcp(nom, serveur, exc)
        return _contenu_outil(resultat)

    def _appeler_sync(**arguments: Any) -> str:
        try:
            return _executer(_appeler(**arguments))
        except Exception as exc:  # noqa: BLE001 - pont best-effort, même contrat
            _logger.warning(
                "Outil MCP %s (connecteur %s) injoignable en synchrone : %s: %s",
                nom,
                serveur,
                type(exc).__name__,
                _texte_court(exc, 200),
            )
            return payload_erreur_mcp(nom, serveur, exc)

    return StructuredTool.from_function(
        func=_appeler_sync,
        coroutine=_appeler,
        name=nom,
        description=description,
        args_schema=schema,
        metadata=getattr(outil, "metadata", None),
    )


def soumettre(coroutine: Any) -> Any:
    """Planifie une coroutine sur la boucle MCP **sans attendre** son résultat.

    Même boucle dédiée que ``_executer``, mais non bloquante : le flux OAuth doit
    publier son URL pendant que la route HTTP qui l'a déclenché rend la main.
    """
    return asyncio.run_coroutine_threadsafe(coroutine, _boucle_dediee())


def motif_erreur(exc: BaseException) -> str:
    """Motif lisible d'une panne, groupes d'exceptions **dépliés**.

    ``anyio`` enveloppe les erreurs de transport dans un ``BaseExceptionGroup``
    dont le message (« unhandled errors in a TaskGroup ») ne dit rien : la cause
    réelle (401, DNS, TLS, refus) est à l'intérieur. Un opérateur qui lit
    « injoignable » sans motif ne peut pas distinguer un jeton absent d'un
    serveur éteint — constaté le 05/10 sur un serveur exigeant OAuth
    (``docs/validation/VERIFICATION-MCP-OAUTH.md``).
    """
    vues: list[str] = []

    def _parcourir(courant: BaseException, profondeur: int = 0) -> None:
        if profondeur > 5 or len(vues) >= 3:
            return
        sous = getattr(courant, "exceptions", None)
        if sous:
            for element in sous:
                _parcourir(element, profondeur + 1)
            return
        reponse = getattr(courant, "response", None)
        statut = getattr(reponse, "status_code", None)
        if statut is not None:
            url = getattr(getattr(reponse, "request", None), "url", "")
            vues.append(f"{type(courant).__name__} HTTP {statut} {url}".strip())
            return
        vues.append(f"{type(courant).__name__}: {_texte_court(courant, 200)}")

    _parcourir(exc)
    return " | ".join(vues) or type(exc).__name__


def politique_tls_mcp(reglages: Any | None = None) -> bool | str:
    """Ce que la vérification TLS des connecteurs MCP exige.

    Returns:
        ``True`` (vérification stricte, défaut), un **chemin** de certificat ou
        de dossier à ajouter aux autorités de confiance
        (``MCP_SSL_CA_BUNDLE``), ou ``False`` quand la vérification est
        explicitement désactivée (``MCP_SSL_VERIFY=false``).

    Un connecteur personnel hébergé derrière un certificat auto-signé (cas d'une
    passerelle MCP maison en https) ne peut pas être joint autrement : le SDK
    MCP, comme httpx, refuse la poignée de main avec
    ``CERTIFICATE_VERIFY_FAILED: self-signed certificate``. Le projet ne
    contourne jamais cette vérification en silence — les deux réglages sont des
    décisions de déploiement, et la seconde est journalisée.
    """
    if reglages is None:
        from app.core.config import get_settings

        reglages = get_settings()
    bundle = (getattr(reglages, "mcp_ssl_ca_bundle", None) or "").strip()
    if bundle:
        return bundle
    if not getattr(reglages, "mcp_ssl_verify", True):
        _avertir_tls_desactive()
        return False
    return True


def _avertir_tls_desactive() -> None:
    """Avertissement **une fois par processus** : la désactivation est visible."""
    global _tls_avertissement_emis
    with _VERROU:
        if _tls_avertissement_emis:
            return
        _tls_avertissement_emis = True
    _logger.warning(
        "MCP_SSL_VERIFY=false : le certificat TLS des connecteurs MCP n'est pas "
        "vérifié (développement uniquement). Préférer MCP_SSL_CA_BUNDLE."
    )


def fabrique_client_mcp(reglages: Any | None = None) -> Any:
    """Fabrique de client httpx des transports réseau MCP, TLS comprise.

    Rendue **telle quelle** par le SDK tant que la politique est la sienne
    (vérification stricte) : rien n'est réimplémenté pour rien. Dès qu'une
    politique TLS est configurée, la même forme est reproduite — délais du
    transport MCP compris (30 s en écriture, 300 s en lecture, valeurs
    documentées par le SDK) — et seuls ``verify`` et donc la confiance changent.
    """
    from mcp.client.streamable_http import create_mcp_http_client

    verification = politique_tls_mcp(reglages)
    if verification is True:
        return create_mcp_http_client

    def _client(headers: Any = None, timeout: Any = None, auth: Any = None) -> Any:
        import httpx

        options: dict[str, Any] = {
            "verify": _verification_httpx(verification),
            "timeout": timeout
            if timeout is not None
            else httpx.Timeout(_TIMEOUT_MCP, read=_TIMEOUT_MCP_LECTURE),
        }
        if headers is not None:
            options["headers"] = headers
        if auth is not None:
            options["auth"] = auth
        return httpx.AsyncClient(**options)

    return _client


def _verification_httpx(verification: bool | str) -> Any:
    """Politique CARSO → argument ``verify`` de httpx.

    Un chemin devient un **contexte SSL** plutôt qu'une chaîne : ``verify=<str>``
    est déprécié par httpx, et un contexte dit exactement ce que l'on accepte — ce
    certificat (fichier PEM) ou ce dossier de certificats.
    """
    import ssl
    from pathlib import Path

    from app.core.errors import ValidationError

    if verification is False:
        return False
    chemin = Path(str(verification)).expanduser()
    if not chemin.exists():
        # Un chemin faux doit se lire dans la sonde du connecteur, pas se
        # présenter comme une panne de la passerelle.
        raise ValidationError(
            "MCP_SSL_CA_BUNDLE introuvable : indiquer un certificat (PEM) ou un "
            "dossier de certificats existant",
            details={"chemin": str(chemin)},
        )
    if chemin.is_dir():
        return ssl.create_default_context(capath=str(chemin))
    return ssl.create_default_context(cafile=str(chemin))


def _connexion(serveur: ServeurMcp, *, attente: Any | None = None) -> dict[str, Any]:
    """Configuration d'un connecteur au format attendu par les adaptateurs.

    Trois modes d'authentification (:mod:`app.domain.mcp`) :

    - ``aucune`` : rien n'est transmis ;
    - ``en_tetes`` : en-têtes déchiffrés ici seulement ;
    - ``oauth`` : un ``httpx.Auth`` OAuth est branché sur le transport. Sans
      ``attente``, il n'ouvre **jamais** de navigateur : il consomme les jetons
      stockés et les rafraîchit. Le consentement interactif n'existe que dans
      ``charger_avec_consentement``.

    La fabrique de client est posée pour **les deux** modes réseau : la politique
    TLS (``MCP_SSL_CA_BUNDLE`` / ``MCP_SSL_VERIFY``) vaut avant l'authentification,
    puisqu'elle s'applique à la poignée de main elle-même.
    """
    from app.application.services.parametres_service import ServeurMcpService

    if serveur.transport == "stdio":
        return {
            "transport": "stdio",
            "command": serveur.commande,
            "args": list(serveur.arguments or []),
        }
    connexion: dict[str, Any] = {
        "transport": serveur.transport,
        "url": serveur.url,
        "httpx_client_factory": fabrique_client_mcp(),
    }
    if getattr(serveur, "mode_authentification", "en_tetes") == "oauth":
        from app.application.services.mcp_oauth_service import fournisseur_oauth

        connexion["auth"] = fournisseur_oauth(serveur, attente=attente)
        return connexion
    entetes = ServeurMcpService.en_tetes(serveur)
    if entetes:
        connexion["headers"] = entetes
    return connexion


def empreinte_credentials(serveur: ServeurMcp) -> str:
    """Empreinte **des credentials** d'un connecteur — jamais leur valeur.

    Deux comptes peuvent declarer le meme serveur (meme URL) avec des jetons
    differents. Sans cette empreinte dans la cle de cache, les deux
    partageraient une meme entree : le second heriterait des identifiants du
    premier. Le hachage rend les jetons indistinguables et non réversibles ;
    seule l'egalite est exploitee, ce qui est exactement ce dont la cle a besoin.
    """
    chiffre = serveur.en_tetes_chiffres or ""
    if not chiffre:
        return "-"
    return sha256(chiffre.encode("utf-8")).hexdigest()[:16]


def signature(serveurs: Sequence[ServeurMcp]) -> str:
    """Empreinte stable des connecteurs (sert de clé de cache).

    Inclut le **proprietaire** et l'**empreinte des credentials** : la cle
    distingue deux contextes identiques en apparence mais distincts en
    realite (meme URL, jetons differents). C'est cette distinction qui porte
    l'isolation — pas une cle utilisateur imposee par convention.
    """
    parties = [
        "|".join(
            [
                serveur.nom,
                serveur.transport,
                serveur.url or "",
                serveur.commande or "",
                ",".join(serveur.arguments or []),
                str(serveur.utilisateur_id or "global"),
                empreinte_credentials(serveur),
            ]
        )
        for serveur in sorted(serveurs, key=lambda s: s.nom)
    ]
    return "§".join(parties)


def _nom_outil_pour(
    agent_id: str, serveur: ServeurMcp, nom: str, pris: dict[str, ServeurMcp]
) -> str | None:
    """Nom effectif d'un outil pour cet agent, ou ``None`` s'il s'écarte.

    Un connecteur global et un connecteur personnel peuvent exposer le même nom
    d'outil. ``tool_name_prefix`` produirait alors deux outils de même nom et
    LangGraph refuse les doublons : l'échec porterait sur la totalité du graphe,
    y compris les outils sans rapport. Règle : **le personnel écarte le global**
    pour cet agent, et l'écart est journalisé — jamais silencieusement.

    Deux outils de même *famille* qui se normalisent pareil ne s'écartent pas :
    ce sont deux capacités distinctes, un suffixe les garde toutes les deux.
    L'ordre de chargement (personnels d'abord, cf. ``outils_mcp_pour``) rend le
    cas « global puis personnel » impossible ; par sûreté il suffixe au lieu de
    masquer.
    """
    occupant = pris.get(nom)
    if occupant is None:
        return nom
    if serveur.utilisateur_id is None and occupant.utilisateur_id is not None:
        _logger.warning(
            "Outil MCP %s écarté pour %s : le connecteur personnel « %s » est "
            "prioritaire sur le global « %s ».",
            nom,
            agent_id,
            occupant.nom,
            serveur.nom,
        )
        return None
    unique = nom_outil_unique(nom, pris)
    _logger.info(
        "Outil MCP %s déjà pris (connecteur « %s ») : exposé sous %s pour %s.",
        nom,
        occupant.nom,
        unique,
        agent_id,
    )
    return unique


def outils_mcp_pour(agent_id: str, serveurs: Sequence[ServeurMcp]) -> tuple[Any, ...]:
    """Outils MCP d'un agent, ou tuple vide si aucun connecteur exploitable.

    Args:
        agent_id: agent pour lequel les outils sont chargés (cache et journal).
        serveurs: connecteurs actifs autorisant cet agent.

    Returns:
        Les outils distants prêts à être ajoutés au graphe de l'agent. Une liste
        vide n'est jamais une erreur : elle signifie « aucun connecteur » ou
        « connecteurs injoignables », et c'est journalisé.
    """
    if not serveurs:
        return ()
    if not paquet_disponible():
        _logger.warning(
            "Connecteurs MCP déclarés pour %s mais langchain-mcp-adapters absent "
            "(pip install 'carso-backend[mcp]') : outils distants non chargés.",
            agent_id,
        )
        return ()

    cle = (agent_id, signature(serveurs))
    with _VERROU:
        if cle in _CACHE:
            return _CACHE[cle]

    from langchain_mcp_adapters.client import MultiServerMCPClient

    outils: list[Any] = []
    #: Nom exposé → connecteur qui l'a fourni (règle personnel/global).
    pris: dict[str, ServeurMcp] = {}
    # Personnels d'abord : en cas de collision de nom d'outil, c'est le global
    # qui s'ecarte (voir ``_nom_outil_pour``).
    ordre = sorted(
        serveurs,
        key=lambda x: (x.utilisateur_id is None, x.nom),
    )
    for serveur in ordre:
        if getattr(serveur, "mode_authentification", "en_tetes") == "oauth":
            from app.application.services.mcp_oauth_service import jetons_presents

            if not jetons_presents(serveur):
                # Refus **explicite** plutôt qu'une connexion vouée au 401 : l'agent
                # n'hérite pas d'un connecteur mort, et l'opérateur lit pourquoi.
                _logger.warning(
                    "Connecteur MCP %s ignoré pour %s : autorisation OAuth "
                    "requise (aucun jeton) — cliquer « S'authentifier ».",
                    serveur.nom,
                    agent_id,
                )
                continue
        try:
            # Typé ``Any`` : la forme exacte attendue (StdioConnection,
            # StreamableHttpConnection…) diffère selon la version des
            # adaptateurs ; ``_connexion`` produit la variante du transport.
            #
            # Clé **assainie** : ``tool_name_prefix`` reprend la clé telle
            # quelle pour préfixer chaque outil. Le libellé du connecteur
            # (« Parallel Search ») produisait `Parallel Search_web_search`,
            # qu'une API OpenAI-compatible refuse — la requête entière tombait.
            connexions: dict[str, Any] = {
                nom_outil_conforme(serveur.nom, defaut="mcp"): _connexion(serveur)
            }
            client = MultiServerMCPClient(
                connexions,
                tool_name_prefix=True,
                handle_tool_errors=True,
            )
            # Enveloppe obligatoire : ``handle_tool_errors`` ne couvre que les
            # erreurs ``isError=True`` du serveur — les pannes de transport et
            # de session remonteraient en exception et arrêteraient le run.
            #
            # Ordre : assainir → dédupliquer → envelopper. Dédupliquer après
            # l'ajout (état antérieur) ne filtrait rien : la liste était déjà
            # étendue quand `_sans_doublons` rendait son résultat.
            charges = _executer(client.get_tools())
            retenus: list[Any] = []
            exposes: list[str] = []
            for outil in charges:
                brut = str(getattr(outil, "name", NOM_OUTIL_DEFAUT))
                nom = _nom_outil_pour(
                    agent_id, serveur, nom_outil_conforme(brut), pris
                )
                if nom is None:
                    continue
                pris[nom] = serveur
                retenus.append(proteger_outil_mcp(outil, serveur=serveur.nom, nom=nom))
                exposes.append(nom if nom == brut else f"{brut} → {nom}")
            outils.extend(retenus)
            # Journalisé au chargement, pas seulement en cas d'échec : sans cette
            # ligne, un connecteur qui répond ne laisse aucune trace, et un
            # agent privé de ses outils distants est indiscernable d'un agent
            # qui n'en a jamais eu (constaté le 04/10 sur `search.parallel.ai`,
            # où cinq connexions 405 semblaient être une panne alors que
            # `tools/list` aboutissait). Les noms journalisés sont ceux que le
            # modèle voit, avec leur forme d'origine quand elle a changé.
            _logger.info(
                "Connecteur MCP %s chargé pour %s : %d outil(s) — %s",
                serveur.nom,
                agent_id,
                len(retenus),
                ", ".join(sorted(exposes)) or "aucun",
            )
        except Exception as exc:
            # Un connecteur en échec ne prive pas l'agent de ses autres outils.
            _logger.warning(
                "Connecteur MCP %s injoignable pour %s : %s",
                serveur.nom,
                agent_id,
                motif_erreur(exc),
            )

    resultat = tuple(outils)
    with _VERROU:
        _CACHE[cle] = resultat
    return resultat


async def charger_avec_consentement(serveur: ServeurMcp, attente: Any) -> tuple[Any, ...]:
    """Charge les outils d'un connecteur **en attendant le consentement** (async).

    Réservé au flux « S'authentifier » : la connexion porte alors les gestionnaires
    interactifs du SDK (publication de l'URL, attente du code). Aucun autre appel
    ne doit l'utiliser — un graphe ne doit jamais bloquer sur un navigateur.

    **Asynchrone par nécessité** : ce flux s'exécute déjà sur la boucle MCP. Un
    chargeur synchrone y attendrait ``run_coroutine_threadsafe(...).result()``,
    c'est-à-dire la boucle sur laquelle il tourne — interblocage constaté en
    écrivant le test de flux (30 s d'attente, aucune URL publiée).
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            nom_outil_conforme(serveur.nom, defaut="mcp"): _connexion(
                serveur, attente=attente
            )
        },
        tool_name_prefix=True,
        handle_tool_errors=True,
    )
    return tuple(await client.get_tools())


def sonder_serveur(serveur: ServeurMcp) -> dict[str, Any]:
    """Connexion **réelle** à un connecteur, hors cache : ce qui répond.

    Une sonde est la seule preuve qu'un connecteur sert quelque chose : elle dit
    les outils obtenus, l'erreur dépliée et la durée. Elle ne consulte jamais le
    cache — sinon elle ne vérifierait que le souvenir du dernier chargement.

    Returns:
        Un compte rendu **public** et sérialisable : ``le``, ``ok``,
        ``duree_ms``, ``nb_outils``, ``outils``, ``erreur``, ``causes``. Aucun
        contenu de jeton n'y figure.
    """
    debut = time.perf_counter()
    rapport: dict[str, Any] = {
        "le": datetime.now(UTC).isoformat(),
        "ok": False,
        "duree_ms": 0,
        "nb_outils": 0,
        "outils": [],
        "erreur": None,
        "causes": [],
    }

    def _finir() -> dict[str, Any]:
        rapport["duree_ms"] = int((time.perf_counter() - debut) * 1000)
        return rapport

    if not paquet_disponible():
        rapport["erreur"] = (
            "Support MCP non installé : pip install 'carso-backend[agents]'."
        )
        return _finir()

    if getattr(serveur, "mode_authentification", "en_tetes") == "oauth":
        from app.application.services.mcp_oauth_service import jetons_presents

        if not jetons_presents(serveur):
            rapport["erreur"] = (
                "Autorisation OAuth requise : aucun jeton enregistré. "
                "Cliquer « S'authentifier » pour autoriser ce connecteur."
            )
            return _finir()

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {nom_outil_conforme(serveur.nom, defaut="mcp"): _connexion(serveur)},
            tool_name_prefix=True,
            handle_tool_errors=True,
        )
        charges = _executer(client.get_tools())
    except Exception as exc:  # noqa: BLE001 - la sonde rapporte, elle ne casse pas
        rapport["erreur"] = motif_erreur(exc)
        rapport["causes"] = _causes(exc)
        return _finir()

    noms = sorted(str(getattr(outil, "name", "?")) for outil in charges)
    rapport.update(ok=True, nb_outils=len(noms), outils=noms[:50])
    return _finir()


def vider_cache(
    *, agent_id: str | None = None, signature_contexte: str | None = None
) -> None:
    """Vide le cache des outils MCP — **cible** par defaut.

    Un utilisateur qui change son connecteur invalide **son** contexte, pas
    celui des autres : une purge globale serait un effet de bord sur des
    contextes parfaitement sains (et un ``get_tools()`` inutile pour tous).

    Sans argument, la purge reste **totale** : reservee au changement de
    connecteur **global** par un administrateur, et aux tests.
    """
    with _VERROU:
        if agent_id is None and signature_contexte is None:
            _CACHE.clear()
            return
        for cle in [
            c
            for c in _CACHE
            if (agent_id is None or c[0] == agent_id)
            and (signature_contexte is None or c[1] == signature_contexte)
        ]:
            del _CACHE[cle]


__all__ = [
    "charger_avec_consentement",
    "motif_erreur",
    "nom_outil_conforme",
    "nom_outil_unique",
    "fabrique_client_mcp",
    "outils_mcp_pour",
    "paquet_disponible",
    "politique_tls_mcp",
    "payload_erreur_mcp",
    "proteger_outil_mcp",
    "signature",
    "sonder_serveur",
    "soumettre",
    "vider_cache",
]
