"""Autorisation OAuth 2.1 des connecteurs MCP personnels (incr. 47).

Pourquoi ce module—un serveur MCP distant peut n'accepter que des clients
OAuth (spécification MCP, autorisation). Le connecteur ne portait qu'un en-tête
statique—la vérification du 05/10 a montré qu'un tel serveur s'enregistrait
« sans erreur » puis échouait à chaque chargement (401), sans que l'interface
puisse le dire (``docs/validation/VERIFICATION-MCP-OAUTH.md``).

Trois responsabilités séparées :

- **le stockage** (``StockageMcp``) implémente le protocole ``TokenStorage`` du
  SDK au-dessus de la ligne du connecteur. Les métadonnées de client et les
  jetons sont chiffrés au repos avec la même clé que les en-têtes ; l'API n'en
  expose jamais le contenu ;
- **le fournisseur** (``fournisseur_oauth``) est l'``httpx.Auth`` branché sur le
  transport. Sans attente, il est **non interactif**—jetons stockés et
  rafraîchissement automatique, jamais de navigateur au chargement des outils ;
- **l'autorisation** (``demarrer`` / ``terminer``) est le seul moment interactif.
  Un consentement navigateur ne peut pas se produire au montage d'un graphe—il
  est déclenché par une requête explicite (bouton « S'authentifier »), puis
  relâché par la route de rappel qui reçoit le code.

Deux limites assumées :

- le registre d'attentes vit **en mémoire du processus**—un déploiement
  multi-processus devrait l'externaliser. Le reste (jetons, statut) est en base ;
- un client **pré-enregistré** est accepté (``oauth_client_id``) parce que
  certains serveurs refusent l'enregistrement dynamique (Meta—allowlist d'URI
  de redirection, mesurée le 05/10). Ce client est public par construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.chiffrement import chiffrer, dechiffrer
from app.core.config import get_settings
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.mcp import ServeurMcp

_logger = logging.getLogger(__name__)

#: Durée maximale d'un consentement navigateur avant abandon (secondes).
DELAI_CONSENTEMENT_S = 300.0
#: Attente maximale de l'URL d'autorisation au démarrage (secondes).
DELAI_URL_S = 30.0
#: Attente maximale de la fin du flux après le retour du navigateur (secondes).
DELAI_FIN_S = 60.0
#: User-Agent des requêtes de découverte OAuth (voir ``_FournisseurAvecAgent``).
AGENT_MCP = f"carso-ai/1.0 python-httpx/{httpx.__version__}"
#: Cadence de scrutation du consentement (secondes) — jamais un blocage de boucle.
_PAS_S = 0.2


# --- registre d'attentes (mémoire du processus) --------------------------------


@dataclass
class _Attente:
    """Un consentement en cours : l'URL publiée, puis le code reçu."""

    serveur_id: UUID
    utilisateur_id: UUID
    nom: str
    url_autorisation: str | None = None
    etat: str | None = None
    code: str | None = None
    remplacee: bool = False
    ok: bool = False
    erreur: str | None = None
    outils: int = 0
    publiee: threading.Event = field(default_factory=threading.Event)
    consentement: threading.Event = field(default_factory=threading.Event)
    termine: threading.Event = field(default_factory=threading.Event)

    async def publier_url(self, url: str) -> None:
        """``redirect_handler`` du SDK : l'URL de consentement est prête.

        L'``état`` (``state``) est extrait de l'URL : c'est lui qui corrèle le
        retour du navigateur à **cette** demande — le SDK le validera ensuite de
        son côté, on ne fait que l'utiliser comme clé de recherche pour une route
        qui, elle, arrive sans jeton d'authentification.
        """
        self.url_autorisation = url
        self.etat = (_query(url).get("state") or [None])[0]
        if self.etat:
            _enregistrer_par_etat(self.etat, self)
        self.publiee.set()

    async def attendre_le_code(self) -> tuple[str, str | None]:
        """``callback_handler`` du SDK : attend le code, sans bloquer la boucle."""
        from mcp.client.auth import OAuthFlowError

        fin = time.monotonic() + DELAI_CONSENTEMENT_S
        while time.monotonic() < fin:
            if self.remplacee:
                raise OAuthFlowError(
                    "Autorisation remplacée par une nouvelle demande."
                )
            if self.consentement.is_set():
                if not self.code:
                    raise OAuthFlowError(
                        "Consentement refusé ou code d'autorisation absent."
                    )
                return self.code, self.etat
            await asyncio.sleep(_PAS_S)
        raise OAuthFlowError(
            "Délai de consentement dépassé (5 min) : relancez « S'authentifier »."
        )


