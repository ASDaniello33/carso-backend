"""Tests — autorisation OAuth des connecteurs MCP (incrément 47).

Ce que ces tests verrouillent, et pourquoi :

1. **Les règles de mode** : OAuth sur un transport réseau, jamais sur ``stdio``
   (un serveur local reçoit ses identifiants de l'environnement), et seulement si
   les jetons peuvent être chiffrés au repos.
2. **Le refus explicite** : un connecteur OAuth sans jeton n'est pas « tenté »
   (il finirait en 401 opaque) — il est écarté avec un motif lisible.
3. **Le stockage** : les jetons sont chiffrés dans la colonne, et un client
   pré-enregistré est réutilisé **sans** enregistrement dynamique — le seul
   recours face à Meta, dont l'enregistrement est réservé à une allowlist
   (``docs/validation/VERIFICATION-MCP-OAUTH.md``).
4. **Le dépliage des erreurs** : « unhandled errors in a TaskGroup » ne doit plus
   cacher un 401 — c'était le diagnostic impossible à poser le 05/10.

Le flux complet (consentement → code → jeton → outils) est couvert par
``test_mcp_oauth_flux.py``, contre un faux serveur MCP OAuth réel.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx

from app.agents.mcp import motif_erreur, outils_mcp_pour
from app.application.services.mcp_oauth_service import (
    StockageMcp,
    jetons_presents,
    revoquer,
    uri_redirection,
)
from app.core import chiffrement
from app.domain.identity import Utilisateur
from app.domain.mcp import ServeurMcp


def _compte(nom: str) -> Utilisateur:
    return Utilisateur(
        email=f"{nom}-{uuid4().hex[:8]}@carso.test",
        nom=nom.capitalize(),
        prenom="Test",
        password_hash="x",
        role="collaborateur",
        statut="actif",
    )


def _compte_persiste(api_session_factory, nom: str = "alice") -> Utilisateur:
    """Compte **enregistré** : l'identifiant n'existe qu'après le flush.

    Un compte transitoire porterait ``id = None`` et le connecteur écrit
    deviendrait global — le test ne vérifierait plus le périmètre personnel.
    """
    session = api_session_factory()
    try:
        compte = _compte(nom)
        session.add(compte)
        session.commit()
        return compte
    finally:
        session.close()


def _brancher(api_client, utilisateur: Utilisateur) -> None:
    from app.api.deps import get_current_user, require_admin

    api_client.app.dependency_overrides[get_current_user] = lambda: utilisateur
    api_client.app.dependency_overrides[require_admin] = lambda: utilisateur


def _corps(mode: str, *, url: str = "https://mcp.exemple.test/mcp", stdio: bool = False) -> dict:
    return {
        "transport": "stdio" if stdio else "streamable_http",
        "url": None if stdio else url,
        "commande": "npx" if stdio else None,
        "arguments": ["-y", "serveur-mcp"] if stdio else [],
        # Un connecteur de compte ne charge ses outils que dans l'agent personnel
        # (règle 10/10) : un agent partagé est refusé à l'écriture.
        "agents": ["agent_personnel"],
        "en_tetes": None,
        "mode_authentification": mode,
        "actif": True,
    }


def _serveur_oauth(*, jeton: str | None = None, client_id: str | None = None) -> ServeurMcp:
    """Connecteur OAuth en mémoire — le stockage n'est lu que par les services.

    L'identifiant est posé explicitement : sans lui, une lecture par clé primaire
    interrogerait ``id NULL`` (avertissement SQLAlchemy, et test qui ne teste
    rien).
    """
    return ServeurMcp(
        id=uuid4(),
        nom="whatsapp",
        transport="streamable_http",
        url="https://mcp.exemple.test/mcp",
        arguments=[],
        agents=["agent_personnel"],
        actif=True,
        mode_authentification="oauth",
        oauth_client_id=client_id,
        oauth_statut="jamais",
        en_tetes_chiffres=None,
        oauth_etat_chiffre=_blob_chiffre(jeton) if jeton else None,
    )


def _blob_chiffre(jeton: str) -> str:
    """Blob OAuth chiffré, forme réelle produite par ``StockageMcp.set_tokens``."""
    import json

    return chiffrement.chiffrer(
        json.dumps({"jetons": {"access_token": jeton, "token_type": "Bearer"}})
    )


# --- 1. Règles de mode ----------------------------------------------------------


def test_oauth_refuse_sur_un_transport_local(api_client, api_session_factory, cle_chiffrement):
    """Un serveur stdio n'a pas de navigateur : la spec MCP l'exclut d'OAuth."""
    _brancher(api_client, _compte_persiste(api_session_factory))

    reponse = api_client.put(
        "/api/v1/parametres/mcp/personnel/local", json=_corps("oauth", stdio=True)
    )

    assert reponse.status_code == 422, reponse.text
    assert "réseau" in reponse.json()["error"]["message"]


