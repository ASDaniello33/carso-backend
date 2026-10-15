"""Lecture d'une vidéo du web par le service et la route — **lecture seule**.

Deux niveaux, du plus fin au plus exposé :

1. **service** — ``VideoWebService`` : adresse vérifiée avant tout appel, et rien
   d'autre. Ni session, ni stockage, ni fichier : le service ne connaît que la
   garde d'URL et les réglages de délai ;
2. **route** — ``GET /documents/media/flux`` : résolutions et adresses réelles,
   refus ``422`` d'une adresse privée, ``403`` hors équipe interne, ``401`` sans
   porteur hors mode lab — et la preuve qu'**aucun document** n'est créé.

Aucun appel réseau : seul le **lancement** d'yt-dlp est remplacé, avec la sortie
qu'il produirait.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.video_web_service import VideoWebService
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.documents import video_web
from tests.test_documents_telechargement import PUBLIQUE, _resoudre
from tests.test_video_web import ADRESSE, MIO, _page

PRÉFIXE = "/api/v1"
ROUTE = f"{PRÉFIXE}/documents/media/flux"


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirige la racine documentaire : la suite n'écrit jamais dans ``/storage``."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def hote_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hôte résolu vers une adresse publique : le test ne dépend pas du DNS."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre(PUBLIQUE))


@pytest.fixture
def yt_dlp_simule(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Remplace le lancement d'yt-dlp : ``commandes`` garde ce qui part réellement."""
    etat: dict[str, Any] = {"commandes": [], "sortie": None, "erreurs": ""}

    def executer(commande: list[str], *, timeout: float) -> tuple[str, str]:
        etat["commandes"].append(commande)
        return etat["sortie"] or json.dumps(_page()), etat["erreurs"]

    monkeypatch.setattr(video_web, "_executer", executer)
    return etat


# --- Service ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "adresse",
    [
        "http://127.0.0.1:8000/video",
        "http://localhost/video",
        "file:///etc/passwd",
        "https://utilisateur:motdepasse@exemple.test/video",
    ],
)
def test_une_adresse_refusee_n_atteint_jamais_l_extracteur(
    yt_dlp_simule: dict[str, Any], adresse: str
) -> None:
    """La garde anti-SSRF s'applique **avant** l'extracteur, pas après lui.

    Aucun hôte public simulé ici : ces quatre adresses doivent être refusées sur
    ce qu'elles sont — schéma, identifiants, ou adresse **littéralement** privée.
    Résoudre la boucle locale vers une adresse publique masquerait précisément le
    cas que cette garde existe pour arrêter.
    """
    with pytest.raises(ValidationError):
        VideoWebService().analyser(adresse)

    assert yt_dlp_simule["commandes"] == []


def test_le_service_rend_les_adresses_sans_rien_ecrire(
    hote_public: None, yt_dlp_simule: dict[str, Any]
) -> None:
    video = VideoWebService().analyser(ADRESSE)

    assert [resolution.hauteur for resolution in video.resolutions] == [720, 360, 240, 144]
    assert video.telechargeable is True
    assert len(yt_dlp_simule["commandes"]) == 1, "une seule lecture, aucun téléchargement"

    commande = yt_dlp_simule["commandes"][0]
    assert "--dump-single-json" in commande
    assert "--output" not in commande