_ATTENTES: dict[str, _Attente] = {}
_VERROU_ATTENTES = threading.Lock()


def _enregistrer_par_etat(etat: str, attente: _Attente) -> None:
    with _VERROU_ATTENTES:
        _ATTENTES[etat] = attente


def _attente_pour(etat: str) -> _Attente | None:
    with _VERROU_ATTENTES:
        return _ATTENTES.get(etat)


def _retirer(etat: str | None) -> None:
    if not etat:
        return
    with _VERROU_ATTENTES:
        _ATTENTES.pop(etat, None)


def _remplacer(serveur_id: UUID, utilisateur_id: UUID) -> None:
    """Abandonne les demandes précédentes du même connecteur et compte.

    Sans cela, deux clics sur « S'authentifier » laisseraient deux flux vivants :
    le premier code reçu pourrait être consommé par l'ancienne demande, et le
    second consentement serait perdu.
    """
    with _VERROU_ATTENTES:
        for attente in _ATTENTES.values():
            if (
                attente.serveur_id == serveur_id
                and attente.utilisateur_id == utilisateur_id
            ):
                attente.remplacee = True
                attente.consentement.set()


# --- stockage (TokenStorage du SDK, chiffré au repos) --------------------------


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def uri_redirection() -> str:
    """URI de rappel déclarée au serveur d'autorisation.

    Elle pointe sur **le backend** : le code y arrive et le jeton y est échangé,
    jamais dans le navigateur. C'est aussi l'URI que doit enregistrer un serveur
    qui n'accepte que des clients connus.
    """
    reglages = get_settings()
    return (
        f"{reglages.backend_public_url.rstrip('/')}"
        f"{reglages.api_v1_prefix}/parametres/mcp/personnel/oauth/callback"
    )


def _lire_blob(session: Session, serveur_id: UUID) -> dict[str, Any]:
    """Blob OAuth déchiffré d'un connecteur (vide si absent ou illisible)."""
    ligne = session.get(ServeurMcp, serveur_id)
    if ligne is None or not ligne.oauth_etat_chiffre:
        return {}
    brut = dechiffrer(ligne.oauth_etat_chiffre)
    if not brut:
        return {}
    try:
        contenu = json.loads(brut)
    except (TypeError, ValueError):
        return {}
    return contenu if isinstance(contenu, dict) else {}


def _ecrire_blob(session: Session, serveur_id: UUID, blob: dict[str, Any]) -> None:
    ligne = session.get(ServeurMcp, serveur_id)
    if ligne is None:
        return
    ligne.oauth_etat_chiffre = chiffrer(json.dumps(blob, ensure_ascii=False))


def _maj_statut(
    serveur_id: UUID,
    *,
    statut: str | None = None,
    erreur: str | None = None,
    oublier_erreur: bool = False,
    sonde: dict[str, Any] | None = None,
) -> None:
    """Écrit l'état **public** du connecteur dans une transaction courte.

    Session dédiée et commit immédiat : le flux OAuth vit sur le fil MCP, pas
    dans la requête HTTP. Partager la session de la requête ferait attendre le
    verrou d'écriture à l'une des deux parties (et laisserait l'écran sur un état
    périmé).
    """
    from app.infrastructure.database import SessionLocal

    with SessionLocal() as session:
        ligne = session.get(ServeurMcp, serveur_id)
        if ligne is None:
            return
        if statut is not None:
            ligne.oauth_statut = statut
        if erreur is not None:
            ligne.oauth_derniere_erreur = erreur
        if oublier_erreur:
            ligne.oauth_derniere_erreur = None
        if sonde is not None:
            ligne.derniere_sonde = json.dumps(sonde, ensure_ascii=False)
        session.commit()


def _purger_cache(agents: list[str] | tuple[str, ...]) -> None:
    """Retire les outils MCP mémorisés des agents concernés (ciblé, pas global)."""
    from app.agents.mcp import vider_cache

    for agent_id in sorted({a for a in agents if a}):
        vider_cache(agent_id=agent_id)


def jetons_presents(serveur: ServeurMcp) -> bool:
    """Vrai si le connecteur porte un jeton exploitable (accès ou rafraîchissement).

    Lu sur l'instance : aucune session ni requête. Sert au chargeur d'outils,
    qui doit **ne pas** tenter une connexion interactive au montage d'un graphe.
    """
    if not serveur.oauth_etat_chiffre:
        return False
    brut = dechiffrer(serveur.oauth_etat_chiffre)
    if not brut:
        return False
    try:
        blob = json.loads(brut)
    except (TypeError, ValueError):
        return False
    jetons = blob.get("jetons") if isinstance(blob, dict) else None
    if not isinstance(jetons, dict):
        return False
    return bool(jetons.get("access_token") or jetons.get("refresh_token"))


