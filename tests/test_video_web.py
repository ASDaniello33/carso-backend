"""Lecture d'une vidéo du web (``app.documents.video_web``) — **sans rien écrire**.

Aucun appel réseau : ``yt-dlp`` n'est jamais réellement lancé, seul son
**lancement** est remplacé, avec la sortie qu'il produirait. Ce qui est vérifié
ici : ce que la page propose réellement (résolutions, pistes, capacités), ce qui
est refusé, et ce qui **n'existe pas** dans ce module — aucune option d'écriture,
aucune invocation de ffmpeg, aucun téléchargement.

Les formes de données reproduites sont celles **mesurées** le 08/10/2026 sur deux
vidéos YouTube réelles : aucun format muxé, storyboards ``mhtml`` porteurs d'une
hauteur, variantes ``avc1``/``av01``/``vp9``, manifestes HLS/DASH sans fichier.
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import time
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.documents import video_web
from app.documents.video_web import commande_analyse, extraire_flux_video

MIO = 1024 * 1024
ADRESSE = "https://exemple.test/watch?v=abc"


def _page() -> dict[str, Any]:
    """Sortie ``-J`` réduite à ce qui compte : formats, titre, durée."""
    return {
        "title": "Formation professionnelle à Madagascar",
        "uploader": "CARSO TV",
        "duration": 635,
        "extractor_key": "Youtube",
        "thumbnail": "https://exemple.test/v.jpg",
        "formats": [
            # Storyboard : une « hauteur » qui n'est pas une résolution.
            {
                "format_id": "sb0",
                "height": 180,
                "ext": "mhtml",
                "vcodec": "none",
                "protocol": "mhtml",
            },
            # 720p : trois variantes réelles, tailles connues.
            {
                "format_id": "298",
                "height": 720,
                "ext": "mp4",
                "vcodec": "avc1.4D4020",
                "acodec": "none",
                "protocol": "https",
                "fps": 30,
                "tbr": 2000.0,
                "filesize": 150 * MIO,
                "url": "https://cdn.exemple.test/298",
            },
            {
                "format_id": "398",
                "height": 720,
                "ext": "mp4",
                "vcodec": "av01.0.08M.0",
                "acodec": "none",
                "protocol": "https",
                "filesize": 71 * MIO,
                "url": "https://cdn.exemple.test/398",
            },
            {
                "format_id": "302",
                "height": 720,
                "ext": "webm",
                "vcodec": "vp9",
                "acodec": "none",
                "protocol": "https",
                "filesize": 112 * MIO,
                "url": "https://cdn.exemple.test/302",
            },
            # 360p : une variante HLS (manifeste, pas un fichier) et une directe.
            {
                "format_id": "311",
                "height": 360,
                "ext": "mp4",
                "vcodec": "avc1.4D401E",
                "acodec": "none",
                "protocol": "m3u8_native",
                "url": "https://cdn.exemple.test/311.m3u8",
            },
            # 240p : flux **combiné** (image + son dans un seul fichier).
            {
                "format_id": "18",
                "height": 240,
                "ext": "mp4",
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "protocol": "https",
                "filesize": 9 * MIO,
                "url": "https://cdn.exemple.test/18",
            },
            # 144p : annoncée, mais sans adresse.
            {
                "format_id": "160",
                "height": 144,
                "ext": "mp4",
                "vcodec": "avc1.4D400C",
                "acodec": "none",
                "protocol": "https",
            },
            # Pistes audio (aucune ne doit devenir une résolution).
            {
                "format_id": "140",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 128,
                "protocol": "https",
                "filesize": 3 * MIO,
                "url": "https://cdn.exemple.test/140",
            },
            {
                "format_id": "251",
                "ext": "webm",
                "vcodec": "none",
                "acodec": "opus",
                "abr": 160,
                "protocol": "https",
                "filesize": 2 * MIO,
                "url": "https://cdn.exemple.test/251",
            },
        ],
    }


@pytest.fixture
def lecture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Remplace le lancement d'yt-dlp : la commande réelle reste observable."""
    etat: dict[str, Any] = {"commande": [], "sortie": json.dumps(_page()), "erreurs": ""}

    def executer(commande: list[str], *, timeout: float) -> tuple[str, str]:
        etat["commande"] = commande
        etat["timeout"] = timeout
        return etat["sortie"], etat["erreurs"]

    monkeypatch.setattr(video_web, "_executer", executer)
    return etat