def test_mode_inconnu_refuse_en_nommant_les_modes(
    api_client, api_session_factory, cle_chiffrement
):
    """Un mode inventé est une incompréhension, pas un réglage."""
    _brancher(api_client, _compte_persiste(api_session_factory))

    reponse = api_client.put(
        "/api/v1/parametres/mcp/personnel/x", json=_corps("magique")
    )

    assert reponse.status_code == 422
    assert "magique" in reponse.text
    assert "oauth" in reponse.text, "le refus nomme les modes valides"


def test_oauth_refuse_sans_chiffrement(api_client, api_session_factory, sans_chiffrement):
    """Sans clé, les jetons ne peuvent pas être chiffrés : on refuse d'activer."""
    _brancher(api_client, _compte_persiste(api_session_factory))

    reponse = api_client.put(
        "/api/v1/parametres/mcp/personnel/x", json=_corps("oauth")
    )

    assert reponse.status_code == 422
    assert "PARAMETRES_CHIFFREMENT_KEY" in reponse.json()["error"]["message"]


def test_mode_oauth_enregistre_pose_le_statut_jamais(
    api_client, api_session_factory, cle_chiffrement
):
    """OAuth choisi mais jamais autorisé : l'écran doit le dire, pas le supposer."""
    _brancher(api_client, _compte_persiste(api_session_factory))

    ecrit = api_client.put("/api/v1/parametres/mcp/personnel/whatsapp", json=_corps("oauth"))

    assert ecrit.status_code == 200, ecrit.text
    corps = ecrit.json()
    assert corps["mode_authentification"] == "oauth"
    assert corps["oauth_statut"] == "jamais"
    assert corps["sonde"] is None, "aucune sonde n'a encore été exécutée"


def test_le_connecteur_officiel_annonce_l_uri_de_redirection(
    api_client, api_session_factory, cle_chiffrement
):
    """L'écran doit pouvoir donner l'URI à déclarer à un serveur à clients connus."""
    _brancher(api_client, _compte_persiste(api_session_factory))

    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()

    assert liste["uri_redirection"] == uri_redirection()
    assert "/parametres/mcp/personnel/oauth/callback" in liste["uri_redirection"]


# --- 2. Refus explicite et sonde ------------------------------------------------


def test_sonde_sans_jeton_dit_autorisation_requise(
    api_client, api_session_factory, cle_chiffrement, session_locale
):
    """Le bouton Tester remplace « injoignable » par la cause réelle."""
    _brancher(api_client, _compte_persiste(api_session_factory))
    api_client.put("/api/v1/parametres/mcp/personnel/whatsapp", json=_corps("oauth"))

    sonde = api_client.post("/api/v1/parametres/mcp/personnel/whatsapp/tester")

    assert sonde.status_code == 200, sonde.text
    corps = sonde.json()
    assert corps["sonde"]["ok"] is False
    assert "Autorisation OAuth requise" in corps["sonde"]["erreur"]
    assert corps["oauth_statut"] == "jamais", "un test ne consomme pas une autorisation"

    relu = api_client.get("/api/v1/parametres/mcp/personnel").json()["connecteurs"][0]
    assert relu["sonde"]["erreur"] == corps["sonde"]["erreur"], "la sonde est persistée"


def test_le_chargeur_ecarte_un_connecteur_oauth_sans_jeton(caplog):
    """Aucun 401 silencieux : le connecteur est écarté, et le journal dit pourquoi."""
    serveur = _serveur_oauth()
    caplog.set_level(logging.WARNING, logger="app.agents.mcp")

    outils = outils_mcp_pour("agent_statistique", [serveur])

    assert outils == ()
    journal = caplog.text.lower()
    assert "autorisation oauth requise" in journal
    assert "authentifier" in journal


# --- 3. Stockage des jetons -----------------------------------------------------


def test_les_jetons_sont_chiffres_et_relus(
    api_session_factory, cle_chiffrement, session_locale
):
    """Un jeton en base n'est jamais lisible : seul le service le déchiffre."""
    serveur = _serveur_oauth()
    session = api_session_factory()
    try:
        session.add(serveur)
        session.commit()
        identifiant = serveur.id
    finally:
        session.close()

    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    stockage = StockageMcp(identifiant, client_pre_enregistre="client-carso")

    import asyncio

    jeton = OAuthToken(access_token="jeton-secret-xyz", token_type="Bearer", expires_in=3600)
    asyncio.run(stockage.set_tokens(jeton))
    asyncio.run(
        stockage.set_client_info(
            OAuthClientInformationFull(
                client_id="client-carso",
                redirect_uris=[uri_redirection()],
                token_endpoint_auth_method="none",
            )
        )
    )

    relu = asyncio.run(stockage.get_tokens())
    assert relu is not None and relu.access_token == "jeton-secret-xyz"

    info = asyncio.run(stockage.get_client_info())
    assert info is not None and info.client_id == "client-carso"

    session = api_session_factory()
    try:
        ligne = session.get(ServeurMcp, identifiant)
        assert ligne is not None
        assert ligne.oauth_etat_chiffre and "jeton-secret-xyz" not in ligne.oauth_etat_chiffre, (
            "le jeton ne doit jamais apparaître en clair dans la colonne"
        )
        assert ligne.oauth_statut == "autorise", "poser un jeton rend le connecteur autorisé"
        assert jetons_presents(ligne) is True
    finally:
        session.close()