class StockageMcp:
    """``TokenStorage`` du SDK, adossé à la ligne du connecteur.

    Chaque opération ouvre une transaction courte : le flux OAuth peut rester
    ouvert plusieurs minutes (attente du consentement) et ne doit pas retenir de
    connexion à la base.
    """

    def __init__(self, serveur_id: UUID, *, client_pre_enregistre: str | None = None) -> None:
        self._serveur_id = serveur_id
        self._client_pre_enregistre = (client_pre_enregistre or "").strip() or None

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        from app.infrastructure.database import SessionLocal

        with SessionLocal() as session:
            blob = _lire_blob(session, self._serveur_id)
        brut = blob.get("jetons")
        if not isinstance(brut, dict):
            return None
        try:
            return OAuthToken.model_validate(brut)
        except Exception:  # noqa: BLE001 - un contenu illisible vaut absence
            return None

    async def set_tokens(self, tokens: Any) -> None:
        from app.infrastructure.database import SessionLocal

        with SessionLocal() as session:
            blob = _lire_blob(session, self._serveur_id)
            blob["jetons"] = tokens.model_dump(mode="json")
            blob["jetons_le"] = datetime.now(UTC).isoformat()
            _ecrire_blob(session, self._serveur_id, blob)
            session.commit()
        # Un jeton neuf ne vaut rien tant que l'état affiché dit le contraire.
        _maj_statut(self._serveur_id, statut="autorise", oublier_erreur=True)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        from app.infrastructure.database import SessionLocal

        # Un client **fourni** prime sur celui obtenu par enregistrement :
        # l'utilisateur qui le renseigne demande à l'utiliser. Sans cette priorité,
        # un connecteur déjà enregistré dynamiquement ignorait silencieusement la
        # valeur saisie — constaté le 05/10 (test réel
        # ``_test_reel_oauth_pre_enregistre.py``, étape 6). Le client enregistré
        # reste dans le blob : effacer le champ le fait revenir.
        #
        # ``issuer`` laissé vide : le SDK réutilise sans condition un client non
        # lié à un émetteur (SEP-2352), ce qui évite d'appeler un enregistrement
        # dynamique que le serveur refuse.
        if self._client_pre_enregistre:
            return OAuthClientInformationFull(
                client_id=self._client_pre_enregistre,
                redirect_uris=[uri_redirection()],
                token_endpoint_auth_method="none",
            )
        with SessionLocal() as session:
            blob = _lire_blob(session, self._serveur_id)
        brut = blob.get("client")
        if isinstance(brut, dict):
            try:
                return OAuthClientInformationFull.model_validate(brut)
            except Exception:  # noqa: BLE001 - contenu illisible : on réenregistre
                pass
        return None

    async def set_client_info(self, client_info: Any) -> None:
        from app.infrastructure.database import SessionLocal

        with SessionLocal() as session:
            blob = _lire_blob(session, self._serveur_id)
            blob["client"] = client_info.model_dump(mode="json")
            _ecrire_blob(session, self._serveur_id, blob)
            session.commit()


# --- fournisseur branché sur le transport --------------------------------------


def support_disponible() -> bool:
    """Vrai si le SDK MCP (et son module d'authentification) est installé."""
    try:
        import mcp.client.auth  # noqa: F401
    except ImportError:
        return False
    return True


def _metadonnees_client() -> Any:
    from mcp.shared.auth import OAuthClientMetadata

    reglages = get_settings()
    return OAuthClientMetadata(
        redirect_uris=[uri_redirection()],
        token_endpoint_auth_method="none",
        # Client public : aucun secret partagé (les serveurs MCP exigent PKCE).
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="CARSO AI",
        client_uri=reglages.frontend_public_url,
        # ``scope`` volontairement absent : le SDK le remplit depuis le défi 401
        # du serveur, ou à défaut depuis ses métadonnées (moindre privilège).
    )