def _lire() -> video_web.VideoFlux:
    return extraire_flux_video(ADRESSE, timeout=30)


def _hauteur(video: video_web.VideoFlux, hauteur: int) -> video_web.ResolutionFlux:
    return next(resolution for resolution in video.resolutions if resolution.hauteur == hauteur)


# --- Ce que la page propose --------------------------------------------------------


def test_les_resolutions_reelles_sont_rendues_de_la_plus_haute_a_la_plus_basse(
    lecture: dict[str, Any],
) -> None:
    video = _lire()

    assert [resolution.hauteur for resolution in video.resolutions] == [720, 360, 240, 144]
    assert video.titre == "Formation professionnelle à Madagascar"
    assert video.duree_secondes == 635
    assert video.extracteur == "Youtube"
    assert video.telechargeable is True


def test_la_taille_annoncee_additionne_image_et_son(lecture: dict[str, Any]) -> None:
    """La taille d'une résolution est celle de ce qu'il faudra télécharger."""
    video = _lire()

    # 150 Mo de piste vidéo + 3 Mo d'audio (le ``m4a`` est préféré au ``opus``).
    assert _hauteur(video, 720).taille_estimee_octets == 153 * MIO
    assert _hauteur(video, 240).taille_estimee_octets == 9 * MIO


def test_les_storyboards_et_les_pistes_audio_ne_sont_pas_des_resolutions(
    lecture: dict[str, Any],
) -> None:
    video = _lire()

    assert 180 not in [resolution.hauteur for resolution in video.resolutions]
    assert all(
        resolution.video is not None or resolution.combine is not None
        for resolution in video.resolutions
    )


def test_une_resolution_separe_image_et_son(lecture: dict[str, Any]) -> None:
    """Le cas mesuré sur YouTube : deux fichiers distincts — deux téléchargements.

    La correction de spécification du 08/10 tranche ce point : c'est le
    **navigateur du client** qui télécharge, hors de CARSO. Deux pistes séparées
    restent donc deux fichiers téléchargeables (`telechargement_possible`), et
    c'est `muxage_requis` qui dit qu'ils devront être réunis ensuite.
    """
    video = _lire()
    resolution = _hauteur(video, 720)

    assert resolution.combine is None
    assert resolution.muxage_requis is True
    assert resolution.telechargement_possible is True
    assert resolution.video is not None and resolution.audio is not None
    assert resolution.video.url == "https://cdn.exemple.test/298"
    assert resolution.video.contient_video and not resolution.video.contient_audio
    assert resolution.video.fps == 30
    assert resolution.audio is not None
    assert resolution.audio.url == "https://cdn.exemple.test/140"
    assert resolution.audio.contient_audio and not resolution.audio.contient_video


def test_un_flux_combine_est_une_seule_adresse_telechargeable(
    lecture: dict[str, Any],
) -> None:
    video = _lire()
    resolution = _hauteur(video, 240)

    assert resolution.combine is not None
    assert resolution.combine.format_id == "18"
    assert resolution.video is None and resolution.audio is None
    assert resolution.muxage_requis is False
    assert resolution.telechargement_possible is True


def test_une_piste_sans_adresse_reste_sans_lien(lecture: dict[str, Any]) -> None:
    video = _lire()
    resolution = _hauteur(video, 144)

    assert resolution.video is not None
    assert resolution.video.url is None
    assert resolution.telechargement_possible is False


def test_une_image_seule_passe_avant_un_flux_muxe(lecture: dict[str, Any]) -> None:
    """Mesuré le 08/10/2026 : le flux muxé que la plateforme liste est refusé (403).

    Quand les deux existent à la même hauteur, retenir le muxé proposerait un lien
    mort alors qu'une image exploitable est disponible. Le test place les deux sous
    la même hauteur, avec le muxé **plus lourd** (donc gagnant sur le débit, comme
    sur YouTube) : c'est bien l'image seule qui doit être retenue.
    """
    lecture["sortie"] = json.dumps(
        {
            "title": "Deux variantes",
            "extractor_key": "Youtube",
            "formats": [
                {
                    "format_id": "18",
                    "height": 360,
                    "ext": "mp4",
                    "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2",
                    "protocol": "https",
                    "url": "https://cdn.exemple.test/18",
                    "filesize": 30 * MIO,
                },
                {
                    "format_id": "135",
                    "height": 360,
                    "ext": "mp4",
                    "vcodec": "avc1.4D401E",
                    "acodec": "none",
                    "protocol": "https",
                    "url": "https://cdn.exemple.test/135",
                    "filesize": 20 * MIO,
                },
            ],
        }
    )

    video = _lire()
    resolution = _hauteur(video, 360)

    assert resolution.combine is None, "le flux muxé refusé n'est pas retenu"
    assert resolution.video is not None
    assert resolution.video.format_id == "135"


