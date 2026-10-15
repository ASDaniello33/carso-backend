"""Connecteur MCP derrière un certificat **auto-signé** — la poignée de main.

Constat du 09/10/2026, sur une passerelle MCP personnelle hébergée en https
(« Personal MCP Gateway ») : l'enregistrement d'un connecteur échouait avec

    ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
    self-signed certificate (_ssl.c:1006)

Le client MCP (comme tout client httpx) refuse une chaîne de certification qu'il
ne peut pas rattacher à une autorité connue. Trois politiques sont donc
verrouillées ici, contre un **vrai serveur TLS** portant un certificat
auto-signé généré pour le test — pas une simulation de la vérification :

1. défaut : la vérification est stricte, la connexion est refusée (c'est le
   défaut sûr, et le symptôme exact rapporté) ;
2. ``MCP_SSL_CA_BUNDLE`` : le certificat de la passerelle est ajouté aux
   autorités de confiance — la poignée de main passe et les outils sont lus ;
3. ``MCP_SSL_VERIFY=false`` : plus aucune vérification (développement), la
   poignée de main passe aussi, et l'avertissement est journalisé **une fois**.

Le serveur de test parle le protocole MCP en version minimale (``initialize`` puis
``tools/list``) : la preuve porte donc sur la connexion réelle, outils compris.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.agents.mcp import (
    _verification_httpx,
    fabrique_client_mcp,
    politique_tls_mcp,
    sonder_serveur,
)
from app.core.config import Settings, get_settings
from app.domain.mcp import ServeurMcp

CERTIFICAT_AUTOSIGNE = "self-signed certificate"
OUTIL = "salut"


# --- Certificat auto-signé ---------------------------------------------------------


def _certificat(racine: Path) -> tuple[Path, Path]:
    """Certificat auto-signé pour ``127.0.0.1`` (clé comprise), écrit sur disque."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nom = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    maintenant = datetime.now(UTC)
    certificat = (
        x509.CertificateBuilder()
        .subject_name(nom)
        .issuer_name(nom)
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - timedelta(minutes=1))
        .not_valid_after(maintenant + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]),
            critical=False,
        )
        .sign(cle, hashes.SHA256())
    )

    chemin_certificat = racine / "passerelle.pem"
    chemin_cle = racine / "passerelle.key"
    chemin_certificat.write_bytes(certificat.public_bytes(serialization.Encoding.PEM))
    chemin_cle.write_bytes(
        cle.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return chemin_certificat, chemin_cle


# --- Passerelle MCP minimale (TLS + protocol suffisant) ---------------------------


class _Passerelle(BaseHTTPRequestHandler):
    """Serveur MCP minimal : ``initialize`` et ``tools/list``, rien de plus."""

    def do_POST(self) -> None:  # noqa: N802 - nom imposé par http.server
        longueur = int(self.headers.get("content-length") or 0)
        try:
            demande = json.loads(self.rfile.read(longueur) or b"{}")
        except ValueError:
            self.send_error(400)
            return

        methode = demande.get("method")
        if methode == "initialize":
            resultat: dict[str, Any] = {
                "protocolVersion": demande.get("params", {}).get("protocolVersion")
                or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "passerelle-personnelle", "version": "1.0"},
            }
        elif methode == "tools/list":
            resultat = {
                "tools": [
                    {
                        "name": OUTIL,
                        "description": "Outil de la passerelle personnelle.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        else:
            resultat = {}

        corps = json.dumps(
            {"jsonrpc": "2.0", "id": demande.get("id"), "result": resultat}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self) -> None:  # noqa: N802 - nom imposé par http.server
        # Comme la passerelle Parallel Search observée le 04/10 : pas de flux
        # SSE en GET (405). ``tools/list`` passe par le POST — c'est suffisant.
        self.send_error(405)

    def log_message(self, *args: Any) -> None:  # pragma: no cover - bruit
        return


@pytest.fixture
def passerelle(tmp_path: Path) -> Iterator[str]:
    """URL https d'une passerelle MCP à certificat auto-signé (fil séparé)."""
    chemin_certificat, chemin_cle = _certificat(tmp_path)
    contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexte.load_cert_chain(certfile=str(chemin_certificat), keyfile=str(chemin_cle))

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), _Passerelle)
    serveur.socket = contexte.wrap_socket(serveur.socket, server_side=True)
    fil = threading.Thread(target=serveur.serve_forever, name="passerelle-test", daemon=True)
    fil.start()
    try:
        yield f"https://127.0.0.1:{serveur.server_port}/mcp"
    finally:
        serveur.shutdown()
        serveur.server_close()


@pytest.fixture
def certificat(passerelle: str, tmp_path: Path) -> Path:  # noqa: ARG001 - ordre imposé
    """Le certificat de la passerelle (généré avec elle)."""
    return tmp_path / "passerelle.pem"


def _connecteur(url: str) -> ServeurMcp:
    """Connecteur en mémoire : même forme que celui enregistré depuis l'interface."""
    return ServeurMcp(
        nom="perso",
        transport="streamable_http",
        url=url,
        agents=["agent_generaliste_readonly"],
        arguments=[],
        en_tetes_chiffres=None,
        mode_authentification="aucune",
        actif=True,
    )


@pytest.fixture
def reglages_mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Environnement des deux réglages TLS MCP, purgé à la fin du test."""
    monkeypatch.delenv("MCP_SSL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("MCP_SSL_VERIFY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Politique ---------------------------------------------------------------------


def test_la_politique_par_defaut_est_la_verification_stricte(reglages_mcp: None) -> None:
    """Défaut sûr : rien n'est désactivé et la fabrique du SDK est rendue telle quelle."""
    from mcp.client.streamable_http import create_mcp_http_client

    assert politique_tls_mcp(Settings()) is True
    assert fabrique_client_mcp(Settings()) is create_mcp_http_client


def test_un_bundle_prime_sur_la_desactivation(
    passerelle: str,  # noqa: ARG001 - le certificat est écrit avec elle
    certificat: Path,
    reglages_mcp: None,
) -> None:
    """On fait confiance à un certificat (recommandé) plutôt que de tout accepter."""
    bundle = certificat
    reglages = Settings(mcp_ssl_ca_bundle=str(bundle), mcp_ssl_verify=False)
    assert politique_tls_mcp(reglages) == str(bundle)

    # Un chemin devient un contexte SSL : « je fais confiance à ce certificat »,
    # jamais « je ne vérifie rien » — l'hôte reste vérifié (et l'on évite
    # ``verify=<str>``, déprécié par httpx).
    assert _verification_httpx(False) is False
    contexte = _verification_httpx(str(bundle))
    assert isinstance(contexte, ssl.SSLContext)
    assert contexte.verify_mode == ssl.CERT_REQUIRED
    assert contexte.check_hostname is True


def test_un_chemin_de_certificat_faux_se_dit_clairement(
    reglages_mcp: None, tmp_path: Path
) -> None:
    """Un chemin inexistant est un réglage à corriger, pas une panne de la passerelle."""
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError, match="introuvable"):
        _verification_httpx(str(tmp_path / "absent.pem"))