class _FournisseurAvecAgent(httpx.Auth):
    """Encapsule le fournisseur du SDK en posant un ``User-Agent`` sur ses requêtes.

    httpx n'applique ses en-têtes par défaut qu'aux requêtes **qu'il construit
    lui-même** ; celles qu'un ``httpx.Auth`` rend à la volée — dont les requêtes
    de découverte de métadonnées OAuth du SDK — partent donc sans ``User-Agent``.
    Meta s'en sert pour aiguiller : sans lui, ``GET
    https://www.facebook.com/.well-known/oauth-authorization-server/<serveur>``
    répond **302 vers /unsupportedbrowser**, le SDK conclut « metadata discovery
    failed » pour chaque URL candidate puis tente ``/register`` (404), au lieu
    d'utiliser le ``registration_endpoint`` annoncé — mesuré le 05/10/2026
    (``docs/validation/INCREMENT-47-MCP-OAUTH.md``, §diagnostic Meta). Avec un
    ``User-Agent`` non navigateur, la même requête rend 200 et le JSON AS se
    valide : l'échec réel (refus d'enregistrement dynamique) est alors exposé.

    L'encapsulation ne change rien d'autre : le fournisseur du SDK garde la main
    sur le flux, les jetons et le rafraîchissement.
    """

    # Repris du fournisseur encapsulé : httpx en a besoin pour ses lectures.
    requires_response_body = True

    def __init__(self, fournisseur: Any, agent: str = AGENT_MCP) -> None:
        self._fournisseur = fournisseur
        self._agent = agent

    def _enrichir(self, requete: httpx.Request) -> httpx.Request:
        if "user-agent" not in requete.headers:
            requete.headers["user-agent"] = self._agent
        return requete

    def sync_auth_flow(self, requete: httpx.Request) -> Any:
        # Relais **bidirectionnel** obligatoire : le flux du SDK reçoit chaque
        # réponse d'httpx (`response = yield request`). Un simple `for`/`async for`
        # la jetterait (le SDK lirait `None`) : c'est ce qui a produit
        # « AttributeError: 'NoneType' object has no attribute 'status_code' »
        # au premier essai contre Meta le 05/10/2026.
        flux = self._fournisseur.sync_auth_flow(requete)
        reponse: httpx.Response | None = None
        while True:
            try:
                suivante = flux.send(reponse)
            except StopIteration:
                return
            reponse = yield self._enrichir(suivante)

    async def async_auth_flow(self, requete: httpx.Request) -> Any:
        flux = self._fournisseur.async_auth_flow(requete)
        reponse: httpx.Response | None = None
        while True:
            try:
                suivante = await flux.asend(reponse)
            except StopAsyncIteration:
                return
            reponse = yield self._enrichir(suivante)

    @property
    def fournisseur(self) -> Any:
        """Le fournisseur du SDK (tests, diagnostic)."""
        return self._fournisseur


def _nouveau_fournisseur(
    serveur: ServeurMcp, *, attente: _Attente | None = None
) -> Any:
    """Construit un ``OAuthClientProvider`` neuf du SDK (un par tentative)."""
    from mcp.client.auth import OAuthClientProvider

    return OAuthClientProvider(
        server_url=serveur.url or "",
        client_metadata=_metadonnees_client(),
        storage=StockageMcp(serveur.id, client_pre_enregistre=serveur.oauth_client_id),
        redirect_handler=attente.publier_url if attente is not None else None,
        callback_handler=attente.attendre_le_code if attente is not None else None,
        timeout=DELAI_CONSENTEMENT_S,
    )


def fournisseur_oauth(serveur: ServeurMcp, *, attente: _Attente | None = None) -> Any:
    """``httpx.Auth`` OAuth d'un connecteur.

    ``attente`` renseignée : flux **interactif** (le consentement navigateur est
    attendu). Sinon : jetons stockés uniquement, rafraîchissement automatique —
    un chargement d'outils ne doit jamais ouvrir de navigateur.
    """
    def fabrique() -> Any:
        return _nouveau_fournisseur(serveur, attente=attente)

    if attente is None:
        return _FournisseurRenouvelable(fabrique, serveur)
    return _FournisseurAvecAgent(fabrique())


# --- renouvellement du jeton d'accès (OAuth 2.1) -------------------------------
#
# Pourquoi ce bloc existe : le jeton de rafraîchissement est précisément ce qui
# permet à un client public de ne **pas** redemander un consentement à chaque
# expiration (RFC 6749 §6, exigé par OAuth 2.1). Le SDK ne l'exploite que
# dans la durée de vie d'une seule instance de fournisseur, et son
# ``_initialize`` ne recalcule pas l'expiration d'un jeton relu du stockage :
# ``token_expiry_time`` reste ``None``, donc ``is_token_valid()`` est vrai tant
# qu'un jeton existe. Un jeton périmé est donc renvoyé tel quel ; le serveur
# répond 401 et le SDK part en **flux d'autorisation complet** — impossible sans
# navigateur, il échoue en « No redirect handler provided ». Mesuré le 05/10/2026
# (test ``test_un_jeton_rejete_est_renouvelle_sans_navigateur``) : la sonde
# « Tester » échouait alors qu'un jeton de rafraîchissement valide attendait.
#
# Le renouvellement est donc fait ici, dans l'adaptateur de stockage qui possède
# déjà la clé et le chiffrement, et le flux est relancé une fois.


