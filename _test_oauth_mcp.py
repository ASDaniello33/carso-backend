"""Test réel — le chemin MCP livré face à un serveur qui exige OAuth.

But : reproduire ce que vit un connecteur « WhatsApp Business Tools » de Meta
(URL fournie par l'utilisateur) en passant **par le code livré**
(``app.agents.mcp.outils_mcp_pour``), puis prouver par les métadonnées du
serveur que l'authentification demandée est OAuth 2.0 avec enregistrement
dynamique et redirection navigateur.

Ce script est né de la **vérification en lecture seule** (incrément 46), quand
le schéma ne portait aucun champ OAuth : sa section 1 constatait alors cette
absence. Le support livré en incrément 47 l'a invalidée ; la section 1 garde
désormais la présence des champs et le branchement `auth`, pour qu'une
régression de schéma se voie ici plutôt qu'en production. Le reste du script
n'a pas changé : il reste la preuve, contre le **vrai** serveur Meta, que
aucun outil ne se charge sans autorisation.

Aucune écriture : ni base, ni connecteur, ni inscription OAuth (le
``registration_endpoint`` est lu, jamais appelé).

Exécution : ./.venv/Scripts/python.exe _test_oauth_mcp.py
"""

import logging
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

AGENT = "agent_generaliste_readonly"
URL_META = "https://mcp.facebook.com/whatsapp_business_tools"

echecs: list[str] = []


def verifier(condition: bool, message: str) -> None:
    if condition:
        print(f"  OK    {message}")
    else:
        print(f"  ECHEC {message}")
        echecs.append(message)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


capture = _Capture()
logging.getLogger("app.agents.mcp").addHandler(capture)

from app.agents.mcp import _connexion, outils_mcp_pour  # noqa: E402
from app.domain.mcp import ServeurMcp  # noqa: E402

print("\n1. Ce que le schéma transporte (état après l'incrément 47)")
serveur = ServeurMcp(
    nom="whatsapp_business_tools",
    transport="streamable_http",
    url=URL_META,
    agents=[AGENT],
    actif=True,
)
champs = sorted(c for c in ServeurMcp.__table__.columns.keys())
print(f"  info  colonnes de ServeurMcp : {champs}")
# Avant l'incrément 47, cette assertion disait l'inverse (« aucune colonne
# OAuth ») : c'était le constat de la vérification. Elle protège maintenant la
# présence des champs — un retrait de schéma se verrait ici.
verifier(
    {
        "mode_authentification",
        "oauth_client_id",
        "oauth_etat_chiffre",
        "oauth_statut",
        "oauth_derniere_erreur",
    }
    <= set(champs),
    "le schéma porte les champs OAuth (mode, client, jetons, statut, erreur)",
)
connexion = _connexion(serveur)
print(f"  info  connexion construite (mode aucune) : {sorted(connexion)}")
verifier(
    "auth" not in connexion,
    "un connecteur sans OAuth ne reçoit aucun authentificateur (`auth`)",
)
verifier(
    sorted(connexion) == ["transport", "url"],
    "sans en-tête : seuls transport et url sont transmis",
)
serveur.mode_authentification = "oauth"
connexion_oauth = _connexion(serveur)
print(f"  info  connexion construite (mode oauth) : {sorted(connexion_oauth)}")
verifier(
    "auth" in connexion_oauth,
    "un connecteur OAuth reçoit un authentificateur branché sur le transport",
)

print("\n2. Le serveur Meta exige OAuth (lecture seule)")
reponse = httpx.post(
    URL_META,
    json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "carso-verif", "version": "0"},
        },
    },
    headers={"Accept": "application/json, text/event-stream"},
    timeout=30,
    follow_redirects=True,
)
print(f"  info  status={reponse.status_code} corps={reponse.text[:200]}")
verifier(reponse.status_code == 401, "sans jeton, le serveur répond 401")
www = reponse.headers.get("www-authenticate", "")
print(f"  info  WWW-Authenticate: {www}")
verifier("resource_metadata=" in www, "il annonce la découverte OAuth (resource_metadata)")
verifier("scope=" in www, "il annonce les scopes attendus")