def test_client_pre_enregistre_evite_l_enregistrement_dynamique(cle_chiffrement):
    """Le client fourni est réutilisé tel quel, sans appel d'enregistrement.

    ``issuer`` reste vide : le SDK ne réutilise sans condition que des
    identifiants non liés à un émetteur (SEP-2352) — c'est ce qui permet de
    contourner un serveur qui refuse l'enregistrement dynamique.
    """
    import asyncio

    from mcp.shared.auth import OAuthClientInformationFull

    serveur = _serveur_oauth(client_id="client-pre-enregistre")
    stockage = StockageMcp(serveur.id, client_pre_enregistre=serveur.oauth_client_id)

    info = asyncio.run(stockage.get_client_info())

    assert isinstance(info, OAuthClientInformationFull)
    assert info.client_id == "client-pre-enregistre"
    assert info.issuer is None


def test_revoquer_retire_les_jetons_sans_toucher_au_client(
    api_session_factory, cle_chiffrement, session_locale
):
    """Révoquer, c'est retirer le secret révocable — pas réenregistrer un client."""
    serveur = _serveur_oauth(jeton="jeton-a-revoquer")
    serveur.oauth_statut = "autorise"
    session = api_session_factory()
    try:
        session.add(serveur)
        session.commit()
        identifiant = serveur.id
    finally:
        session.close()

    session = api_session_factory()
    try:
        ligne = session.get(ServeurMcp, identifiant)
        assert ligne is not None
        revoquer(ligne)
    finally:
        session.close()

    session = api_session_factory()
    try:
        ligne = session.get(ServeurMcp, identifiant)
        assert ligne is not None
        assert ligne.oauth_statut == "jamais"
        assert jetons_presents(ligne) is False
    finally:
        session.close()


# --- 4. Diagnostic --------------------------------------------------------------


def test_motif_erreur_deplie_un_groupe_d_exceptions():
    """« unhandled errors in a TaskGroup » ne doit plus cacher le 401."""
    requete = httpx.Request("POST", "https://mcp.exemple.test/mcp")
    try:
        httpx.Response(401, request=requete).raise_for_status()
    except httpx.HTTPStatusError as exc:
        interne: BaseException = exc
    else:  # pragma: no cover - raise_for_status lève toujours sur 401
        raise AssertionError("401 attendu")

    motif = motif_erreur(ExceptionGroup("unhandled errors in a TaskGroup", [interne]))

    assert "401" in motif
    assert "TaskGroup" not in motif, "le message du groupe ne doit pas masquer la cause"
    assert "mcp.exemple.test" in motif


# --- 5. Encapsuleur User-Agent --------------------------------------------------


class _FluxFactice(httpx.Auth):
    """Flux minimal respectant le contrat du SDK : demande, réponse, demande.

    Il **exige** de recevoir la réponse d'httpx : si l'encapsuleur jetait le
    relais (un `async for` le fait), il lirait ``None`` et lèverait
    ``AttributeError`` — exactement la panne du 05/10 contre Meta.
    """

    def __init__(self, agent_pose: str | None = None) -> None:
        self.reponses_vues: list[int] = []
        self._agent_pose = agent_pose

    def _demandes(self) -> list[httpx.Request]:
        entetes = {"mcp-protocol-version": "2025-11-25"}
        if self._agent_pose is not None:
            entetes["user-agent"] = self._agent_pose
        return [
            httpx.Request(
                "GET",
                "https://exemple.test/.well-known/oauth-authorization-server/serveur",
                headers=entetes,
            ),
            httpx.Request("GET", "https://exemple.test/final", headers=dict(entetes)),
        ]

    def sync_auth_flow(self, requete: httpx.Request):
        for demande in self._demandes():
            reponse = yield demande
            self.reponses_vues.append(reponse.status_code)

    async def async_auth_flow(self, requete: httpx.Request):
        for demande in self._demandes():
            reponse = yield demande
            self.reponses_vues.append(reponse.status_code)


def _poser_agent(requete: httpx.Request, statut: int = 200) -> httpx.Response:
    return httpx.Response(statut, request=requete)