def _jeton_perime(serveur_id: UUID) -> bool:
    """Vrai si le jeton stocké a dépassé sa durée de vie **connue**.

    Le SDK ne sait pas le dire (voir le commentaire ci-dessus) ; nous le
    déduisons de l'instant d'obtention et du ``expires_in`` conservé au blob.
    Une durée inconnue ne vaut pas expiration : on ne rafraîchit pas sur un
    jeton dont le serveur n'a pas donné de durée.
    """
    from app.infrastructure.database import SessionLocal

    with SessionLocal() as session:
        blob = _lire_blob(session, serveur_id)
    jetons = blob.get("jetons")
    obtenu_le = blob.get("jetons_le")
    if not isinstance(jetons, dict) or not isinstance(obtenu_le, str):
        return False
    duree = jetons.get("expires_in")
    if not isinstance(duree, int) or duree <= 0:
        return False
    try:
        moment = datetime.fromisoformat(obtenu_le)
    except ValueError:
        return False
    return moment + timedelta(seconds=duree) <= datetime.now(UTC)


async def _rafraichir_async(serveur: ServeurMcp) -> bool:
    """Échange le jeton de rafraîchissement contre un nouveau jeton d'accès."""
    from mcp.client.auth.utils import (
        build_oauth_authorization_server_metadata_discovery_urls,
        build_protected_resource_metadata_discovery_urls,
    )
    from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata

    from app.infrastructure.database import SessionLocal

    with SessionLocal() as session:
        blob = _lire_blob(session, serveur.id)
    jetons = blob.get("jetons") if isinstance(blob.get("jetons"), dict) else None
    jeton_refraichissement = jetons.get("refresh_token") if jetons else None
    client = blob.get("client") if isinstance(blob.get("client"), dict) else None
    client_id = serveur.oauth_client_id or (client or {}).get("client_id")
    if not jeton_refraichissement or not client_id or not serveur.url:
        return False

    async with httpx.AsyncClient(timeout=15.0, headers={"user-agent": AGENT_MCP}) as http:
        async def _premier(urls: list[str]) -> httpx.Response | None:
            for url in urls:
                try:
                    reponse = await http.get(url)
                except httpx.HTTPError:
                    continue
                if reponse.status_code == 200:
                    return reponse
            return None

        prm = await _premier(
            build_protected_resource_metadata_discovery_urls(None, serveur.url)
        )
        if prm is None:
            return False
        try:
            ressource = ProtectedResourceMetadata.model_validate_json(prm.text)
        except ValueError:
            return False
        if not ressource.authorization_servers:
            return False
        oasm = await _premier(
            build_oauth_authorization_server_metadata_discovery_urls(
                str(ressource.authorization_servers[0]), serveur.url
            )
        )
        if oasm is None:
            return False
        try:
            metadonnees = OAuthMetadata.model_validate_json(oasm.text)
        except ValueError:
            return False

        reponse = await http.post(
            str(metadonnees.token_endpoint),
            data={
                "grant_type": "refresh_token",
                "refresh_token": jeton_refraichissement,
                "client_id": client_id,
                "resource": serveur.url,
            },
        )

    if reponse.status_code != 200:
        # Un jeton de rafraîchissement rejeté (expiré, révoqué) : le connecteur
        # redevient « jamais autorisé » et l'interface proposing de nouveau un
        # consentement. Le motif reste actionnable, jamais le jeton.
        _logger.info(
            "Renouvellement refusé par le serveur d'autorisation (HTTP %s) : "
            "un nouveau consentement est nécessaire.",
            reponse.status_code,
        )
        with SessionLocal() as session:
            blob = _lire_blob(session, serveur.id)
            blob.pop("jetons", None)
            blob.pop("jetons_le", None)
            _ecrire_blob(session, serveur.id, blob)
            session.commit()
        _maj_statut(
            serveur.id,
            statut="jamais",
            erreur=(
                "Autorisation expirée et renouvellement refusé par le serveur : "
                "relancez « S'authentifier »."
            ),
        )
        return False

    try:
        nefrais = OAuthToken.model_validate(reponse.json())
    except ValueError:
        return False
    if not nefrais.access_token:
        return False

    with SessionLocal() as session:
        blob = _lire_blob(session, serveur.id)
        blob["jetons"] = nefrais.model_dump(mode="json")
        blob["jetons_le"] = datetime.now(UTC).isoformat()
        _ecrire_blob(session, serveur.id, blob)
        session.commit()
    _maj_statut(serveur.id, statut="autorise", oublier_erreur=True)
    return True