# --- Poignée de main réelle --------------------------------------------------------


def test_sans_reglage_la_passerelle_auto_signee_est_refusee(
    passerelle: str, reglages_mcp: None
) -> None:
    """Le symptôme rapporté, reproduit : personne ne désactive TLS par surprise."""
    rapport = sonder_serveur(_connecteur(passerelle))

    assert rapport["ok"] is False
    assert CERTIFICAT_AUTOSIGNE in " ".join([str(rapport["erreur"]), *rapport["causes"]])


def test_le_certificat_de_la_passerelle_suffit_a_connecter(
    passerelle: str, certificat: Path, reglages_mcp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MCP_SSL_CA_BUNDLE`` : confiance à **ce** certificat, outils lus pour de vrai."""
    monkeypatch.setenv("MCP_SSL_CA_BUNDLE", str(certificat))
    get_settings.cache_clear()

    rapport = sonder_serveur(_connecteur(passerelle))

    assert rapport["ok"] is True, rapport
    assert rapport["outils"] == [f"perso_{OUTIL}"], "préfixés par le connecteur"


def test_desactiver_la_verification_connecte_et_le_dit(
    passerelle: str,
    reglages_mcp: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``MCP_SSL_VERIFY=false`` : la poignée de main passe, l'avertissement aussi."""
    monkeypatch.setenv("MCP_SSL_VERIFY", "false")
    get_settings.cache_clear()

    with caplog.at_level("WARNING", logger="app.agents.mcp"):
        rapport = sonder_serveur(_connecteur(passerelle))

    assert rapport["ok"] is True, rapport
    assert rapport["outils"] == [f"perso_{OUTIL}"]
    assert "MCP_SSL_VERIFY=false" in caplog.text
    assert "MCP_SSL_CA_BUNDLE" in caplog.text, "l'alternative recommandée est nommée"


def test_le_connecteur_reseau_porte_la_fabrique_tls(reglages_mcp: None) -> None:
    """Le transport reçoit la fabrique ; un connecteur ``stdio`` n'en a pas besoin."""
    from app.agents.mcp import _connexion

    reseau = _connexion(_connecteur("https://exemple.test/mcp"))
    assert "httpx_client_factory" in reseau

    stdio = _connexion(
        ServeurMcp(
            nom="local",
            transport="stdio",
            commande="python",
            url=None,
            agents=[],
            arguments=[],
            actif=True,
        )
    )
    assert "httpx_client_factory" not in stdio