def test_un_manifeste_ne_passe_jamais_avant_un_fichier(lecture: dict[str, Any]) -> None:
    """Mesuré : un ``m3u8_native`` de la même hauteur l'emportait sur l'image mp4.

    Le manifeste se classait mieux sur le codec ; la résolution devenait alors
    intéléchargeable alors qu'un fichier existait à cette hauteur.
    """
    lecture["sortie"] = json.dumps(
        {
            "title": "Manifeste et fichier",
            "extractor_key": "Youtube",
            "formats": [
                {
                    "format_id": "hls",
                    "height": 720,
                    "ext": "mp4",
                    "vcodec": "avc1.640020",
                    "acodec": "none",
                    "protocol": "m3u8_native",
                    "url": "https://cdn.exemple.test/720.m3u8",
                },
                {
                    "format_id": "vwebm",
                    "height": 720,
                    "ext": "webm",
                    "vcodec": "vp9",
                    "acodec": "none",
                    "protocol": "https",
                    "url": "https://cdn.exemple.test/720.webm",
                    "filesize": 10 * MIO,
                },
            ],
        }
    )

    video = _lire()
    resolution = _hauteur(video, 720)

    assert resolution.video is not None
    assert resolution.video.format_id == "vwebm"
    assert resolution.telechargement_possible is True


def test_une_video_muette_reste_telechargeable(lecture: dict[str, Any]) -> None:
    """Sans piste son, l'image seule reste un fichier — et rien n'est inventé.

    Une résolution muette n'a pas de son à assembler : `muxage_requis` doit donc
    être faux, sans quoi l'écran annoncerait un assemblage qui n'a pas lieu.
    """
    lecture["sortie"] = json.dumps(
        {
            "title": "Muet",
            "extractor_key": "Generic",
            "formats": [
                {
                    "format_id": "v",
                    "height": 480,
                    "ext": "mp4",
                    "vcodec": "avc1.4D401F",
                    "acodec": "none",
                    "protocol": "https",
                    "url": "https://cdn.exemple.test/v.mp4",
                    "filesize": 4 * MIO,
                }
            ],
        }
    )

    video = _lire()
    resolution = _hauteur(video, 480)

    assert resolution.audio is None and resolution.combine is None
    assert resolution.muxage_requis is False
    assert resolution.telechargement_possible is True
    assert resolution.taille_estimee_octets == 4 * MIO


