"""Rapatriement contrôlé d'une image externe (``app.documents.telechargement``).

Aucun appel réseau : le transport est un ``httpx.MockTransport``, et la
résolution DNS est remplacée — sans quoi les gardes anti-SSRF ne seraient pas
testables hors ligne.

Ce que ces tests verrouillent, dans l'ordre du danger :

1. une adresse qui n'est pas ``http(s)``, ou qui porte des identifiants, est
   refusée **avant** toute requête ;
2. un hôte public dans l'URL mais privé, loopback ou lien-local après
   résolution est refusé (SSRF) ;
3. une redirection est revalidée comme une nouvelle URL (un hôte public ne peut
   pas renvoyer vers la boucle locale) ;
4. la taille est bornée sur ``Content-Length`` **et** en flux ;
5. le type enregistré vient de la **signature** des octets : une page HTML
   d'erreur servie en 200 est refusée, et un ``Content-Type`` menteur n'autorise
   rien ;
6. une **vidéo** est refusée avec son motif — elle ne rentre jamais dans CARSO.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.errors import ValidationError
from app.documents.telechargement import MAX_REDIRECTIONS, recuperer_media

# --- Signatures binaires réelles -------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 20
WEBP = b"RIFF" + (200).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 12
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16
WEBM = b"\x1a\x45\xdf\xa3" + b"\x01\x00\x00\x00" + b"\x00" * 12
OGV = b"OggS" + b"\x00\x02" + b"\x00" * 18
PAGE_HTML = b"<!doctype html><html><body>Erreur 404</body></html>"
PUBLIQUE = "93.184.216.34"


def _resoudre(*adresses: str) -> Callable[..., list[Any]]:
    """Remplace ``socket.getaddrinfo`` par une résolution fixe."""

    def faux(hote: str, port: Any, **kwargs: Any) -> list[Any]:
        del hote, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (adresse, port or 0))
            for adresse in adresses
        ]

    return faux


@pytest.fixture
def hote_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un hôte résolu vers une adresse publique : les tests ne dépendent pas du DNS."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre(PUBLIQUE))


_Reponses = dict[str, tuple[int, bytes, dict[str, str]]] | Callable[[httpx.Request], httpx.Response]


def _transport(reponses: _Reponses) -> httpx.MockTransport:
    if callable(reponses):
        return httpx.MockTransport(reponses)
    return httpx.MockTransport(
        lambda requete: httpx.Response(
            reponses[str(requete.url)][0],
            content=reponses[str(requete.url)][1],
            headers=reponses[str(requete.url)][2],
        )
    )


def _recuperer(url: str, transport: httpx.MockTransport, **kwargs: Any):
    with httpx.Client(transport=transport, follow_redirects=False) as client:
        plafond = kwargs.pop("max_bytes", 1_000_000)
        return recuperer_media(url, max_bytes=plafond, client=client, **kwargs)


# --- Type réel --------------------------------------------------------------


@pytest.mark.parametrize(
    ("contenu", "extension", "mime"),
    [
        (PNG, "png", "image/png"),
        (JPEG, "jpg", "image/jpeg"),
        (GIF, "gif", "image/gif"),
        (WEBP, "webp", "image/webp"),
    ],
)
def test_le_type_reel_vient_de_la_signature(
    hote_public: None, contenu: bytes, extension: str, mime: str
) -> None:
    """L'extension enregistrée est celle du contenu, jamais celle annoncée."""
    transport = _transport(
        {"https://exemple.org/media": (200, contenu, {"content-type": "application/octet-stream"})}
    )
    media = _recuperer("https://exemple.org/media", transport)
    assert (media.extension, media.mime_type) == (extension, mime)
    assert media.nom_fichier == f"media.{extension}"
    assert media.taille_octets == len(contenu)
    assert media.octets == contenu


@pytest.mark.parametrize(
    ("contenu", "mime"),
    [
        (MP4, "video/mp4"),
        (WEBM, "video/webm"),
        (OGV, "video/ogg"),
    ],
)
def test_une_video_est_refusee_jamais_rapatriee(
    hote_public: None, contenu: bytes, mime: str
) -> None:
    """Le refus est catégorique, quelle que soit la vidéo : rien n'entre dans CARSO."""
    transport = _transport(
        {"https://exemple.org/media": (200, contenu, {"content-type": mime})}
    )
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/media", transport)
    assert "vidéo" in str(erreur.value)
    assert erreur.value.details.get("url") == "https://exemple.org/media"


