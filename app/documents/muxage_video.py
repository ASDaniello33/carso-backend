"""Assemblage à la volée—piste vidéo + piste son → **un seul** fichier lisible.

Pourquoi ce module existe—constaté en usage réel (09/10/2026, mesuré à
nouveau le même jour sur six clients yt-dlp) :

- les plateformes ne publient plus de fichier image+son (aucun format muxé
  listé via le client par défaut ; le format ``18`` du client ``android_vr`` est
  refusé par le CDN—``HTTP 403``—YouTube exige un GVS PO Token) ; servir une
  piste seule donne une vidéo **muette**, qu'aucun secours ne peut réparer
  autrement ;
- un MP4 sans piste audio n'est pas toujours ouvert par le lecteur du poste
  (Windows Media Player), alors que l'utilisateur a demandé « la vidéo ».

Le relais assemble donc les deux pistes **en flux**, à la demande, avec ffmpeg :
``-c copy`` (aucun réencodage—la qualité de la source, et un coût CPU
négligeable), conteneur MP4 **fragmenté** (le seul MP4 qui s'écrit en flux),
sortie sur ``stdout``, jamais sur un disque.

Ce que ce module ne fait **jamais** :

- écrire un fichier, un temporaire ou une ligne en base—les octets traversent ;
- réencoder une piste (donc aucun changement de codec ni de qualité) ;
- lancer ffmpeg par un shell, ni avec un chemin venu du client ;
- assembler une piste qu'il n'a pas **vérifiée publique**—les deux adresses
  repassent par la garde anti-SSRF avant tout appel.
"""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Iterator

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.documents.relais_video import FluxRelaye, nom_propose
from app.documents.telechargement import url_publique_verifiee

__all__ = [
    "arguments_ffmpeg",
    "chemin_ffmpeg",
    "muxage_disponible",
    "ouvrir_muxage",
]

_logger = logging.getLogger(__name__)

_TAILLE_BLOC = 64 * 1024
#: Options communes : pas de bannière, seules les erreurs, aucun accès au clavier.
_ARGUMENTS = ("-hide_banner", "-loglevel", "error", "-nostdin")
#: MP4 fragmenté : le seul conteneur MP4 qui se lit en flux (la table des
#: échantillons est écrite en tête, pas à la fin du fichier).
_SORTIE = ("-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1")
#: Délai maximal laissé à ffmpeg pour livrer le **premier** bloc avant de conclure
#: qu'il n'y arrivera pas : sans cette borne, l'écran resterait bloqué sans motif.
DEMARRAGE_MAX_SECONDES = 25.0


def chemin_ffmpeg(reglages: Settings | None = None) -> str | None:
    """Binaire ffmpeg disponible, ou ``None``.

    Trois sources, dans cet ordre : le chemin configuré
    (``VIDEO_FFMPEG_PATH``), le ``ffmpeg`` du PATH, puis le binaire fourni par
    ``imageio-ffmpeg`` (extra ``video`` — aucune installation système, cf.
    ``pyproject.toml``).
    """
    reglages = reglages or get_settings()
    configure = (reglages.video_ffmpeg_path or "").strip()
    if configure:
        return configure if shutil.which(configure) or _existe(configure) else None
    trouve = shutil.which("ffmpeg")
    if trouve:
        return trouve
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - binaire absent : c'est un « non »
        _logger.warning("Binaire ffmpeg d'imageio-ffmpeg indisponible : %s", exc)
        return None


def _existe(chemin: str) -> bool:
    from pathlib import Path

    return Path(chemin).exists()


def muxage_disponible(reglages: Settings | None = None) -> bool:
    """Vrai si un fichier image+son peut être produit **maintenant**."""
    return chemin_ffmpeg(reglages) is not None


def arguments_ffmpeg(video_url: str, audio_url: str) -> list[str]:
    """Ligne de commande de l'assemblage — liste d'arguments, jamais un shell.

    ``-map 0:v:0 -map 1:a:0`` retient la première piste vidéo du premier fichier
    et la première piste audio du second : exactement ce que le clic a demandé.
    ``-c copy`` ne réencode rien.
    """
    return [
        *_ARGUMENTS,
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto",
        "-i",
        video_url,
        "-i",
        audio_url,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        *_SORTIE,
    ]