prm = httpx.get(
    "https://mcp.facebook.com/.well-known/oauth-protected-resource/whatsapp_business_tools",
    timeout=30,
)
metadonnees = prm.json()
print(f"  info  resource={metadonnees.get('resource')}")
print(f"  info  authorization_servers={metadonnees.get('authorization_servers')}")
verifier(prm.status_code == 200, "les métadonnées de ressource protégée sont publiées")
verifier(bool(metadonnees.get("authorization_servers")), "un serveur d'autorisation est désigné")

as_url = str(metadonnees["authorization_servers"][0])
as_meta = httpx.get(
    "https://mcp.facebook.com/.well-known/oauth-authorization-server/whatsapp_business_tools",
    timeout=30,
).json()
print(f"  info  authorization_endpoint={as_meta.get('authorization_endpoint')}")
print(f"  info  token_endpoint={as_meta.get('token_endpoint')}")
print(f"  info  registration_endpoint={as_meta.get('registration_endpoint')}")
print(f"  info  code_challenge_methods={as_meta.get('code_challenge_methods_supported')}")
verifier(
    bool(as_meta.get("registration_endpoint")),
    "l'enregistrement dynamique du client (DCR) est nécessaire",
)
verifier(
    as_meta.get("code_challenge_methods_supported") == ["S256"],
    "PKCE S256 exigé",
)
verifier(
    as_meta.get("token_endpoint_auth_methods_supported") == ["none"],
    "client public : aucun secret partagé ne peut être préconfiguré",
)
verifier(
    str(as_meta.get("authorization_endpoint", "")).startswith("https://www.facebook.com/"),
    "l'autorisation passe par une redirection navigateur chez Facebook",
)
verifier(
    as_url == as_meta.get("issuer"),
    "l'émetteur désigné par la ressource est bien celui des métadonnées",
)

print("\n3. Le code livré face à ce serveur (chemin réel de l'application)")
outils = outils_mcp_pour(AGENT, [serveur])
print(f"  info  outils chargés : {len(outils)}")
verifier(outils == (), "aucun outil n'est chargé — l'agent perd le connecteur")
journal = " | ".join(capture.messages)
print(f"  info  journal : {journal[:300]}")
verifier(bool(capture.messages), "l'échec est journalisé (jamais silencieux)")
# Deux motifs possibles selon l'état livré : un connecteur OAuth **sans jeton**
# est écarté avec la cause exacte (« autorisation OAuth requise »), et un
# connecteur tenté sans autorisation journalise l'échec du serveur
# (« injoignable » + 401). Les deux disent la même chose à l'écran : pourquoi
# l'agent n'a pas ces outils. Avant l'incrément 47, seul le second existait.
verifier(
    "autorisation OAuth requise" in journal or "injoignable" in journal,
    "le motif nomme la cause exacte (autorisation absente, ou échec du serveur)",
)

print("\n4. Ce que la pile installée permet déjà (preuve par introspection)")
from langchain_mcp_adapters.sessions import StreamableHttpConnection  # noqa: E402
from mcp.client.auth import OAuthClientProvider  # noqa: E402

champs_adaptateur = sorted(StreamableHttpConnection.__annotations__)
print(f"  info  clés acceptées par l'adaptateur : {champs_adaptateur}")
verifier("auth" in champs_adaptateur, "l'adaptateur accepte un authentificateur `auth`")
import httpx as _httpx  # noqa: E402

verifier(
    isinstance(OAuthClientProvider, type) and issubclass(OAuthClientProvider, _httpx.Auth),
    "OAuthClientProvider du SDK est un httpx.Auth (compatible de bout en bout)",
)

from importlib.metadata import version  # noqa: E402

print(f"  info  version du SDK mcp : {version('mcp')}")
print(f"\n{'ECHECS: ' + str(len(echecs)) if echecs else 'TOUT OK'}")
if echecs:
    for e in echecs:
        print(f"  - {e}")
sys.exit(1 if echecs else 0)
