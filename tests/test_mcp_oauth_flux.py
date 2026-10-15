"""Tests — flux OAuth complet contre un faux serveur MCP (incrément 47).

Ce que ce fichier prouve, et qu'aucun test unitaire ne peut prouver : **le flux
entier fonctionne**, dans l'ordre réel — défi 401, découverte de la ressource
protégée, métadonnées du serveur d'autorisation, enregistrement du client,
redirection navigateur, échange du code **avec PKCE**, stockage chiffré du jeton,
puis chargement des outils.

Le faux serveur est un vrai serveur HTTP (uvicorn dans un fil) :

- il expose un outil MCP réel (``ping``) via ``FastMCP`` ;
- il refuse toute requête MCP sans jeton — 401 + ``WWW-Authenticate`` portant
  l'URL de découverte, exactement comme un serveur conforme ;
- il vérifie le PKCE (``code_verifier`` → S256 → ``code_challenge``), sinon le
  test passerait avec un flux qui n'authentifie rien ;
- il **compte** les enregistrements dynamiques : c'est ce compteur qui prouve
  qu'un client pré-enregistré évite l'appel refusé par Meta (allowlist mesurée
  le 05/10, ``docs/validation/VERIFICATION-MCP-OAUTH.md``).

Aucun accès réseau externe : tout est en boucle locale.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.domain.identity import Utilisateur
from app.domain.mcp import ServeurMcp

#: Un connecteur **personnel** ne charge ses outils que dans l'agent personnel
#: du compte (règle 10/10) : c'est cet agent que le flux complet doit alimenter.
AGENT = "agent_personnel"


# --- Faux serveur MCP protégé par OAuth -----------------------------------------


class _EtatFauxServeur:
    """État observable du faux serveur (compteurs lus par les tests)."""

    def __init__(self, *, refuser_enregistrement: bool) -> None:
        self.refuser_enregistrement = refuser_enregistrement
        self.registrations: list[dict] = []
        self.codes: dict[str, dict] = {}
        self.jetons_emis = 0
        self.jetons_rafraichis = 0
        self.jeton = f"jeton-faux-{uuid4().hex[:8]}"
        self.refresh = f"refresh-faux-{uuid4().hex[:8]}"
        self.base = ""
        self.demande_journal: list[str] = []
        #: Journal **HTTP** de ce que le client a réellement envoyé, dans l'ordre
        #: (chemin, statut, défi ``WWW-Authenticate``). C'est la seule preuve
        #: qu'un flux OAuth 2.1 est respecté lien par lien : un test peut
        #: « fonctionner » sans jamais avoir reçu le 401 ni découvert le
        #: serveur d'autorisation.
        self.journal: list[dict] = []

    @property
    def prm_url(self) -> str:
        return f"{self.base}/.well-known/oauth-protected-resource/mcp"

    @property
    def defi(self) -> str:
        """Défi 401 exact, conforme à RFC 9728 (§5.1)."""
        return f'Bearer resource_metadata="{self.prm_url}", scope="demo"'

    def noter(
        self, methode: str, chemin: str, *, autorise: bool, statut: int, defi: str | None
    ) -> None:
        self.journal.append(
            {
                "methode": methode,
                "chemin": chemin,
                "autorise": autorise,
                "statut": statut,
                "defi": defi,
            }
        )

    def chemins(self, prefixe: str = "") -> list[str]:
        """Chemins journalisés (préfixés), dans l'ordre réel des appels."""
        return [e["chemin"] for e in self.journal if e["chemin"].startswith(prefixe)]

    def premiere(self, prefixe: str) -> dict:
        for entree in self.journal:
            if entree["chemin"].startswith(prefixe):
                return entree
        raise AssertionError(f"aucune requête « {prefixe}… » dans le journal : {self.chemins()}")

    def chaine(self) -> list[str]:
        """Journal en une ligne par étape — message d'échec lisible."""
        return [
            f"{e['methode']} {e['chemin']} → {e['statut']}"
            f"{' (avec jeton)' if e['autorise'] else ' (sans jeton)'}"
            for e in self.journal
        ]