class _FournisseurRenouvelable(httpx.Auth):
    """Fournisseur non interactif qui renouvelle le jeton puis **relance** le flux.

    Un jeton périmé ou rejeté par le serveur ne doit pas devenir une panne : le
    client tente d'abord un renouvellement (proactif si l'expiration est connue,
    sinon sur le 401), puis rejoue la requête. Un seul essai : au-delà, c'est un
    serveur en panne, pas un jeton à renouveler.

    Le renouvellement n'a de sens que **sans navigateur** : le chemin interactif
    (``attente``) laisse le SDK gérer le 401, car il peut rouvrir la page de
    consentement.
    """

    requires_response_body = True

    def __init__(self, fabrique: Callable[[], Any], serveur: ServeurMcp) -> None:
        self._fabrique = fabrique
        self._serveur = serveur
        self._renouvele = False
        self._demarre = False
        self._agent = AGENT_MCP

    def _enrichir(self, requete: httpx.Request) -> httpx.Request:
        if "user-agent" not in requete.headers:
            requete.headers["user-agent"] = self._agent
        return requete

    async def _relais(self, flux: Any) -> Any:
        reponse: httpx.Response | None = None
        while True:
            try:
                suivante = await flux.asend(reponse)
            except StopAsyncIteration:
                return
            reponse = yield self._enrichir(suivante)

    async def _tenter_renouvellement(self) -> bool:
        if self._renouvele:
            return False
        self._renouvele = True
        try:
            return await _rafraichir_async(self._serveur)
        except Exception as exc:  # noqa: BLE001 - l'échec est un état
            _logger.warning("Renouvellement du jeton impossible : %s", exc)
            return False

    async def async_auth_flow(self, requete: httpx.Request) -> Any:
        if not self._demarre:
            self._demarre = True
            # Expiration connue : renouveler **avant** la requête, plutôt que
            # de demander au serveur pour apprendre qu'il la refusera.
            if _jeton_perime(self._serveur.id):
                await self._tenter_renouvellement()

        flux = self._relais(self._fabrique().async_auth_flow(requete))
        reponse: httpx.Response | None = None
        while True:
            try:
                suivante = await flux.asend(reponse)
            except StopAsyncIteration:
                return
            except Exception:
                # Le flux a échoué : jeton refusé, expiré ou métadonnées
                # injoignables. Un renouvellement peut encore le sauver.
                requete.headers.pop("Authorization", None)
                if await self._tenter_renouvellement():
                    flux = self._relais(self._fabrique().async_auth_flow(requete))
                    reponse = None
                    continue
                raise
            reponse = yield self._enrichir(suivante)

    def sync_auth_flow(self, requete: httpx.Request) -> Any:
        """Chemin synchrone : pas de renouvellement, le SDK garde la main.

        Le client MCP du projet est asynchrone ; ce chemin n'existe que pour ne
        pas laisser une ``NotImplementedError`` surprising.
        """
        return _FournisseurAvecAgent(self._fabrique()).sync_auth_flow(requete)

    @property
    def fournisseur(self) -> Any:
        """Fournisseur du SDK sous-jacent (tests, diagnostic)."""
        return self._fabrique()


# --- cycle de vie interactif ---------------------------------------------------


def _exiger_support() -> None:
    if not support_disponible():
        raise BusinessValidationError(
            "Authentification OAuth indisponible : installer l'extra « agents » "
            "(pip install 'carso-backend[agents]') puis redémarrer le backend.",
            details={"champ": "mode_authentification"},
        )