def test_un_manifeste_n_est_pas_un_lien_de_telechargement(lecture: dict[str, Any]) -> None:
    """HLS/DASH : une adresse, mais elle se lit avec un lecteur, pas un lien."""
    lecture["sortie"] = json.dumps(
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

    video = _lire()
    resolution = _hauteur(video, 720)

    assert resolution.video is not None
    assert resolution.video.url == "https://cdn.exemple.test/720.m3u8"
    assert resolution.video.telechargeable_directement is False
    assert resolution.telechargement_possible is False
    # Aucune adresse de fichier nulle part : l'écran doit le dire.
    assert video.telechargeable is False


def test_les_adresses_sont_annoncees_temporaires(lecture: dict[str, Any]) -> None:
    """Les plateformes signent leurs adresses : rien ici ne les dit permanentes."""
    assert _lire().adresse_temporaire is True


# --- Ce qui est refusé -------------------------------------------------------------


def test_aucune_resolution_exploitable_est_un_refus(lecture: dict[str, Any]) -> None:
    lecture["sortie"] = json.dumps(
        {
            "title": "Audio seul",
            "extractor_key": "Generic",
            "formats": [
                {
                    "format_id": "140",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "protocol": "https",
                    "url": "https://cdn.exemple.test/140",
                }
            ],
        }
    )

    with pytest.raises(ValidationError) as refus:
        _lire()

    assert "résolution vidéo" in refus.value.message


def test_support_absent_est_un_refus_explicite(
    monkeypatch: pytest.MonkeyPatch, lecture: dict[str, Any]
) -> None:
    monkeypatch.setattr(video_web, "paquet_disponible", lambda: False)

    with pytest.raises(ValidationError) as refus:
        _lire()

    assert "carso-backend[video]" in refus.value.message
    assert lecture["commande"] == [], "aucune commande ne part sans support installé"


def test_le_refus_du_site_est_affiche_avec_son_remède(lecture: dict[str, Any]) -> None:
    """Le vrai libellé de YouTube (apostrophe typographique) reçoit son conseil.

    Constaté le 19/10/2026 sur le service Render : le libellé servi emploie « you’re »
    (U+2019) là où sa documentation écrit « you're ». Une comparaison littérale
    laissait alors passer la ligne brute — flags et liens wiki compris.
    """
    lecture["sortie"] = ""
    lecture["erreurs"] = (
        "ERROR: [youtube] O8egem8pDxE: Sign in to confirm you’re not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication. "
        "See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ  for how to manually pass cookies"
    )

    with pytest.raises(ValidationError) as refus:
        _lire()

    assert "anti-robot" in refus.value.message
    assert "cookie" in refus.value.message.lower()
    assert "--" not in refus.value.message, "aucun drapeau d'outil à l'écran"
    assert "http" not in refus.value.message.lower(), "aucun lien de documentation à l'écran"


def test_un_motif_inconnu_ne_livre_pas_la_sortie_de_l_outil(lecture: dict[str, Any]) -> None:
    """Un libellé nouveau n'expose jamais la ligne d'yt-dlp : phrase générique.

    C'est l'invariant de l'écran : la sortie de l'extracteur sert aux journaux,
    jamais au collaborateur. Une reformulation côté site ne doit pas rouvrir la
    fuite qu'a montrée le 19/10/2026.
    """
    lecture["sortie"] = "pas du json"
    lecture["erreurs"] = (
        "ERROR: [youtube] abc: A wording the maintainers just changed. "
        "Use --extractor-args for the workaround. See https://exemple.test/x"
    )

    with pytest.raises(ValidationError) as refus:
        _lire()

    assert "CARSO n'a pas pu lire" in refus.value.message
    assert "--" not in refus.value.message
    assert "exemple.test" not in refus.value.message


def test_une_sortie_illisible_garde_le_motif_connu(lecture: dict[str, Any]) -> None:
    """Un JSON invalide n'efface pas la cause : l'extracteur absent est nommé."""
    lecture["sortie"] = "pas du json"
    lecture["erreurs"] = "ERROR: No suitable extractor found"

    with pytest.raises(ValidationError) as refus:
        _lire()

    assert "extracteur" in refus.value.message.lower()


def test_une_reponse_nulle_rend_le_motif_reel(lecture: dict[str, Any]) -> None:
    """Mesuré : sans extracteur, yt-dlp écrit ``null`` sur la sortie standard."""
    lecture["sortie"] = "null\n"
    lecture["erreurs"] = "ERROR: Unsupported URL: https://exemple.test/v"

    with pytest.raises(ValidationError) as refus:
        _lire()

    assert "prise en charge" in refus.value.message
    assert refus.value.details["sortie"].strip() == "null"


# --- Ce qui n'existe pas : aucune écriture, aucun ffmpeg ---------------------------


def test_la_commande_n_ecrit_aucun_fichier_et_n_assemble_rien(
    lecture: dict[str, Any],
) -> None:
    """La preuve par la commande : rien n'y demande d'écrire ni de muxer."""
    _lire()

    commande = lecture["commande"]
    assert commande[:3] == [sys.executable, "-m", "yt_dlp"]
    assert "--dump-single-json" in commande
    assert commande[-1] == ADRESSE
    assert "--output" not in commande, "aucune écriture demandée à yt-dlp"
    assert "--merge-output-format" not in commande, "aucun muxage"
    assert "--ffmpeg-location" not in commande, "aucun binaire externe invoqué"


def test_la_lecture_ne_charge_ni_configuration_ni_greffon_ni_playlist(
    lecture: dict[str, Any],
) -> None:
    """Ni configuration personnelle du serveur, ni greffons, ni playlist entière."""
    _lire()

    commande = lecture["commande"]
    assert "--no-config" in commande
    assert "--no-plugin-dirs" in commande
    assert "--no-playlist" in commande
    assert "--socket-timeout" in commande


def test_aucun_client_de_plateforme_n_est_impose(lecture: dict[str, Any]) -> None:
    """Le client par défaut suffit — et forcer une liste n'ajoute rien d'exploitable.

    Mesuré le 08/10/2026 : la liste ``default,android_vr`` rendait l'échelle
    complète des hauteurs **plus** le format muxé de la plateforme (``18``) — un
    format que son CDN refuse (403, tous clients et tous agents confondus). La
    commande ne doit donc demander ni client, ni aucun autre réglage d'extraction.
    """
    _lire()

    commande = lecture["commande"]
    assert "--extractor-args" not in commande, "aucun client imposé à la plateforme"
    assert "--format" not in commande, "aucun format choisi ici : la lecture rend tout"


def test_le_module_ne_peut_ni_muxer_ni_ecrire() -> None:
    """Structurel : aucun muxage ni écriture possible, faute d'outil et d'option.

    Le mot « ffmpeg » peut apparaître dans la documentation — c'est même le but,
    dire que ce module ne muxe pas. Ce qui est vérifié, c'est qu'aucun **code** ne
    l'invoque, et que le module ne contient qu'**une seule** commande : la lecture.
    """
    arbre = ast.parse(inspect.getsource(video_web))
    importes = {
        alias.name
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Import)
        for alias in noeud.names
    } | {
        noeud.module or "" for noeud in ast.walk(arbre) if isinstance(noeud, ast.ImportFrom)
    }
    assert not any("ffmpeg" in nom for nom in importes)
    # Aucune recherche de binaire externe non plus (l'ancien ``shutil.which``).
    assert "shutil" not in importes
    # Une seule ligne de commande dans tout le module : impossible d'en lancer une
    # seconde qui écrirait, muxerait ou rapatrierait quoi que ce soit.
    assert inspect.getsource(video_web).count("sys.executable") == 1