def test_le_content_type_annonce_par_le_site_ne_fait_pas_foi(hote_public: None) -> None:
    """Un site peut annoncer ``text/html`` sur une vraie image : le contenu décide."""
    transport = _transport(
        {
            "https://exemple.org/telecharger": (
                200,
                JPEG,
                {"content-type": "text/html; charset=utf-8"},
            )
        }
    )
    media = _recuperer("https://exemple.org/telecharger", transport)
    assert media.mime_type == "image/jpeg"
    assert media.nom_fichier == "telecharger.jpg"


def test_une_page_html_servie_en_200_est_refusee(hote_public: None) -> None:
    """Le cas réel : le site répond « 200 » avec une page au lieu du fichier."""
    transport = _transport(
        {"https://exemple.org/photo.jpg": (200, PAGE_HTML, {"content-type": "image/jpeg"})}
    )
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/photo.jpg", transport)
    message = str(erreur.value)
    assert "n'est pas une image" in message
    assert erreur.value.details.get("url") == "https://exemple.org/photo.jpg"


def test_le_nom_du_fichier_est_sur_et_garde_son_extension_reelle(hote_public: None) -> None:
    """Un segment d'URL douteux ne devient pas un nom de fichier douteux."""
    transport = _transport(
        {
            "https://exemple.org/wp-content/uploads/photo%20finale%2Fv2.php": (
                200,
                PNG,
                {"content-type": "image/png"},
            ),
            "https://exemple.org/telecharger/": (200, PNG, {"content-type": "image/png"}),
        }
    )
    adresse = "https://exemple.org/wp-content/uploads/photo%20finale%2Fv2.php"
    premier = _recuperer(adresse, transport)
    assert premier.nom_fichier.endswith(".png")
    assert "/" not in premier.nom_fichier and "\\" not in premier.nom_fichier
    # Un chemin sans nom de fichier rend un nom générique, jamais un nom vide.
    second = _recuperer("https://exemple.org/telecharger/", transport)
    assert second.nom_fichier == "media-exemple.png"


def test_le_nom_demande_par_l_utilisateur_est_respecte(hote_public: None) -> None:
    transport = _transport({"https://exemple.org/a.png": (200, PNG, {"content-type": "image/png"})})
    media = _recuperer("https://exemple.org/a.png", transport, nom="Rizières de Madagascar")
    assert media.nom_fichier == "Rizières de Madagascar.png"