async def _tenter(cible: ServeurMcp, attente: _Attente) -> None:
    """Tente une connexion **avec** consentement, puis relâche les outils.

    Exécuté sur la boucle MCP dédiée (``app.agents.mcp.soumettre``). Le résultat
    est publié dans l'attente : la route de démarrage attend l'URL, la route de
    rappel attend la fin. Rien ici ne lève vers l'appelant HTTP.
    """
    from app.agents.mcp import charger_avec_consentement, motif_erreur

    debut = time.perf_counter()
    try:
        charges = await charger_avec_consentement(cible, attente)
    except Exception as exc:  # noqa: BLE001 - l'échec est un état, pas une panne
        motif = _conseil(motif_erreur(exc))
        if attente.remplacee:
            # Une demande plus récente a pris la main : l'échec de celle-ci (le
            # plus souvent « Autorisation remplacée ») ne doit pas écraser l'état
            # de la nouvelle — le double clic laissait l'écran sur « erreur »
            # alors qu'un consentement était en cours (mesuré le 05/10).
            _logger.info(
                "Tentative OAuth remplacée pour le connecteur %s : %s",
                attente.nom,
                motif,
            )
        else:
            _logger.warning(
                "Autorisation OAuth du connecteur %s refusée ou incomplète : %s",
                attente.nom,
                motif,
            )
            # L'état public est écrit **avant** que l'attente n'annonce l'échec.
            # `demarrer` se lève dès que `attente.erreur` est renseignée : écrire
            # après revenait à répondre 422 « enregistrement refusé » alors que la
            # ligne portait encore « en_attente » — l'écran (et une recharge)
            # pouvait contredire la réponse qu'il venait de recevoir. Défaut
            # mesuré le 05/10/2026 sur le refus de Meta.
            _maj_statut(cible.id, statut="erreur", erreur=motif)
        attente.ok = False
        # Publication en dernier : c'est elle qui libère la route qui attend.
        attente.erreur = motif
    else:
        noms = sorted(str(getattr(o, "name", "?")) for o in charges)
        attente.ok = True
        attente.outils = len(charges)
        _maj_statut(
            cible.id,
            statut="autorise",
            oublier_erreur=True,
            sonde={
                "le": datetime.now(UTC).isoformat(),
                "ok": True,
                "duree_ms": int((time.perf_counter() - debut) * 1000),
                "nb_outils": len(charges),
                "outils": noms[:50],
                "erreur": None,
                "causes": [],
            },
        )
        _logger.info(
            "Connecteur MCP %s autorisé : %d outil(s) — %s",
            attente.nom,
            len(charges),
            ", ".join(noms) or "aucun",
        )
    finally:
        _retirer(attente.etat)
        _purger_cache(list(cible.agents or []))
        attente.termine.set()


#: Remède commun aux deux situations où l'enregistrement dynamique est impossible.
_CONSEIL_CLIENT = (
    "Créez une application OAuth dans la console du fournisseur, puis renseignez "
    "son Client ID dans le champ « Client OAuth pré-enregistré » — avec l'URI de "
    "redirection affichée sous ce champ, déclarée auprès du fournisseur."
)


def _conseil(motif: str) -> str:
    """Motif d'échec rendu actionnable (le serveur ne dit pas toujours quoi faire).

    Deux échecs d'enregistrement dynamique se ressemblent dans le message mais ne
    se diagnostiquent pas pareil — mesuré le 05/10/2026 sur deux serveurs réels :

    - **refus explicite** (Meta, WhatsApp Business Tools) : le point d'inscription
      répond ``400 invalid_client_metadata`` (« Dynamic registration is not
      available »). L'enregistrement est **fermé par politique** ;
    - **absence de point d'inscription** (GitHub, ``api.githubcopilot.com``) : la
      découverte OAuth **aboutit** (401 défi → métadonnées de la ressource →
      métadonnées du serveur d'autorisation), mais celles-ci n'annoncent aucun
      ``registration_endpoint``. Le SDK retombe alors sur ``{origine}/register``,
      qui répond ``404`` : rien n'a été refusé, il n'y avait rien à appeler.

    L'ancien message unique disait « le serveur refuse l'enregistrement dynamique :
    renseignez le client pré-enregistré **qu'il vous a fourni**, s'il existe ».
    C'était faux pour GitHub, qui ne fournit aucun client : l'utilisateur cherchait
    un identifiant qu'il n'existe pas. Le remède est le même, le diagnostic ne l'est
    pas — et c'est le diagnostic qui fait perdre du temps.
    """
    if "invalid_client_metadata" in motif:
        return (
            f"{motif} : ce serveur **refuse** l'enregistrement dynamique "
            f"(fermeture volontaire de sa part). {_CONSEIL_CLIENT}"
        )
    if "Registration failed" in motif:
        return (
            f"{motif} : ce serveur n'annonce **aucun** point d'enregistrement "
            f"dynamique : la découverte OAuth aboutit, mais l'inscription du client "
            f"est impossible. {_CONSEIL_CLIENT}"
        )
    if "401" in motif or "Unauthorized" in motif:
        return f"{motif} : le serveur a refusé l'authentification."
    return motif