class _FluxProcessus:
    """Sortie d'un processus ffmpeg : blocs, erreurs, arrêt propre.

    La lecture se fait dans un fil (le premier bloc peut tarder : le CDN doit
    répondre et ffmpeg doit écrire son en-tête), et ``stderr`` est drainé dans un
    autre : sans cela, un ffmpeg bavard remplirait le tuyau d'erreurs et se
    bloquerait lui-même au milieu d'un téléchargement.
    """

    def __init__(self, commande: list[str]) -> None:
        self._processus = subprocess.Popen(  # noqa: S603 - commande construite ici, sans shell
            commande,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        self._blocs: queue.Queue[bytes | None] = queue.Queue(maxsize=8)
        self._erreurs: deque[str] = deque(maxlen=40)
        self._fils = [
            threading.Thread(target=self._lire_sortie, daemon=True, name="carso-mux-sortie"),
            threading.Thread(target=self._lire_erreurs, daemon=True, name="carso-mux-erreurs"),
        ]

    def demarrer(self) -> None:
        for fil in self._fils:
            fil.start()

    def _lire_sortie(self) -> None:
        sortie = self._processus.stdout
        if sortie is None:  # pragma: no cover - impossible avec stdout=PIPE
            self._blocs.put(None)
            return
        try:
            while True:
                bloc = sortie.read(_TAILLE_BLOC)
                if not bloc:
                    break
                self._blocs.put(bloc)
        finally:
            sortie.close()
            self._blocs.put(None)

    def _lire_erreurs(self) -> None:
        erreurs = self._processus.stderr
        if erreurs is None:  # pragma: no cover - impossible avec stderr=PIPE
            return
        for ligne in erreurs:
            texte = ligne.decode("utf-8", errors="replace").strip()
            if texte:
                self._erreurs.append(texte)
        erreurs.close()

    def premier_bloc(self, delai: float) -> bytes | None:
        """Premier bloc, ou ``None`` si ffmpeg n'a rien livré dans le délai."""
        try:
            return self._blocs.get(timeout=delai)
        except queue.Empty:
            return None

    def suivants(self) -> Iterator[bytes]:
        """Blocs suivants, jusqu'à la fin de la sortie."""
        while True:
            bloc = self._blocs.get()
            if bloc is None:
                return
            yield bloc

    def arreter(self) -> tuple[int, str]:
        """Arrête le processus (s'il vit encore) et rend code de sortie + erreurs."""
        if self._processus.poll() is None:
            self._processus.kill()
        try:
            code = self._processus.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - kill() vient d'être appelé
            code = -9
        for fil in self._fils:
            fil.join(timeout=5)
        return code, " | ".join(self._erreurs)


def ouvrir_muxage(
    video_url: str,
    audio_url: str,
    *,
    nom: str | None = None,
    reglages: Settings | None = None,
) -> FluxRelaye:
    """Assemble les deux pistes en un MP4 lisible, en flux, sans rien écrire.

    Args:
        video_url: adresse de la piste vidéo (signée par l'analyse).
        audio_url: adresse de la piste son.
        nom: nom d'enregistrement proposé par le client (nettoyé ici).
        reglages: réglages à utiliser (tests) ; sinon ceux du processus.

    Returns:
        Le flux prêt à être retransmis (type ``video/mp4``, taille inconnue :
        ffmpeg écrit un MP4 fragmenté, dont la taille n'existe qu'à la fin).

    Raises:
        ValidationError: ffmpeg absent, adresse refusée par la garde anti-SSRF,
            ou assemblage qui ne démarre pas (motif de ffmpeg inclus).
    """
    reglages = reglages or get_settings()
    binaire = chemin_ffmpeg(reglages)
    if not binaire:
        raise ValidationError(
            "L'assemblage vidéo + son n'est pas disponible sur ce serveur : "
            "installer l'extra « video » (imageio-ffmpeg) ou indiquer "
            "VIDEO_FFMPEG_PATH."
        )

    source_video = url_publique_verifiee(video_url)
    source_son = url_publique_verifiee(audio_url)
    flux = _FluxProcessus([binaire, *arguments_ffmpeg(source_video, source_son)])
    flux.demarrer()

    premier = flux.premier_bloc(DEMARRAGE_MAX_SECONDES)
    if premier is None:
        code, erreurs = flux.arreter()
        raise ValidationError(
            "L'assemblage vidéo + son n'a pas démarré : la source n'a pas livré "
            "les pistes demandées",
            details={"ffmpeg": code, "motif": erreurs[:300] or None},
        )

    def _octets() -> Iterator[bytes]:
        try:
            yield premier
            yield from flux.suivants()
        finally:
            # Client qui ferme l'onglet, téléchargement annulé : le processus ne
            # doit pas continuer à tirer sur la source pour personne.
            flux.arreter()

    return FluxRelaye(
        octets=_octets(),
        nom_fichier=nom_propose(nom, source_video, "video/mp4"),
        type_mime="video/mp4",
        taille_octets=None,
    )