def test_le_lien_complet_assemble_video_et_son(
    hote_public: None, yt_dlp_simule: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``signer_complet`` signe **un** MP4 image + son que le relais assemblera.

    Le jeton porte les **deux** adresses (image et son), la page et la hauteur :
    c'est ce qui permet au clic de livrer un seul fichier lisible, et au relais
    de reprendre le téléchargement si la source refuse l'une des adresses.
    """
    from app.documents.relais_video import verifier_jeton

    monkeypatch.setattr(
        "app.application.services.video_web_service.muxage_disponible", lambda *_: True
    )
    service = VideoWebService()
    flux = service.analyser(ADRESSE)
    resolution = next(r for r in flux.resolutions if r.hauteur == 720)
    assert resolution.muxage_requis is True

    jeton = service.signer_complet(resolution, page=ADRESSE)
    assert jeton is not None

    cible = verifier_jeton(jeton)
    assert cible.url == resolution.video.url
    assert cible.audio_url == resolution.audio.url
    assert (cible.page, cible.hauteur, cible.piste) == (ADRESSE, 720, "video")
    assert cible.reparables is True


def test_lien_complet_absent_sans_son_sans_fichier_ou_sans_ffmpeg(
    hote_public: None, yt_dlp_simule: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trois impossibilités rendent ``None`` — jamais un lien qui échouerait."""
    from app.documents import video_web
    from app.documents.video_web import PisteFlux

    monkeypatch.setattr(
        "app.application.services.video_web_service.muxage_disponible", lambda *_: True
    )
    service = VideoWebService()
    resolution = next(r for r in service.analyser(ADRESSE).resolutions if r.hauteur == 720)

    # ffmpeg absent du serveur : pas de promesse d'assemblage.
    monkeypatch.setattr(
        "app.application.services.video_web_service.muxage_disponible", lambda *_: False
    )
    assert service.signer_complet(resolution, page=ADRESSE) is None

    monkeypatch.setattr(
        "app.application.services.video_web_service.muxage_disponible", lambda *_: True
    )
    # Vidéo muette (aucune piste son) : rien à assembler.
    muette = video_web.ResolutionFlux(
        hauteur=480,
        taille_estimee_octets=4 * 1024 * 1024,
        video=PisteFlux(
            format_id="v",
            url="https://cdn.exemple.test/v.mp4",
            extension="mp4",
            protocole="https",
            vcodec="avc1.4D401F",
            acodec="",
            hauteur=480,
            fps=30,
            debit_kbits=1200,
            taille_octets=4 * 1024 * 1024,
            contient_video=True,
            contient_audio=False,
            telechargeable_directement=True,
        ),
        audio=None,
        combine=None,
    )
    assert service.signer_complet(muette, page=ADRESSE) is None

    # Piste vidéo sans adresse de fichier (manifeste) : rien à signer non plus.
    manifeste = video_web.ResolutionFlux(
        hauteur=480,
        taille_estimee_octets=None,
        video=muette.video,
        audio=resolution.audio,
        combine=None,
    )
    sans_adresse = manifeste.video
    assert sans_adresse is not None
    object.__setattr__(sans_adresse, "url", None)
    object.__setattr__(sans_adresse, "telechargeable_directement", False)
    assert service.signer_complet(manifeste, page=ADRESSE) is None


def test_le_service_ne_depend_ni_du_stockage_ni_d_un_processus() -> None:
    """Structurel : rien d'écrit, aucun processus lancé par le service lui-même.

    Le service n'a ni session ni stockage — le constructeur le montre — et son
    module ne connaît ni service documentaire, ni lancement de processus : le
    seul accès à l'assemblage ffmpeg passe par ``app.documents.muxage_video``
    (un seul propriétaire). C'est la garantie la plus forte contre la dérive « on
    remet le téléchargement ».
    """
    import inspect

    from app.application.services import video_web_service as module_service

    source = inspect.getsource(module_service)
    assert "DocumentService" not in source
    assert "DocumentUploadInput" not in source
    assert "subprocess" not in source, "l'assemblage passe par muxage_video, jamais direct"
    assert "storage" not in source.lower()

    signature = inspect.signature(VideoWebService.__init__)
    assert list(signature.parameters) == ["self"], "aucune dépendance injectée"


# --- Route ------------------------------------------------------------------------


def test_route_flux_rend_les_resolutions_et_leurs_adresses(
    api_client: TestClient,
    storage_root: Path,
    hote_public: None,
    yt_dlp_simule: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ffmpeg disponible : la résolution séparée reçoit son **lien complet**
    # (l'assemblage à la volée du relais) — fixé pour rester déterministe.
    monkeypatch.setattr(
        "app.application.services.video_web_service.muxage_disponible", lambda *_: True
    )
    reponse = api_client.get(ROUTE, params={"url": ADRESSE})

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["titre"] == "Formation professionnelle à Madagascar"
    assert corps["extracteur"] == "Youtube"
    assert corps["adresse_temporaire"] is True
    assert corps["telechargeable"] is True
    assert [resolution["hauteur"] for resolution in corps["resolutions"]] == [720, 360, 240, 144]

    haute = corps["resolutions"][0]
    assert haute["taille_estimee_octets"] == 153 * MIO
    assert haute["video"]["url"] == "https://cdn.exemple.test/298"
    assert haute["audio"]["url"] == "https://cdn.exemple.test/140"
    assert haute["combine"] is None
    # Deux pistes séparées : le **bouton principal** est le lien complet — un
    # MP4 image + son assemblé à la volée par le relais, rien n'est écrit.
    assert haute["telechargement_possible"] is True
    assert haute["muxage_requis"] is True
    assert haute["jeton_complet"], "le bouton principal assemble image et son"

    combinee = corps["resolutions"][2]
    assert combinee["combine"]["url"] == "https://cdn.exemple.test/18"
    assert combinee["telechargement_possible"] is True
    assert combinee["muxage_requis"] is False
    assert combinee["jeton_complet"] is None, "un seul fichier déjà : rien à assembler"


def test_route_flux_ne_cree_aucun_document_ni_fichier(
    api_client: TestClient, storage_root: Path, hote_public: None, yt_dlp_simule: dict[str, Any]
) -> None:
    """Le cœur de la règle : la vidéo n'entre **pas** dans CARSO."""
    assert api_client.get(ROUTE, params={"url": ADRESSE}).status_code == 200

    assert api_client.get(f"{PRÉFIXE}/documents/all").json() == []
    assert list(storage_root.rglob("*")) == []
    assert len(yt_dlp_simule["commandes"]) == 1


def test_route_flux_annonce_une_video_non_telechargeable(
    api_client: TestClient, storage_root: Path, hote_public: None, yt_dlp_simule: dict[str, Any]
) -> None:
    """Aucune adresse de fichier ⇒ l'écran doit le dire, pas afficher des boutons."""
    yt_dlp_simule["sortie"] = json.dumps(
        {
            "title": "Manifeste seul",
            "extractor_key": "Generic",
            "formats": [
                {
                    "format_id": "hls-720",
                    "height": 720,
                    "ext": "mp4",
                    "vcodec": "avc1.640020",
                    "acodec": "none",
                    "protocol": "m3u8_native",
                    "url": "https://cdn.exemple.test/720.m3u8",
                }
            ],
        }
    )

    corps = api_client.get(ROUTE, params={"url": ADRESSE}).json()

    assert corps["telechargeable"] is False
    assert corps["resolutions"][0]["telechargement_possible"] is False


@pytest.mark.parametrize(
    "adresse",
    ["http://127.0.0.1:8000/video", "http://169.254.169.254/latest/meta-data/"],
)
def test_route_flux_refuse_une_adresse_privee(
    api_client: TestClient, storage_root: Path, adresse: str
) -> None:
    """Le module ne doit pas devenir un relais vers le réseau interne (SSRF)."""
    reponse = api_client.get(ROUTE, params={"url": adresse})

    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"


def test_route_flux_reservee_a_lequipe_interne(
    api_client: TestClient, storage_root: Path, hote_public: None, yt_dlp_simule: dict[str, Any]
) -> None:
    """Mêmes règles que le reste de la gestion documentaire : l'équipe interne."""
    from app.api.deps import get_current_user
    from app.domain.identity import Utilisateur

    formateur = Utilisateur(
        email="formateur.flux@carso.test",
        nom="Flux",
        prenom="Formateur",
        password_hash="x",
        role="formateur",
        statut="actif",
    )
    api_client.app.dependency_overrides[get_current_user] = lambda: formateur
    try:
        reponse = api_client.get(ROUTE, params={"url": ADRESSE})
    finally:
        api_client.app.dependency_overrides.pop(get_current_user, None)

    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "permission_denied"


def test_route_flux_exige_un_jeton_hors_mode_lab(
    storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AUTH_SECRET`` actif ⇒ la lecture exige un porteur."""
    from app.main import app

    monkeypatch.setenv("AUTH_SECRET", "secret-test-video-carso")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            reponse = client.get(ROUTE, params={"url": ADRESSE})
    finally:
        get_settings.cache_clear()

    assert reponse.status_code == 401
    assert reponse.json()["error"]["code"] == "unauthenticated"