def test_la_commande_reelle_est_acceptee_par_yt_dlp() -> None:
    """La commande construite passe l'analyseur d'options **réel** d'yt-dlp.

    Mesuré le 08/10/2026 : ``--noplaylist`` est le nom de l'option dans l'API
    Python, **pas** sur la ligne de commande. Mal orthographiée, la commande est
    refusée en entier, avec un message générique — un échec que le simulateur de
    lancement utilisé partout ailleurs dans ce fichier ne peut pas voir. Ici,
    aucune simulation : ``overrideArguments`` désactive en plus la lecture des
    fichiers de configuration, donc l'analyse reste hors ligne.
    """
    from yt_dlp.options import parseOpts

    _, _, arguments = parseOpts(commande_analyse(ADRESSE)[3:])

    assert arguments == [ADRESSE]


# --- Bornes de temps et de processus ------------------------------------------------


def test_le_delai_est_une_borne_reelle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un site qui ne répond pas ne consomme pas le délai de tout le monde."""
    tues: list[int] = []

    class FauxProcessus:
        pid = 4242

        def communicate(self, timeout: float) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            tues.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FauxProcessus())

    with pytest.raises(ValidationError) as refus:
        extraire_flux_video(ADRESSE, timeout=2)

    assert refus.value.details["delai_secondes"] == 2
    assert tues == [4242], "le sous-processus est tué au délai dépassé"


def test_un_enfant_du_processus_est_tue_aussi(monkeypatch: pytest.MonkeyPatch) -> None:
    """``yt-dlp`` peut avoir des enfants : le nettoyage tue le groupe entier."""
    appels: list[list[str]] = []

    class FauxProcessus:
        pid = 777

        def communicate(self, timeout: float) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            appels.append(["kill"])

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FauxProcessus())
    monkeypatch.setattr(subprocess, "run", lambda commande, **kwargs: appels.append(commande))
    monkeypatch.setattr(video_web.os, "name", "nt")

    with pytest.raises(ValidationError):
        extraire_flux_video(ADRESSE, timeout=1)

    assert any("/T" in appel for appel in appels), "l'arbre de processus est tué"


def test_sous_processus_sans_console_sous_windows() -> None:
    """Un serveur n'a pas de console : aucun drapeau ne doit ouvrir de fenêtre."""
    drapeaux = video_web._drapeaux_processus()
    if sys.platform.startswith("win"):  # pragma: no cover - dépend du système
        assert drapeaux["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:  # pragma: no cover - dépend du système
        assert drapeaux == {}


def test_le_lancement_est_mesure_en_secondes(lecture: dict[str, Any]) -> None:
    """Le délai transmis vient de l'appelant, jamais d'une constante oubliée."""
    extraire_flux_video(ADRESSE, timeout=12.5)

    assert lecture["timeout"] == 12.5
    assert isinstance(time.monotonic(), float)  # le chronomètre du module est réel
