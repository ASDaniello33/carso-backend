"""Relais de téléchargement vidéo — le fichier passe, **rien ne reste**.

Trois niveaux :

1. **jeton** — une adresse signée ne vaut que pour elle-même et quelques
   minutes ; toute altération est refusée ;
2. **relais** — le corps est retransmis en flux, jamais matérialisé ; une
   redirection ou une erreur de la source refuse au lieu de mentir ;
3. **route** — ``GET /documents/media/fichier`` sert un vrai téléchargement
   (``Content-Disposition: attachment``) **sans en-tête d'authentification**
   (un ``<a href>`` ne peut pas en porter), et n'écrit ni document ni fichier.

Aucun appel réseau : le transport ``httpx`` est simulé.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.services import video_web_service as module_relais
from app.application.services.video_web_service import RelaisVideoService
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.documents.relais_video import (
    CibleRelais,
    FluxRelaye,
    SourceVideoIndisponible,
    entete_disposition,
    nom_propose,
    ouvrir_flux_relaye,
    signer_jeton,
    verifier_jeton,
)
from app.documents.video_web import PisteFlux, ResolutionFlux, VideoFlux
from tests.test_documents_telechargement import PUBLIQUE, _resoudre, _transport
from tests.test_video_web import ADRESSE, _page

PRÉFIXE = "/api/v1"
FLUX = f"{PRÉFIXE}/documents/media/flux"
FICHIER = f"{PRÉFIXE}/documents/media/fichier"
FICHIER_CDN = "https://cdn.exemple.test/298"
FICHIER_FRAIS = "https://cdn.exemple.test/298-frais"
OCTETS = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def _piste_domaine(url: str, *, video: bool = True) -> PisteFlux:
    """Piste telle que ``video_web`` la rend (même forme que la source)."""
    return PisteFlux(
        format_id="298" if video else "140",
        url=url,
        extension="mp4" if video else "m4a",
        protocole="https",
        vcodec="avc1.4D401F" if video else "",
        acodec="" if video else "mp4a.40.2",
        hauteur=720 if video else None,
        fps=30 if video else None,
        debit_kbits=1200,
        taille_octets=len(OCTETS),
        contient_video=video,
        contient_audio=not video,
        telechargeable_directement=True,
    )


def _flux_analyse(adresse_video: str, *, son: str | None = None) -> VideoFlux:
    """Ce qu'une nouvelle analyse rendrait : une résolution, ses pistes."""
    return VideoFlux(
        url=ADRESSE,
        titre="Essai",
        auteur="CARSO",
        duree_secondes=19,
        extracteur="Youtube",
        miniature=None,
        resolutions=(
            ResolutionFlux(
                hauteur=720,
                taille_estimee_octets=None,
                video=_piste_domaine(adresse_video),
                audio=_piste_domaine(son, video=False) if son else None,
                combine=None,
            ),
        ),
    )