class _ExigeJeton:
    """Garde le point d'entrée MCP : 401 + défi OAuth tant que le jeton manque.

    Les routes OAuth restent publiques — c'est le principe : le client se découvre
    avant de pouvoir s'authentifier.
    """

    def __init__(self, application, etat: _EtatFauxServeur) -> None:
        self.application = application
        self.etat = etat

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        entetes = dict(scope.get("headers", []))
        attendu = f"Bearer {self.etat.jeton}".encode()
        porte_jeton = entetes.get(b"authorization") == attendu
        chemin = str(scope.get("path", ""))
        methode = str(scope.get("method", "?"))

        if chemin.startswith("/mcp") and not porte_jeton:
            # Le défi est le **premier** maillon d'OAuth 2.1 : sans lui, le
            # client ne connaît ni le serveur d'autorisation ni son URL. Il est
            # journalisé avec son statut pour être vérifiable.
            self.etat.noter(methode, chemin, autorise=False, statut=401, defi=self.etat.defi)
            reponse = Response(
                json.dumps(
                    {"title": "Authentication Required", "status": 401},
                    ensure_ascii=False,
                ),
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": self.etat.defi},
            )
            await reponse(scope, receive, send)
            return

        # Journalisation de tout le reste (découverte, enregistrement, jeton) :
        # le statut réel est lu dans ``http.response.start``, donc le journal
        # reflète l'échange et non l'intention.
        entree: dict = {"methode": methode, "chemin": chemin, "autorise": porte_jeton}
        self.etat.journal.append(entree)

        async def _send(evenement: dict) -> None:
            if evenement["type"] == "http.response.start":
                entree["statut"] = evenement["status"]
                recus = {
                    cle.decode().lower(): valeur.decode()
                    for cle, valeur in evenement.get("headers", [])
                }
                entree["defi"] = recus.get("www-authenticate")
            await send(evenement)

        await self.application(scope, receive, _send)


def _construire_application(etat: _EtatFauxServeur):
    from mcp.server.fastmcp import FastMCP

    serveur = FastMCP("faux-serveur")

    @serveur.tool()
    def ping(message: str) -> str:
        """Répond pong au message fourni."""
        return f"pong:{message}"

    @serveur.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
    async def ressource_protegee(request: Request) -> Response:
        return JSONResponse(
            {
                "resource": f"{etat.base}/mcp",
                "authorization_servers": [etat.base],
                "scopes_supported": ["demo"],
                "bearer_methods_supported": ["header"],
            }
        )

    @serveur.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def metadonnees_autorisation(request: Request) -> Response:
        return JSONResponse(
            {
                "issuer": etat.base,
                "authorization_endpoint": f"{etat.base}/authorize",
                "token_endpoint": f"{etat.base}/token",
                "registration_endpoint": f"{etat.base}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["demo"],
            }
        )

    @serveur.custom_route("/register", methods=["POST"])
    async def enregistrement(request: Request) -> Response:
        corps = await request.json()
        etat.registrations.append(corps)
        if etat.refuser_enregistrement:
            # Réponse calquée sur celle de Meta : c'est ce texte que l'utilisateur
            # doit lire, avec le conseil d'utiliser un client pré-enregistré.
            return JSONResponse(
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Dynamic registration is not available for this client.",
                },
                status_code=400,
            )
        return JSONResponse(
            {
                "client_id": "client-dynamique",
                "redirect_uris": corps.get("redirect_uris", []),
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "demo",
            },
            status_code=201,
        )

    @serveur.custom_route("/authorize", methods=["GET"])
    async def autorisation(request: Request) -> Response:
        params = request.query_params
        code = f"code-{uuid4().hex[:10]}"
        etat.codes[code] = {
            "redirect_uri": params.get("redirect_uri", ""),
            "code_challenge": params.get("code_challenge", ""),
        }
        cible = f"{params.get('redirect_uri', '')}?code={code}&state={params.get('state', '')}"
        return RedirectResponse(url=cible, status_code=302)

    @serveur.custom_route("/token", methods=["POST"])
    async def jeton(request: Request) -> Response:
        donnees = await request.form()

        # OAuth 2.1 : un client public ne peut exiger de nouveau consentement à
        # chaque expiration — le jeton de rafraîchissement est obligatoire. Ce
        # chemin est celui qu'emprunte la sonde « Tester » quand le jeton
        # d'accès est périmé, sans jamais ouvrir de navigateur.
        if str(donnees.get("grant_type", "")) == "refresh_token":
            if str(donnees.get("refresh_token", "")) != etat.refresh:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "refresh_token inconnu"},
                    status_code=400,
                )
            etat.jetons_rafraichis += 1
            return JSONResponse(
                {
                    "access_token": etat.jeton,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": etat.refresh,
                    "scope": "demo",
                }
            )

        code = str(donnees.get("code", ""))
        enregistre = etat.codes.pop(code, None)
        if enregistre is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        verifieur = str(donnees.get("code_verifier", ""))
        attendu = (
            base64.urlsafe_b64encode(hashlib.sha256(verifieur.encode()).digest())
            .decode()
            .rstrip("=")
        )
        if not verifieur or attendu != enregistre["code_challenge"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE invalide"},
                status_code=400,
            )
        etat.jetons_emis += 1
        return JSONResponse(
            {
                "access_token": etat.jeton,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": etat.refresh,
                "scope": "demo",
            }
        )

    return _ExigeJeton(serveur.streamable_http_app(), etat)