# --- Adresses refusées avant toute requête ---------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "file:///etc/passwd",
        "ftp://exemple.org/photo.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "https://",
        "https://user:motdepasse@exemple.org/photo.png",
    ],
)
def test_adresse_refusee_avant_toute_requete(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Aucune requête n'est émise : le refus précède l'appel réseau."""
    appels = {"nb": 0}

    def faux_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        appels["nb"] += 1
        return []

    monkeypatch.setattr(socket, "getaddrinfo", faux_getaddrinfo)
    transport = _transport(lambda requete: pytest.fail(f"requête émise vers {requete.url}"))
    with pytest.raises(ValidationError):
        _recuperer(url, transport)
    assert appels["nb"] == 0


@pytest.mark.parametrize(
    "adresse",
    [
        "127.0.0.1",
        "10.0.0.7",
        "192.168.1.20",
        "172.16.5.4",
        "169.254.169.254",  # métadonnées d'hébergeur
        "::1",
        "fd00::1",
        "0.0.0.0",
    ],
)
def test_hote_non_public_refuse(monkeypatch: pytest.MonkeyPatch, adresse: str) -> None:
    """SSRF : ni boucle locale, ni réseau interne, ni adresse de métadonnées."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre(adresse))
    transport = _transport(lambda requete: pytest.fail(f"requête émise vers {requete.url}"))
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://interne.exemple/photo.png", transport)
    message = str(erreur.value)
    assert "pas publique" in message
    assert erreur.value.details.get("adresse") == adresse


def test_un_hote_public_et_prive_a_la_fois_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une seule adresse privée suffit à refuser : le DNS peut jouer double jeu."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre(PUBLIQUE, "10.1.2.3"))
    transport = _transport(lambda requete: pytest.fail("requête émise malgré l'adresse privée"))
    with pytest.raises(ValidationError):
        _recuperer("https://exemple.org/photo.png", transport)


def test_hote_introuvable_est_dit(monkeypatch: pytest.MonkeyPatch) -> None:
    def echoue(*args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        raise socket.gaierror("nom inconnu")

    monkeypatch.setattr(socket, "getaddrinfo", echoue)
    transport = _transport(lambda requete: pytest.fail("requête émise"))
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://inconnu.exemple/photo.png", transport)
    assert "introuvable" in str(erreur.value)


# --- Redirections -----------------------------------------------------------


def test_redirection_vers_un_hote_interne_refusee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le piège classique : un hôte public renvoie vers la boucle locale."""
    vues: list[str] = []

    def resoudre(hote: str, port: Any, **kwargs: Any) -> list[Any]:
        del kwargs
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1" if "interne" in hote else PUBLIQUE, port or 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resoudre)

    def route(requete: httpx.Request) -> httpx.Response:
        vues.append(str(requete.url))
        if requete.url.host == "exemple.org":
            return httpx.Response(302, headers={"location": "http://interne.local/secret"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/photo.png", _transport(route))
    assert "pas publique" in str(erreur.value)
    assert vues == ["https://exemple.org/photo.png"], "la cible interne n'est jamais appelée"


def test_redirection_legitime_suivie_et_url_finale_conservee(hote_public: None) -> None:
    def route(requete: httpx.Request) -> httpx.Response:
        if requete.url.path == "/depart":
            return httpx.Response(301, headers={"location": "/media/photo.png"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    media = _recuperer("https://exemple.org/depart", _transport(route))
    assert media.url_source == "https://exemple.org/depart"
    assert media.url_finale == "https://exemple.org/media/photo.png"
    assert media.nom_fichier == "photo.png"


def test_trop_de_redirections_refuse(hote_public: None) -> None:
    compteur = {"nb": 0}

    def route(requete: httpx.Request) -> httpx.Response:
        compteur["nb"] += 1
        return httpx.Response(302, headers={"location": f"/saut-{compteur['nb']}"})

    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/depart", _transport(route))
    assert "Trop de redirections" in str(erreur.value)
    assert compteur["nb"] == MAX_REDIRECTIONS + 1


def test_redirection_sans_destination_refuse(hote_public: None) -> None:
    transport = _transport(lambda requete: httpx.Response(302))
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/photo.png", transport)
    assert "Redirection sans destination" in str(erreur.value)


# --- Taille et statuts ------------------------------------------------------


def test_content_length_au_dela_du_plafond_refuse(hote_public: None) -> None:
    """Le refus arrive avant de lire le corps : rien n'est rapatrié pour rien."""

    def route(requete: httpx.Request) -> httpx.Response:
        del requete
        return httpx.Response(
            200,
            content=PNG,
            headers={"content-type": "image/png", "content-length": "5000000"},
        )

    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/enorme.png", _transport(route), max_bytes=1000)
    assert "trop volumineux" in str(erreur.value)
    assert erreur.value.details.get("max_octets") == 1000


def test_plafond_applique_en_flux_meme_sans_content_length(hote_public: None) -> None:
    """Un ``Content-Length`` absent ou menteur ne protège de rien : on compte."""
    gros = PNG + b"\x00" * 5000
    transport = _transport(
        {"https://exemple.org/gros.png": (200, gros, {"content-type": "image/png"})}
    )
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/gros.png", transport, max_bytes=1000)
    assert "trop volumineux" in str(erreur.value)


def test_reponse_du_site_en_erreur_est_dite(hote_public: None) -> None:
    transport = _transport(
        {"https://exemple.org/photo.png": (404, b"", {"content-type": "text/plain"})}
    )
    with pytest.raises(ValidationError) as erreur:
        _recuperer("https://exemple.org/photo.png", transport)
    assert "répondu 404" in str(erreur.value)
    assert erreur.value.details.get("statut") == 404


def test_le_client_par_defaut_declare_carso_et_ne_suit_pas_les_redirections(
    monkeypatch: pytest.MonkeyPatch, hote_public: None
) -> None:
    """Sans client injecté, le module construit le sien : identité déclarée et
    redirections **à la main** (sinon un saut pourrait viser le réseau interne
    sans être revalidé)."""
    vues: list[httpx.Request] = []
    captures: list[dict[str, Any]] = []
    vrai_client = httpx.Client

    def route(requete: httpx.Request) -> httpx.Response:
        vues.append(requete)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    def faux_client(**kwargs: Any) -> httpx.Client:
        captures.append(kwargs)
        return vrai_client(transport=_transport(route), **kwargs)

    monkeypatch.setattr(httpx, "Client", faux_client)
    media = recuperer_media("https://exemple.org/photo.png", max_bytes=1_000_000)

    assert media.extension == "png"
    assert captures[0].get("follow_redirects") is False
    assert "CARSO" in captures[0]["headers"]["User-Agent"]
    assert "image" in captures[0]["headers"]["Accept"]
    assert "CARSO" in vues[0].headers.get("user-agent", "")