def test_l_encapsuleur_pose_un_agent_et_relaie_les_reponses():
    """Sans `User-Agent`, Meta répond 302 vers /unsupportedbrowser (mesuré le 05/10)."""
    import asyncio

    from app.application.services.mcp_oauth_service import AGENT_MCP, _FournisseurAvecAgent

    flux_interne = _FluxFactice()
    enveloppe = _FournisseurAvecAgent(flux_interne)
    assert enveloppe.requires_response_body is True, "le SDK lit les réponses"

    async def _piloter() -> None:
        flux = enveloppe.async_auth_flow(httpx.Request("POST", "https://exemple.test/mcp"))
        premiere = await flux.__anext__()
        assert premiere.headers["user-agent"] == AGENT_MCP

        seconde = await flux.asend(_poser_agent(premiere))
        assert seconde.headers["user-agent"] == AGENT_MCP, "chaque requête, pas seulement la 1re"

        try:
            await flux.asend(_poser_agent(seconde, 401))
        except StopAsyncIteration:
            pass
        else:  # pragma: no cover - le flux factice s'arrête après deux demandes
            raise AssertionError("fin de flux attendue")

    asyncio.run(_piloter())

    assert flux_interne.reponses_vues == [200, 401], "la réponse doit traverser l'encapsuleur"


def test_l_encapsuleur_respecte_un_agent_deja_present():
    """Un flux qui fixe son propre agent n'est pas écrasé."""
    import asyncio

    from app.application.services.mcp_oauth_service import AGENT_MCP, _FournisseurAvecAgent

    flux_interne = _FluxFactice(agent_pose="agent-du-flux/2.0")
    enveloppe = _FournisseurAvecAgent(flux_interne)

    async def _piloter() -> None:
        flux = enveloppe.async_auth_flow(httpx.Request("POST", "https://exemple.test/mcp"))
        premiere = await flux.__anext__()
        assert premiere.headers["user-agent"] == "agent-du-flux/2.0"
        assert premiere.headers["user-agent"] != AGENT_MCP

    asyncio.run(_piloter())


def test_l_encapsuleur_fonctionne_aussi_en_synchrone():
    """Le chemin `httpx.Client` doit relayer les réponses de la même façon."""
    from app.application.services.mcp_oauth_service import _FournisseurAvecAgent

    flux_interne = _FluxFactice()
    flux = _FournisseurAvecAgent(flux_interne).sync_auth_flow(
        httpx.Request("POST", "https://exemple.test/mcp")
    )
    premiere = next(flux)
    seconde = flux.send(_poser_agent(premiere))

    assert premiere.headers["user-agent"]
    assert seconde.headers["user-agent"]
    assert flux_interne.reponses_vues == [200]


# --- 6. Conseil d'échec d'enregistrement ----------------------------------------


def test_un_refus_explicite_dit_refus_et_pas_absence():
    """Meta refuse l'inscription : le dire, plutôt que « rien à appeler ».

    Mesuré le 05/10/2026 sur ``mcp.facebook.com`` : 400 ``invalid_client_metadata``.
    """
    from app.application.services.mcp_oauth_service import _conseil

    message = _conseil(
        'OAuthRegistrationError: Registration failed: 400 {"error":'
        '"invalid_client_metadata","error_description":"Dynamic registration is not'
        ' available for this client."}'
    )

    assert "refuse" in message
    assert "fermeture volontaire" in message
    assert "aucun** point" not in message
    assert "pré-enregistré" in message, "le remède doit nommer le champ à remplir"


def test_une_inscription_impossible_sans_refus_separe_les_deux_cas():
    """GitHub n'annonce aucun point d'inscription : ce n'est pas un refus.

    Mesuré le 05/10/2026 sur ``api.githubcopilot.com`` : la découverte aboutit,
    ``registration_endpoint`` est absent, ``{origine}/register`` répond 404.
    """
    from app.application.services.mcp_oauth_service import _conseil

    message = _conseil("OAuthRegistrationError: Registration failed: 404 404 page not found")

    assert "n'annonce **aucun** point d'enregistrement" in message, message
    assert "découverte OAuth aboutit" in message
    assert "refuse** l'enregistrement dynamique" not in message, (
        "rien n'a été refusé : le confondre avec Meta fait perdre du temps"
    )
    assert "pré-enregistré" in message, "le remède reste le même"
    # L'ancien message annonçait un client « fourni par le serveur » : faux ici.
    assert "qu'il vous a fourni" not in message


def test_un_echec_hors_registration_reste_tel_quel():
    """Le conseil ne doit rien inventer hors des cas qu'il connaît."""
    from app.application.services.mcp_oauth_service import _conseil

    assert _conseil("gaierdconnect: [Errno 11001]") == "gaierdconnect: [Errno 11001]"
    assert "refusé l'authentification" in _conseil("HTTPStatusError HTTP 401 https://x/mcp")