def _port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


def _demarrer_faux_serveur(*, refuser_enregistrement: bool) -> Iterator[_EtatFauxServeur]:
    etat = _EtatFauxServeur(refuser_enregistrement=refuser_enregistrement)
    port = _port_libre()
    etat.base = f"http://127.0.0.1:{port}"
    application = _construire_application(etat)
    config = uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning")
    serveur = uvicorn.Server(config)
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()

    fin = time.monotonic() + 30
    while time.monotonic() < fin:
        try:
            if httpx.get(etat.prm_url, timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:  # pragma: no cover - dépend de la machine
        raise AssertionError("le faux serveur MCP n'a pas démarré")

    yield etat
    serveur.should_exit = True
    fil.join(timeout=10)


@pytest.fixture(scope="module")
def serveur_avec_enregistrement() -> Iterator[_EtatFauxServeur]:
    """Serveur conforme : il accepte un client enregistré dynamiquement."""
    yield from _demarrer_faux_serveur(refuser_enregistrement=False)


@pytest.fixture(scope="module")
def serveur_sans_enregistrement() -> Iterator[_EtatFauxServeur]:
    """Serveur fermé comme Meta : seuls des clients déjà connus sont acceptés."""
    yield from _demarrer_faux_serveur(refuser_enregistrement=True)


# --- Pilotage ----------------------------------------------------------------


def _compte() -> Utilisateur:
    return Utilisateur(
        email=f"oauth-{uuid4().hex[:8]}@carso.test",
        nom="Oauth",
        prenom="Test",
        password_hash="x",
        role="collaborateur",
        statut="actif",
    )


def _brancher(api_client, utilisateur: Utilisateur) -> None:
    from app.api.deps import get_current_user, require_admin

    api_client.app.dependency_overrides[get_current_user] = lambda: utilisateur
    api_client.app.dependency_overrides[require_admin] = lambda: utilisateur


def _preparer_utilisateur(api_client, api_session_factory) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        _brancher(api_client, compte)
    finally:
        session.close()


def _corps(url: str, *, client_id: str | None = None) -> dict:
    return {
        "transport": "streamable_http",
        "url": url,
        "commande": None,
        "arguments": [],
        "agents": [AGENT],
        "en_tetes": None,
        "mode_authentification": "oauth",
        "oauth_client_id": client_id,
        "actif": True,
    }


def _consentir(api_client, url_autorisation: str) -> httpx.Response:
    """Joue le navigateur : suit la redirection d'autorisation, puis appelle le rappel.

    Le ``state`` est renvoyé tel quel à la route de rappel — c'est lui qui corrèle
    la demande, et le SDK le valide avant l'échange du code.
    """
    redirection = httpx.get(url_autorisation, follow_redirects=False, timeout=10)
    assert redirection.status_code == 302, redirection.text
    cible = redirection.headers["location"]
    params = parse_qs(urlparse(cible).query)
    return api_client.get(
        "/api/v1/parametres/mcp/personnel/oauth/callback",
        params={"code": params["code"][0], "state": params["state"][0]},
        follow_redirects=False,
    )


# --- Scénario 1 : flux complet avec enregistrement dynamique --------------------


def test_le_flux_oauth_complet_va_jusqu_aux_outils(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """401 → découverte → client → consentement → jeton → outils : de bout en bout."""
    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)

    ecrit = api_client.put(
        "/api/v1/parametres/mcp/personnel/faux-outil",
        json=_corps(f"{etat.base}/mcp"),
    )
    assert ecrit.status_code == 200, ecrit.text
    assert ecrit.json()["oauth_statut"] == "jamais"

    demarre = api_client.post("/api/v1/parametres/mcp/personnel/faux-outil/autoriser")
    assert demarre.status_code == 200, demarre.text
    url = demarre.json()["url_autorisation"]
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"][0].endswith("/parametres/mcp/personnel/oauth/callback")
    assert etat.jeton not in demarre.text, "aucun jeton ne doit sortir avant le consentement"

    retour = _consentir(api_client, url)
    assert retour.status_code == 303, retour.text
    assert "mcp=autorise" in retour.headers["location"]

    assert etat.jetons_emis == 1, "le code a été échangé une fois, avec PKCE valide"

    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    connecteur = next(c for c in liste["connecteurs"] if c["nom"] == "faux-outil")
    assert connecteur["oauth_statut"] == "autorise"
    assert connecteur["oauth_derniere_erreur"] is None

    sonde = api_client.post("/api/v1/parametres/mcp/personnel/faux-outil/tester").json()
    assert sonde["sonde"]["ok"] is True, sonde["sonde"]
    assert sonde["sonde"]["nb_outils"] == 1
    assert "ping" in sonde["sonde"]["outils"][0]

    # Le jeton est en base, chiffré : sa valeur n'apparaît nulle part en clair.
    session = api_session_factory()
    try:
        ligne = (
            session.query(ServeurMcp)
            .filter(ServeurMcp.nom == "faux-outil")
            .one()
        )
        assert ligne.oauth_etat_chiffre and etat.jeton not in ligne.oauth_etat_chiffre
    finally:
        session.close()


def test_un_etat_inconnu_ne_consomme_rien(api_client, cle_chiffrement, session_locale):
    """Une route de rappel publique doit refuser un ``state`` qu'elle n'attend pas."""
    retour = api_client.get(
        "/api/v1/parametres/mcp/personnel/oauth/callback",
        params={"code": "code-invente", "state": "etat-inconnu"},
        follow_redirects=False,
    )

    assert retour.status_code == 303
    assert "mcp=echec" in retour.headers["location"]


# --- Scénario 2 : serveur fermé (cas Meta) -------------------------------------


def test_un_client_refuse_explique_et_conseille_le_client_pre_enregistre(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_sans_enregistrement,
):
    """Meta refuse l'enregistrement dynamique : le refus doit être immédiat et utile.

    Le message doit contenir le motif du serveur **et** le remède : fournir un
    client pré-enregistré. Sans cela, l'utilisateur n'aurait qu'un « pas d'URL
    d'autorisation » après trente secondes d'attente.
    """
    etat = serveur_sans_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put(
        "/api/v1/parametres/mcp/personnel/ferme", json=_corps(f"{etat.base}/mcp")
    )

    debut = time.monotonic()
    refus = api_client.post("/api/v1/parametres/mcp/personnel/ferme/autoriser")
    duree = time.monotonic() - debut

    assert refus.status_code == 422, refus.text
    message = refus.json()["error"]["message"]
    assert "Registration failed" in message
    assert "invalid_client_metadata" in message
    assert "pré-enregistré" in message, "le remède doit être nommé"
    assert duree < 20, "le refus est rendu sans attendre le délai de consentement"

    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    connecteur = next(c for c in liste["connecteurs"] if c["nom"] == "ferme")
    assert connecteur["oauth_statut"] == "erreur"
    assert "Registration failed" in connecteur["oauth_derniere_erreur"]


def test_un_client_pre_enregistre_evite_l_enregistrement(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_sans_enregistrement,
):
    """Avec un client fourni, le même serveur fermé autorise : zéro enregistrement."""
    etat = serveur_sans_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    avant = len(etat.registrations)

    ecrit = api_client.put(
        "/api/v1/parametres/mcp/personnel/avec-client",
        json=_corps(f"{etat.base}/mcp", client_id="client-fourni-par-le-serveur"),
    )
    assert ecrit.status_code == 200, ecrit.text
    assert ecrit.json()["oauth_client_id"] == "client-fourni-par-le-serveur"

    demarre = api_client.post("/api/v1/parametres/mcp/personnel/avec-client/autoriser")
    assert demarre.status_code == 200, demarre.text
    url = demarre.json()["url_autorisation"]
    assert parse_qs(urlparse(url).query)["client_id"] == ["client-fourni-par-le-serveur"]

    retour = _consentir(api_client, url)
    assert retour.status_code == 303
    assert "mcp=autorise" in retour.headers["location"]

    assert len(etat.registrations) == avant, (
        "un client pré-enregistré ne doit déclencher aucun enregistrement dynamique"
    )

    sonde = api_client.post("/api/v1/parametres/mcp/personnel/avec-client/tester").json()
    assert sonde["sonde"]["ok"] is True, sonde["sonde"]


def test_revoquer_rend_le_connecteur_inutilisable(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """Après révocation, la sonde repasse par « autorisation requise »."""
    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put(
        "/api/v1/parametres/mcp/personnel/a-revoquer", json=_corps(f"{etat.base}/mcp")
    )
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/a-revoquer/autoriser")
    _consentir(api_client, demarre.json()["url_autorisation"])

    revoque = api_client.post("/api/v1/parametres/mcp/personnel/a-revoquer/revoquer")

    assert revoque.status_code == 200, revoque.text
    assert revoque.json()["oauth_statut"] == "jamais"
    sonde = api_client.post("/api/v1/parametres/mcp/personnel/a-revoquer/tester").json()
    assert sonde["sonde"]["ok"] is False
    assert "Autorisation OAuth requise" in sonde["sonde"]["erreur"]


# --- Scénario 3 : défauts trouvés par le test réel du 05/10 --------------------


def test_un_second_clic_ne_desavoue_pas_la_demande_en_cours(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_sans_enregistrement,
):
    """Deux clics sur « S'authentifier » : la demande remplacée ne décide pas.

    Défaut mesuré : le fil de la première tentative se termine en « remplacée » et
    écrivait ``erreur`` **après** que la seconde a posé ``en_attente`` — l'écran
    annonçait une panne pendant qu'un consentement était en cours.
    """
    etat = serveur_sans_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put(
        "/api/v1/parametres/mcp/personnel/double-clic",
        json=_corps(f"{etat.base}/mcp", client_id="client-fourni-par-le-serveur"),
    )

    premiere = api_client.post("/api/v1/parametres/mcp/personnel/double-clic/autoriser")
    seconde = api_client.post("/api/v1/parametres/mcp/personnel/double-clic/autoriser")

    assert premiere.status_code == 200, premiere.text
    assert seconde.status_code == 200, seconde.text

    # Laisse à la tentative remplacée le temps d'écrire, si elle écrit encore.
    time.sleep(1.0)
    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    connecteur = next(c for c in liste["connecteurs"] if c["nom"] == "double-clic")
    assert connecteur["oauth_statut"] == "en_attente", connecteur["oauth_derniere_erreur"]

    retour = _consentir(api_client, seconde.json()["url_autorisation"])
    assert retour.status_code == 303
    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    connecteur = next(c for c in liste["connecteurs"] if c["nom"] == "double-clic")
    assert connecteur["oauth_statut"] == "autorise"


def test_autoriser_un_connecteur_deja_autorise_repond_immediatement(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_sans_enregistrement,
):
    """Déjà autorisé : réponse immédiate et utile, pas trente secondes d'attente.

    Défaut mesuré : un jeton valide suffisait, donc aucune URL n'était publiée ;
    le backend attendait le délai complet (30 s) puis conseillait le bouton
    « Tester », qui ne peut rien résoudre.
    """
    etat = serveur_sans_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put(
        "/api/v1/parametres/mcp/personnel/deja",
        json=_corps(f"{etat.base}/mcp", client_id="client-fourni-par-le-serveur"),
    )
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/deja/autoriser")
    _consentir(api_client, demarre.json()["url_autorisation"])

    debut = time.monotonic()
    seconde = api_client.post("/api/v1/parametres/mcp/personnel/deja/autoriser")
    duree = time.monotonic() - debut

    assert seconde.status_code == 422, seconde.text
    message = seconde.json()["error"]["message"]
    assert "déjà autorisé" in message, message
    assert "Révoquer" in message, message
    assert duree < 5, f"la réponse ne doit pas attendre le délai d'URL : {duree:.1f} s"


def test_un_client_fourni_remplace_le_client_enregistre_dynamiquement(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """Un client renseigné après un enregistrement dynamique doit servir.

    Défaut mesuré : le client stocké (issu du DCR) était lu en premier, la valeur
    saisie était ignorée en silence — l'écran l'affichait pourtant comme la
    configuration active.
    """
    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put("/api/v1/parametres/mcp/personnel/remplace", json=_corps(f"{etat.base}/mcp"))
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/remplace/autoriser")
    _consentir(api_client, demarre.json()["url_autorisation"])
    avant = len(etat.registrations)
    assert avant >= 1

    api_client.post("/api/v1/parametres/mcp/personnel/remplace/revoquer")
    ecrit = api_client.put(
        "/api/v1/parametres/mcp/personnel/remplace",
        json=_corps(f"{etat.base}/mcp", client_id="client-impose-par-l-utilisateur"),
    )
    assert ecrit.json()["oauth_client_id"] == "client-impose-par-l-utilisateur"

    relance = api_client.post("/api/v1/parametres/mcp/personnel/remplace/autoriser")
    assert relance.status_code == 200, relance.text
    url = relance.json()["url_autorisation"]
    assert parse_qs(urlparse(url).query)["client_id"] == ["client-impose-par-l-utilisateur"]

    _consentir(api_client, url)
    assert len(etat.registrations) == avant, "le client fourni ne doit rien réenregistrer"


# --- Scénario 4 : la chaîne OAuth 2.1, lien par lien ---------------------------
#
# Le contrat attendu, dans cet ordre strict :
#
#   1. la requête de ressource est refusée **401** et l'en-tête
#      ``WWW-Authenticate`` porte l'URL de découverte de la ressource protégée ;
#   2. le client lit ces métadonnées, qui désignent le **serveur d'autorisation** ;
#   3. il lit les métadonnées de ce serveur et en tire l'URL d'autorisation ;
#   4. il ouvre cette URL : PKCE **S256**, ``state``, ``redirect_uri`` exacte,
#      ``resource`` (RFC 8707) ;
#   5. le serveur d'autorisation renvoie un **code**, jamais un jeton ;
#   6. le client échange ce code contre un jeton, en prouvant le ``code_verifier`` ;
#   7. la ressource est enfin servie, et seulement avec le jeton.


def _rang(etat, prefixe: str, *, autorise: bool | None = None) -> int:
    """Position d'une requête dans le journal (échec explicite si absente)."""
    for position, entree in enumerate(etat.journal):
        if entree["chemin"].startswith(prefixe) and (
            autorise is None or bool(entree.get("autorise")) is autorise
        ):
            return position
    raise AssertionError(
        f"« {prefixe}… » absent du journal (autorise={autorise}) : {etat.chaine()}"
    )


def test_la_chaine_oauth_21_est_respectee_dans_l_ordre(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """Chaque maillon d'OAuth 2.1 est observé, dans l'ordre, sur le vrai réseau."""
    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put("/api/v1/parametres/mcp/personnel/chaine", json=_corps(f"{etat.base}/mcp"))

    etat.journal.clear()
    avant_echanges = etat.jetons_emis
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/chaine/autoriser")
    assert demarre.status_code == 200, demarre.text
    url = demarre.json()["url_autorisation"]
    params = parse_qs(urlparse(url).query)

    # --- maillon 4 : l'URL d'autorisation est bien celle du serveur découvert ---
    assert url.startswith(etat.base), url
    assert urlparse(url).path == "/authorize", url

    # --- maillon 1 : 401 + défi pointant la découverte de la ressource ---
    rang_401 = _rang(etat, "/mcp", autorise=False)
    defi = etat.journal[rang_401]["defi"] or ""
    assert etat.journal[rang_401]["statut"] == 401
    assert "Bearer" in defi and f'resource_metadata="{etat.prm_url}"' in defi, defi

    # --- maillons 2 et 3 : découverte de la ressource puis du serveur d'AS ---
    rang_prm = _rang(etat, "/.well-known/oauth-protected-resource")
    rang_oasm = _rang(etat, "/.well-known/oauth-authorization-server")
    assert rang_401 < rang_prm < rang_oasm, etat.chaine()

    # --- maillon 4bis : PKCE S256, state, redirect_uri exacte, resource (8707) ---
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"], params
    assert 43 <= len(params["code_challenge"][0]) <= 128
    assert len(params["state"][0]) >= 22, "state trop court pour être anti-CSRF"
    assert params["redirect_uri"][0].endswith("/parametres/mcp/personnel/oauth/callback")
    assert params["resource"] == [f"{etat.base}/mcp"], params

    # --- maillon 5 : le retour du navigateur ne porte qu'un code, jamais de jeton ---
    retour_requete = httpx.get(url, follow_redirects=False, timeout=10)
    cible = retour_requete.headers["location"]
    cible_parsee = urlparse(cible)
    params_retour = parse_qs(cible_parsee.query)
    assert params_retour["code"][0], cible
    assert params_retour["state"] == params["state"], cible
    for interdit in ("access_token", "id_token", "token"):
        assert interdit not in params_retour, f"jeton exposé dans l'URL : {interdit}"
    assert not cible_parsee.fragment, "aucun jeton ne doit transiter dans le fragment"

    # --- maillon 6 : le code n'est échangé qu'après le consentement ---
    rang_autorisation = _rang(etat, "/authorize")
    assert rang_oasm < rang_autorisation
    assert not etat.chemins("/token"), f"échange avant consentement : {etat.chaine()}"
    _consentir_via_cible(api_client, cible)

    rang_jeton = _rang(etat, "/token")
    assert rang_autorisation < rang_jeton, etat.chaine()
    # Compteur **cumulatif** sur le module : c'est l'écart qui compte.
    assert etat.jetons_emis - avant_echanges == 1, (
        "un seul échange de code, avec un code_verifier valide"
    )

    # --- maillon 7 : la ressource n'est servie qu'avec le jeton ---
    rang_servi = _rang(etat, "/mcp", autorise=True)
    assert rang_jeton < rang_servi, etat.chaine()
    assert all(e["statut"] != 401 for e in etat.journal[rang_servi:]), etat.chaine()

    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    connecteur = next(c for c in liste["connecteurs"] if c["nom"] == "chaine")
    assert connecteur["oauth_statut"] == "autorise", connecteur


def test_un_jeton_perime_est_rafraichi_sans_reconsentir(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """OAuth 2.1 : le jeton de rafraîchissement évite un second navigateur.

    Défaut mesuré : le SDK ne recalcule pas l'expiration d'un jeton **relu du
    stockage** (``_initialize`` n'appelle pas ``update_token_expiry``), donc
    ``is_token_valid()`` reste vrai tant qu'un jeton existe. Le serveur recevait
    un jeton périmé, répondait 401, et le client partait en flux d'autorisation
    complet — impossible sans navigateur : « No redirect handler provided ».
    La sonde « Tester » échouait alors qu'un jeton de rafraîchissement valide
    attendait.
    """
    from datetime import UTC, datetime, timedelta

    from app.application.services import mcp_oauth_service as service

    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put("/api/v1/parametres/mcp/personnel/perime", json=_corps(f"{etat.base}/mcp"))
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/perime/autoriser")
    _consentir(api_client, demarre.json()["url_autorisation"])

    # Expiration réaliste : le jeton a été obtenu il y a plus longtemps que sa
    # durée de vie. Sa **valeur** reste la même — seule l'heure change.
    session = api_session_factory()
    try:
        ligne = session.query(ServeurMcp).filter(ServeurMcp.nom == "perime").one()
        blob = service._lire_blob(session, ligne.id)
        assert blob["jetons"]["refresh_token"], "un client public doit recevoir un refresh_token"
        assert blob["jetons"]["expires_in"] > 0
        blob["jetons_le"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        service._ecrire_blob(session, ligne.id, blob)
        session.commit()
    finally:
        session.close()

    etat.journal.clear()
    etat.jetons_rafraichis = 0
    sonde = api_client.post("/api/v1/parametres/mcp/personnel/perime/tester").json()

    assert sonde["sonde"]["ok"] is True, sonde["sonde"]
    assert etat.jetons_rafraichis == 1, f"aucun rafraîchissement : {etat.chaine()}"
    assert not etat.chemins("/authorize"), f"nouveau consentement : {etat.chaine()}"
    # Proactif : le renouvellement précède la requête servie, pas le 401.
    assert _rang(etat, "/token") < _rang(etat, "/mcp", autorise=True), etat.chaine()


def test_un_jeton_rejete_est_renouvelle_sans_navigateur(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """Un jeton que le serveur refuse (révoqué, expiré côté serveur) se renouvelle.

    Complément du cas précédent : ici l'échéance est inconnue du client — le
    serveur change de jeton attendu. La seule réaction possible est un 401, et
    la seule issue sans navigateur est le jeton de rafraîchissement.
    """
    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put("/api/v1/parametres/mcp/personnel/rejete", json=_corps(f"{etat.base}/mcp"))
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/rejete/autoriser")
    _consentir(api_client, demarre.json()["url_autorisation"])

    # Le serveur ne reconnaît plus le jeton stocké (révocation côté serveur).
    etat.jeton = f"jeton-rotule-{uuid4().hex[:8]}"
    etat.journal.clear()
    etat.jetons_rafraichis = 0

    sonde = api_client.post("/api/v1/parametres/mcp/personnel/rejete/tester").json()

    assert sonde["sonde"]["ok"] is True, sonde["sonde"]
    assert etat.jetons_rafraichis == 1, f"aucun renouvellement après 401 : {etat.chaine()}"
    assert not etat.chemins("/authorize"), "navigateur malgré un jeton de rafraîchissement"
    rang_401 = _rang(etat, "/mcp", autorise=False)
    rang_renouvellement = _rang(etat, "/token")
    rang_servi = _rang(etat, "/mcp", autorise=True)
    assert rang_401 < rang_renouvellement < rang_servi, etat.chaine()


def _consentir_via_cible(api_client, cible: str) -> httpx.Response:
    """Appelle la route de rappel avec la redirection déjà suivie."""
    params = parse_qs(urlparse(cible).query)
    return api_client.get(
        "/api/v1/parametres/mcp/personnel/oauth/callback",
        params={"code": params["code"][0], "state": params["state"][0]},
        follow_redirects=False,
    )


def test_le_refus_est_persiste_avant_que_la_reponse_ne_parte(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_sans_enregistrement,
    monkeypatch,
):
    """Le 422 ne doit jamais précéder l'écriture de l'échec qu'il annonce.

    Défaut mesuré : ``_tenter`` renseignait ``attente.erreur`` **puis** écrivait
    l'état public. Or ``demarrer`` se lève dès que ``attente.erreur`` est
    renseignée : la réponse 422 partait donc avant la validation, et la relecture
    immédiate trouvait encore « en_attente ». Un panneau rechargé dans cet intervalle
    contredisait la réponse affichée (constaté contre Meta le 05/10/2026).

    L'écriture est ici ralentie à dessein : la fenêtre devient large et le test
    **déterministe** au lieu de dépendre d'une course de quelques millisecondes.
    """
    from app.application.services import mcp_oauth_service as service

    etat = serveur_sans_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put(
        "/api/v1/parametres/mcp/personnel/trop-tot", json=_corps(f"{etat.base}/mcp")
    )

    ecriture_lente = service._maj_statut
    vue = threading.Event()

    def _ralenti(serveur_id, *, statut=None, **kwargs):  # noqa: ANN001
        if statut == "erreur":
            time.sleep(1.0)
            vue.set()
        return ecriture_lente(serveur_id, statut=statut, **kwargs)

    monkeypatch.setattr(service, "_maj_statut", _ralenti)

    refus = api_client.post("/api/v1/parametres/mcp/personnel/trop-tot/autoriser")
    assert refus.status_code == 422, refus.text

    # Relecture **immédiate**, sans attendre : c'est l'instant exact où la
    # réponse et la base ne doivent pas se contredire.
    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    connecteur = next(c for c in liste["connecteurs"] if c["nom"] == "trop-tot")
    assert connecteur["oauth_statut"] == "erreur", (
        f"la réponse annonce l'échec, la base dit {connecteur['oauth_statut']!r}"
    )
    assert "Registration failed" in connecteur["oauth_derniere_erreur"]
    # Garde-fou : le test ne doit rien prouver si le chemin ralenti n'a pas
    # servi (sinon il passerait sans rien ralentir).
    assert vue.is_set(), "l'écriture ralentie n'a pas eu lieu : test sans valeur"


def test_un_jeton_valide_ne_declenche_aucune_requete_supplementaire(
    api_client,
    api_session_factory,
    cle_chiffrement,
    session_locale,
    serveur_avec_enregistrement,
):
    """Un jeton encore valable sert **directement** : aucune découverte, aucun ``/token``.

    Contrepartie obligatoire des deux tests de renouvellement : le chemin le plus
    fréquent est la sonde « Tester » et le chargement des outils, sur un jeton
    valable. Si le renouvellement y partait quand même, il coûterait deux
    requêtes de découverte **et** un ``POST /token`` à chaque connexion — un
    défaut de performance que les seuls tests d'échec ne verraient pas.
    """
    etat = serveur_avec_enregistrement
    _preparer_utilisateur(api_client, api_session_factory)
    api_client.put(
        "/api/v1/parametres/mcp/personnel/valide", json=_corps(f"{etat.base}/mcp")
    )
    demarre = api_client.post("/api/v1/parametres/mcp/personnel/valide/autoriser")
    _consentir(api_client, demarre.json()["url_autorisation"])

    etat.journal.clear()
    etat.jetons_rafraichis = 0
    sonde = api_client.post("/api/v1/parametres/mcp/personnel/valide/tester").json()

    assert sonde["sonde"]["ok"] is True, sonde["sonde"]
    assert etat.jetons_rafraichis == 0, f"renouvellement inutile : {etat.chaine()}"
    assert not etat.chemins("/token"), f"échange inutile : {etat.chaine()}"
    assert not etat.chemins("/.well-known/"), f"découverte inutile : {etat.chaine()}"
    # Aucune requête sans jeton non plus : le jeton a servi du premier coup.
    assert not [e for e in etat.journal if e["chemin"].startswith("/mcp") and not e["autorise"]]