def _transport_sequential(
    reponses: list[tuple[int, bytes, dict[str, str]]],
) -> tuple[httpx.MockTransport, dict[str, Any]]:
    """Transport qui sert une réponse différente à chaque appel (403 puis 200…)."""
    etat: dict[str, Any] = {"nb": 0, "urls": []}

    def gestionnaire(requete: httpx.Request) -> httpx.Response:
        etat["urls"].append(str(requete.url))
        index = min(etat["nb"], len(reponses) - 1)
        etat["nb"] += 1
        code, corps, entetes = reponses[index]
        return httpx.Response(code, content=corps, headers=entetes)

    return httpx.MockTransport(gestionnaire), etat


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
def auth_actif(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Active l'authentification : c'est l'état réel du serveur (Bearer exigé).

    Sans ``AUTH_SECRET``, ``get_current_user`` rend un utilisateur de lab et
    **toute** route passe : le test serait alors aveugle au défaut corrigé — sur
    le serveur réel, le navigateur recevait ``401`` sur un lien qui ne peut
    porter aucun en-tête. Le secret posé ici est celui qui signe les jetons.
    """
    monkeypatch.setenv("AUTH_SECRET", "secret-de-test-relais")
    get_settings.cache_clear()
    yield Settings(auth_secret="secret-de-test-relais")
    get_settings.cache_clear()


@pytest.fixture
def yt_dlp_simule(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Remplace le lancement d'yt-dlp : ``commandes`` garde ce qui part réellement."""
    etat: dict[str, Any] = {"commandes": [], "sortie": None, "erreurs": ""}

    def executer(commande: list[str], *, timeout: float) -> tuple[str, str]:
        etat["commandes"].append(commande)
        return etat["sortie"] or json.dumps(_page()), etat["erreurs"]

    monkeypatch.setattr("app.documents.video_web._executer", executer)
    return etat


# --- Jeton ------------------------------------------------------------------------


def test_un_jeton_ne_vaut_que_pour_son_adresse_et_son_temps() -> None:
    """Aller-retour nominal, puis les trois refus : altéré, expiré, autre clé."""
    jeton = signer_jeton(FICHIER_CDN, duree=60)
    assert verifier_jeton(jeton).url == FICHIER_CDN
    assert verifier_jeton(jeton).reparables is False, "adresse seule : aucun secours"

    altere = jeton[:-2] + ("aa" if not jeton.endswith("aa") else "bb")
    with pytest.raises(ValidationError):
        verifier_jeton(altere)

    perime = signer_jeton(FICHIER_CDN, duree=-1)
    with pytest.raises(ValidationError):
        verifier_jeton(perime)

    ailleurs = Settings(auth_secret="secret-ailleurs")
    autre_cle = signer_jeton(FICHIER_CDN, duree=60, settings=ailleurs)
    with pytest.raises(ValidationError):
        verifier_jeton(autre_cle)


def test_un_jeton_porte_de_quoi_retrouver_la_piste() -> None:
    """La cible complète (page, hauteur, genre) survit à l'aller-retour.

    C'est ce contexte qui permet au relais de reprendre le fichier quand la
    source refuse une adresse expirée : sans lui, il rendrait l'erreur.
    """
    cible = CibleRelais(
        url=FICHIER_CDN,
        page=ADRESSE,
        hauteur=720,
        piste="video",
    )
    rendue = verifier_jeton(signer_jeton(cible, duree=60))

    assert rendue == cible
    assert rendue.reparables is True


def test_un_jeton_illisible_ou_non_http_est_refuse() -> None:
    for mauvais in ("", "pas-un-jeton", "a.b"):
        with pytest.raises(ValidationError):
            verifier_jeton(mauvais)

    with pytest.raises(ValidationError):
        verifier_jeton(signer_jeton("file:///etc/passwd", duree=60))


# --- Nom et en-tête ---------------------------------------------------------------


def test_le_nom_est_nettoye_et_recoit_son_extension() -> None:
    """Un titre de vidéo ne peut pas devenir un chemin ni un en-tête cassé."""
    assert nom_propose("Manizha & Ryan — 1080p", FICHIER_CDN, "video/mp4") == (
        "Manizha & Ryan — 1080p.mp4"
    )
    assert nom_propose('evil"/../x', FICHIER_CDN, "video/mp4") == "evil x.mp4"
    assert nom_propose("clip.avi", FICHIER_CDN, "video/mp4") == "clip.mp4"
    assert nom_propose("notice", FICHIER_CDN, "video/mp4") == "notice.mp4"
    assert nom_propose("deja.webm", FICHIER_CDN, "video/webm") == "deja.webm"
    assert nom_propose(None, FICHIER_CDN, "video/mp4") == "video.mp4"


def test_l_en_tete_disposition_est_un_vrai_telechargement() -> None:
    entete = entete_disposition("Formation 720p.mp4")

    assert entete.startswith("attachment;")
    assert 'filename="Formation 720p.mp4"' in entete
    assert "filename*=UTF-8''" in entete


# --- Relais ------------------------------------------------------------------------


def test_le_relais_retransmet_le_fichier_sans_l_ecrire(hote_public: None) -> None:
    """Le corps traverse en flux : type, taille et nom viennent de la source."""
    transport = _transport(
        {FICHIER_CDN: (200, OCTETS, {"content-type": "video/mp4; charset=binary"})}
    )
    with httpx.Client(transport=transport, follow_redirects=False) as client:
        flux = ouvrir_flux_relaye(FICHIER_CDN, nom="essai.mp4", client=client)
        assert isinstance(flux, FluxRelaye)
        assert flux.type_mime == "video/mp4"
        assert flux.taille_octets == len(OCTETS)
        assert flux.nom_fichier == "essai.mp4"
        assert b"".join(flux.octets) == OCTETS


def test_le_relais_refuse_une_redirection_ou_une_erreur_de_la_source(hote_public: None) -> None:
    """Une redirection n'est pas suivie (elle pourrait viser le réseau interne)."""
    transport = _transport(
        {
            FICHIER_CDN: (302, b"", {"location": "http://127.0.0.1/interne"}),
        }
    )
    with httpx.Client(transport=transport, follow_redirects=False) as client:
        with pytest.raises(ValidationError, match="redirige"):
            ouvrir_flux_relaye(FICHIER_CDN, client=client)

    transport = _transport({FICHIER_CDN: (403, b"", {"content-type": "text/plain"})})
    with httpx.Client(transport=transport, follow_redirects=False) as client:
        with pytest.raises(ValidationError, match="403"):
            ouvrir_flux_relaye(FICHIER_CDN, client=client)


def test_le_relais_ne_touche_jamais_une_adresse_privee() -> None:
    """Même signée, une adresse interne est refusée par la garde anti-SSRF."""
    with pytest.raises(ValidationError):
        ouvrir_flux_relaye("http://169.254.169.254/latest/", nom="x.mp4")


# --- Routes ------------------------------------------------------------------------


def test_la_route_flux_signe_chaque_fichier(
    api_client: TestClient, storage_root: Path, hote_public: None, yt_dlp_simule: dict[str, Any]
) -> None:
    # (jetons vérifiés avec la clé du processus : hors ``auth_actif``, pas de Bearer)
    """Chaque piste à fichier reçoit un jeton vérifiable ; un manifeste n'en a pas."""
    corps = api_client.get(FLUX, params={"url": ADRESSE}).json()

    haute = corps["resolutions"][0]
    video = verifier_jeton(haute["video"]["jeton"])
    assert video.url == haute["video"]["url"]
    assert (video.page, video.hauteur, video.piste) == (ADRESSE, haute["hauteur"], "video")

    son = verifier_jeton(haute["audio"]["jeton"])
    assert (son.url, son.hauteur, son.piste) == (haute["audio"]["url"], haute["hauteur"], "audio")

    combinee = corps["resolutions"][2]
    complet = verifier_jeton(combinee["combine"]["jeton"])
    assert (complet.url, complet.piste) == (combinee["combine"]["url"], "combine")

    manifeste = json.dumps(
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
    yt_dlp_simule["sortie"] = manifeste
    sans_fichier = api_client.get(FLUX, params={"url": ADRESSE}).json()
    assert sans_fichier["resolutions"][0]["video"]["jeton"] is None


def test_la_route_fichier_sert_un_vrai_telechargement_sans_authentification(
    api_client: TestClient,
    storage_root: Path,
    auth_actif: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le clic du navigateur : ``attachment``, aucun Bearer possible, rien gardé.

    Authentification **active** : c'est la configuration où le défaut se voyait
    (le relais était sur le router protégé, donc ``401``). La garde, elle, reste
    entière sur la route de lecture : c'est le jeton qui ouvre le relais, pas une
    exception d'authentification.
    """
    flux = FluxRelaye(
        octets=iter([OCTETS]),
        nom_fichier="Formation 720p.mp4",
        type_mime="video/mp4",
        taille_octets=len(OCTETS),
    )
    monkeypatch.setattr(module_relais, "ouvrir_flux_relaye", lambda *a, **k: flux)

    sans_porteur = api_client.get(FLUX, params={"url": ADRESSE})
    assert sans_porteur.status_code == 401, sans_porteur.text

    reponse = api_client.get(
        FICHIER,
        params={
            "jeton": signer_jeton(FICHIER_CDN, settings=auth_actif),
            "nom": "Formation 720p.mp4",
        },
    )

    assert reponse.status_code == 200, reponse.text
    assert reponse.headers["content-disposition"].startswith("attachment;")
    assert reponse.headers["cache-control"] == "private, no-store"
    assert reponse.headers["content-type"] == "video/mp4"
    assert reponse.headers["content-length"] == str(len(OCTETS))
    assert reponse.content == OCTETS
    assert list(storage_root.rglob("*")) == []


def test_le_relais_ne_cree_aucun_document(
    api_client: TestClient, storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Après un passage par le relais, le CRUD documentaire est **vide**.

    Hors authentification active (lecture ``/documents/all`` possible) : c'est
    l'autre moitié de la promesse — le fichier passe, rien ne reste.
    """
    flux = FluxRelaye(
        octets=iter([OCTETS]),
        nom_fichier="Formation 720p.mp4",
        type_mime="video/mp4",
        taille_octets=len(OCTETS),
    )
    monkeypatch.setattr(module_relais, "ouvrir_flux_relaye", lambda *a, **k: flux)

    assert api_client.get(FICHIER, params={"jeton": signer_jeton(FICHIER_CDN)}).status_code == 200
    assert api_client.get(f"{PRÉFIXE}/documents/all").json() == []
    assert list(storage_root.rglob("*")) == []


def test_la_route_fichier_refuse_un_jeton_invalide_ou_perime(
    api_client: TestClient, auth_actif: Settings
) -> None:
    """Sans jeton valide, la route n'est pas un proxy : elle refuse, sans réseau."""
    invalide = api_client.get(FICHIER, params={"jeton": "jeton-inexistant-000000"})
    assert invalide.status_code == 422

    perime = api_client.get(
        FICHIER,
        params={"jeton": signer_jeton(FICHIER_CDN, duree=-1, settings=auth_actif)},
    )
    assert perime.status_code == 422
    assert "expiré" in perime.json()["error"]["message"]


# --- Reprise après refus de la source (403/410) -------------------------------------


def test_une_adresse_expiree_est_reprise_avec_l_adresse_fraiche(hote_public: None) -> None:
    """403 de la source → réanalyse → deuxième tentative, **une seule**.

    C'est le cas réel : l'écran affiche les résolutions, l'utilisateur clique
    plus tard, et l'adresse signée par la plateforme a expiré. Le fichier arrive
    quand même, sans que l'utilisateur relance quoi que ce soit.
    """
    transport, etat = _transport_sequential(
        [(403, b"", {}), (200, OCTETS, {"content-type": "video/mp4"})]
    )
    cible = CibleRelais(url=FICHIER_CDN, page=ADRESSE, hauteur=720, piste="video")
    analyses: list[str] = []

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        service = RelaisVideoService(
            client=client,
            analyse=lambda page: (analyses.append(page), _flux_analyse(FICHIER_FRAIS))[1],
        )
        flux = service.ouvrir(signer_jeton(cible, duree=60))
        assert b"".join(flux.octets) == OCTETS

    assert etat["nb"] == 2, "une seule reprise : pas de boucle"
    assert analyses == [ADRESSE], "la page du jeton est réanalysée"
    assert etat["urls"] == [FICHIER_CDN, FICHIER_FRAIS], "la 2ᵉ tentative vise l'adresse fraîche"


def test_la_reprise_respecte_le_genre_de_la_piste(hote_public: None) -> None:
    """Un jeton de son ne doit jamais rendre l'image, même à la même hauteur."""
    transport, etat = _transport_sequential(
        [(410, b"", {}), (200, OCTETS, {"content-type": "audio/mp4"})]
    )
    cible = CibleRelais(url=FICHIER_CDN, page=ADRESSE, hauteur=720, piste="audio")

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        service = RelaisVideoService(
            client=client,
            analyse=lambda page: _flux_analyse(FICHIER_FRAIS, son="https://cdn.exemple.test/140-frais"),
        )
        flux = service.ouvrir(signer_jeton(cible, duree=60))
        assert flux.type_mime == "audio/mp4"

    assert etat["urls"][1] == "https://cdn.exemple.test/140-frais"


def test_sans_contexte_le_refus_de_la_source_est_rendu_tel_quel(hote_public: None) -> None:
    """Un jeton d'adresse seule ne peut pas être réparé : l'erreur est honnête."""
    transport, etat = _transport_sequential([(403, b"", {}), (200, OCTETS, {})])
    analyses: list[str] = []

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        service = RelaisVideoService(
            client=client, analyse=lambda page: analyses.append(page)  # pragma: no cover
        )
        with pytest.raises(SourceVideoIndisponible) as refus:
            service.ouvrir(signer_jeton(FICHIER_CDN, duree=60))

    assert refus.value.statut == 403
    assert refus.value.recuperable is True
    assert etat["nb"] == 1 and analyses == []


def test_si_la_piste_n_existe_plus_le_refus_d_origine_est_rendu(hote_public: None) -> None:
    """La source ne publie plus cette piste : on ne sert pas autre chose à sa place."""
    transport, etat = _transport_sequential([(403, b"", {}), (200, OCTETS, {})])
    cible = CibleRelais(url=FICHIER_CDN, page=ADRESSE, hauteur=1080, piste="video")

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        service = RelaisVideoService(
            client=client, analyse=lambda page: _flux_analyse(FICHIER_FRAIS)
        )
        with pytest.raises(SourceVideoIndisponible):
            service.ouvrir(signer_jeton(cible, duree=60))

    assert etat["nb"] == 1, "rien n'a été retenté : la piste 1080p n'existe pas"


def test_une_erreur_non_recuperable_ne_relance_pas_d_analyse(hote_public: None) -> None:
    """Un 404 n'est pas une adresse expirée : réanalyser ne réparerait rien."""
    transport, etat = _transport_sequential([(404, b"", {}), (200, OCTETS, {})])
    cible = CibleRelais(url=FICHIER_CDN, page=ADRESSE, hauteur=720, piste="video")
    analyses: list[str] = []

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        service = RelaisVideoService(client=client, analyse=lambda page: analyses.append(page))
        with pytest.raises(ValidationError) as refus:
            service.ouvrir(signer_jeton(cible, duree=60))

    assert not isinstance(refus.value, SourceVideoIndisponible)
    assert etat["nb"] == 1 and analyses == []


def test_une_reanalyse_impossible_laisse_le_refus_d_origine(hote_public: None) -> None:
    """Si la page ne répond plus, c'est bien la source qui a refusé — on le dit."""
    transport, etat = _transport_sequential([(403, b"", {}), (200, OCTETS, {})])
    cible = CibleRelais(url=FICHIER_CDN, page=ADRESSE, hauteur=720, piste="video")

    def analyse_qui_casse(page: str) -> VideoFlux:
        raise ValidationError("La source n'a pas répondu")

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        service = RelaisVideoService(client=client, analyse=analyse_qui_casse)
        with pytest.raises(SourceVideoIndisponible) as refus:
            service.ouvrir(signer_jeton(cible, duree=60))

    assert refus.value.statut == 403
    assert etat["nb"] == 1


# --- Sélection d'une piste par hauteur et genre -------------------------------------


def test_piste_pour_retrouve_la_bonne_piste_ou_rien() -> None:
    """La reprise retrouve **cette** résolution, ce genre, ou renonce."""
    flux = _flux_analyse(FICHIER_FRAIS, son="https://cdn.exemple.test/140-frais")

    assert flux.piste_pour(720, "video") is not None
    assert flux.piste_pour(720, "audio") is not None
    assert flux.piste_pour(720, "combine") is None, "aucun fichier complet ici"
    assert flux.piste_pour(1080, "video") is None, "autre résolution : rien"



def test_le_service_de_relais_verifie_le_jeton_avant_tout_appel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un jeton invalide n'atteint pas la source."""
    appels = {"nb": 0}

    def interdit(*args: Any, **kwargs: Any) -> FluxRelaye:  # pragma: no cover - ne doit pas tourner
        appels["nb"] += 1
        raise AssertionError("la source ne doit pas être appelée")

    monkeypatch.setattr(module_relais, "ouvrir_flux_relaye", interdit)

    with pytest.raises(ValidationError):
        RelaisVideoService().ouvrir("jeton-inexistant-000000")

    assert appels["nb"] == 0