def demarrer(
    serveur: ServeurMcp, *, utilisateur_id: UUID
) -> tuple[str, datetime]:
    """Démarre un consentement : rend l'URL à ouvrir et sa validité.

    Raises:
        BusinessValidationError: support absent, connecteur non OAuth, transport
            local, URL manquante, ou le serveur n'a pas publié d'URL à temps.
    """
    _exiger_support()
    if serveur.mode_authentification != "oauth":
        raise BusinessValidationError(
            "Ce connecteur n'est pas en mode OAuth : enregistrez-le d'abord avec "
            "le mode « oauth ».",
            details={"champ": "mode_authentification"},
        )
    if serveur.transport == "stdio":
        raise BusinessValidationError(
            "OAuth suppose un transport réseau : un serveur stdio reçoit ses "
            "identifiants de l'environnement.",
            details={"champ": "transport"},
        )
    if not serveur.url:
        raise BusinessValidationError(
            "Un connecteur OAuth doit déclarer l'URL du serveur.",
            details={"champ": "url"},
        )

    _remplacer(serveur.id, utilisateur_id)
    attente = _Attente(serveur_id=serveur.id, utilisateur_id=utilisateur_id, nom=serveur.nom)
    _maj_statut(serveur.id, statut="en_attente", oublier_erreur=True)

    from app.agents.mcp import soumettre

    soumettre(_tenter(serveur, attente))

    # Deux issues possibles avant tout consentement : l'URL est publiée, ou la
    # tentative échoue (découverte, enregistrement du client). Dans le second
    # cas, on **rend le motif tout de suite** : attendre le délai complet pour
    # annoncer « pas d'URL » priverait l'utilisateur de la cause réelle — c'est
    # exactement le refus d'enregistrement de Meta (400 ``invalid_client_metadata``),
    # mesuré le 05/10.
    fin = time.monotonic() + DELAI_URL_S
    while time.monotonic() < fin:
        if attente.publiee.wait(timeout=_PAS_S):
            break
        if attente.erreur:
            attente.remplacee = True
            raise BusinessValidationError(attente.erreur, details={"champ": "url"})
        if attente.termine.is_set() and attente.ok:
            # Le connecteur a fonctionné **sans** consentement : un jeton valide
            # (ou un rafraîchissement réussi) suffisait, donc aucune URL ne peut
            # être publiée. Le dire tout de suite — sans quoi l'attente de 30 s
            # aboutissait à « découvrez d'abord la ressource (bouton Tester) »,
            # un message qui n'aide pas face à un connecteur déjà autorisé
            # (mesuré le 05/10).
            attente.remplacee = True
            raise BusinessValidationError(
                f"Connecteur « {serveur.nom} » déjà autorisé : {attente.outils} "
                "outil(s) chargé(s) sans nouveau consentement. Cliquez « Révoquer » "
                "pour changer d'autorisation ou de compte, puis relancez.",
                details={"champ": "url"},
            )
    else:
        attente.remplacee = True
        attente.consentement.set()
        raise BusinessValidationError(
            "Le serveur n'a pas fourni d'URL d'autorisation : découvrez d'abord "
            "la ressource protégée (bouton « Tester ») pour voir son motif exact.",
            details={"champ": "url"},
        )
    expire_le = datetime.now(UTC) + timedelta(seconds=DELAI_CONSENTEMENT_S)
    return str(attente.url_autorisation), expire_le


def terminer(*, state: str, code: str | None, erreur: str | None) -> dict[str, Any]:
    """Relâche une attente avec le code reçu (ou l'erreur annoncée).

    Returns:
        Un compte rendu **public** : trouvée ou non, issue, motif. Jamais un
        jeton, jamais un code.
    """
    attente = _attente_pour(state)
    if attente is None:
        return {
            "trouvee": False,
            "ok": False,
            "message": (
                "Demande d'autorisation introuvable ou expirée : relancez "
                "« S'authentifier » depuis l'interface."
            ),
        }
    if erreur:
        attente.erreur = f"Le serveur a refusé le consentement : {erreur}"
        attente.code = None
        attente.consentement.set()
    else:
        attente.code = code
        attente.consentement.set()
    attente.termine.wait(timeout=DELAI_FIN_S)
    return {
        "trouvee": True,
        "ok": bool(attente.ok),
        "nom": attente.nom,
        "outils": attente.outils,
        "message": attente.erreur
        or (
            f"Connecteur « {attente.nom} » autorisé : {attente.outils} outil(s) "
            "chargé(s)."
        ),
    }


def revoquer(serveur: ServeurMcp) -> None:
    """Retire les jetons stockés (le client enregistré est conservé).

    Conserver les métadonnées de client évite un nouvel enregistrement — parfois
    refusé — au prochain consentement ; seul le jeton est un secret révocable.
    """
    from app.infrastructure.database import SessionLocal

    with SessionLocal() as session:
        blob = _lire_blob(session, serveur.id)
        blob.pop("jetons", None)
        blob.pop("jetons_le", None)
        _ecrire_blob(session, serveur.id, blob)
        session.commit()
    _maj_statut(serveur.id, statut="jamais", oublier_erreur=True)
    _purger_cache(list(serveur.agents or []))


__all__ = [
    "DELAI_CONSENTEMENT_S",
    "StockageMcp",
    "demarrer",
    "fournisseur_oauth",
    "jetons_presents",
    "revoquer",
    "support_disponible",
    "terminer",
    "uri_redirection",
]
