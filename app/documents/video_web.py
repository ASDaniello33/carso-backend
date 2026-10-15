"""Vidéo du web (``yt-dlp``)—proposer les **adresses réelles** des flux.

Un résultat de recherche vidéo n'est presque jamais un fichier—c'est une page
(YouTube, Vimeo, Dailymotion…). Ce module fait **une seule chose**—lire ce que
la page propose, et rendre au client les adresses des pistes—pour qu'il
télécharge depuis la source, dans son propre navigateur, sur son propre disque.

Rien n'est téléchargé côté serveur, rien n'est écrit, rien n'est conservé :
aucun fichier temporaire, aucun document, aucun muxage. ``yt-dlp`` est lancé en
lecture (``--dump-single-json``), jamais en écriture—il n'existe dans ce
module **aucune** option ``--output``, et pas la moindre invocation de ffmpeg.

Ce qu'il ne fait pas non plus—décider de l'adresse (elle vient de
l'utilisateur), deviner une résolution absente, recomposer un fichier qui n'existe
pas, ni contourner une protection. Aucun cookie, aucun jeton anti-robot, aucun
proxy—si le site refuse l'adresse du serveur, le refus est remonté **tel quel**
avec son motif—la leçon des connecteurs MCP.

**Image et son peuvent être deux fichiers distincts** (mesuré le 08/10/2026—la
plupart des hauteurs d'une vidéo YouTube n'existent qu'en image seule, avec une
seule piste son à part). Une résolution se décrit donc par ce qu'elle propose
**réellement**—un fichier complet (``combine``), ou une image (``video``) qu'une
piste son (``audio``) complète—jamais une troisième adresse inventée. Le modèle
le dit (``muxage_requis``) et l'écran le répète avant le geste—il n'y a **pas**
de « 720p MP4 » fabriquée à partir de deux adresses—mais le **relais**, lui,
assemble les deux pistes à la volée (``app.documents.muxage_video``) pour livrer
un seul fichier lisible, avec le son.

**Ce que le navigateur fera de ces adresses** (mesuré le 08/10/2026—le CDN ne
renvoie ni CORS ni ``Content-Disposition``, même avec ``Origin``)—il ne peut pas
les récupérer en JavaScript, donc aucune barre de progression n'est fabriquée ici.
Un lien direct mène au flux—le navigateur l'enregistre si la source l'annonce
comme pièce à télécharger, sinon il le lit dans un onglet—et c'est alors le
téléchargement du lecteur qui range le fichier sur le disque du client. Dans les
deux cas le fichier vient de la source et va sur le disque du client—CARSO ne
voit passer aucun octet.

**Le format « muxé » des plateformes ne sert plus** (mesuré le 09/10/2026 sur
deux vidéos et six clients yt-dlp—le MP4 progressif ``18`` du client
``android_vr`` est listé mais son CDN le refuse—``HTTP 403``, un GVS PO Token
anti-bot étant exigé ; les clients ``ios``, ``web_safari``, ``mweb`` et ``tv``
ne rendent aucun format du tout). Un format muxé n'est donc **jamais** préféré à
une piste image quand les deux existent à la même hauteur—ce serait proposer
un lien mort. Le téléchargement complet passe par l'assemblage du relais.

Conséquence assumée—sur ces plateformes, une vidéo complète demande **deux
fichiers** (l'image, puis le son)—la spécification du 08/10 exclut le
téléchargement côté serveur, donc CARSO n'assemble rien, et le dit à l'écran.
Enfin, une adresse signée peut être liée à l'IP qui l'a obtenue—elle se vérifie
depuis le poste qui télécharge, et l'écran offre de relancer la lecture.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationError

__all__ = [
    "PisteFlux",
    "ResolutionFlux",
    "VideoFlux",
    "commande_analyse",
    "extraire_flux_video",
    "paquet_disponible",
]

#: Délai de connexion/lecture d'yt-dlp, par requête. Un site qui ne répond pas
#: ne doit pas consommer tout le délai global.
_SOCKET_TIMEOUT = 20

#: Conteneurs vidéo qu'un navigateur ouvre et enregistre tels quels. Écartés :
#: ``flv``, ``mkv``, ``3gp``… — un fichier que le poste du client ne saura pas
#: lire ne lui rendrait pas service, et CARSO n'en reçoit aucun octet.
_EXTENSIONS_VIDEO = ("mp4", "webm", "ogv")
#: Conteneurs de démonstration (vignettes animées) : jamais une résolution.
_PROTOCOLES_EXCLUS = ("mhtml",)
#: Protocoles dont l'adresse se récupère **telle quelle** (un fichier). Les
#: manifestes (``m3u8_native``, ``dash``) portent une adresse qui se lit avec un
#: lecteur : elles ne sont pas présentées comme un lien direct.
_PROTOCOLES_DIRECTS = ("https", "http")
#: Motifs de refus reconnus, du plus explicite au plus général.
_REFUS_CONNUS = (
    (
        "Sign in to confirm you're not a bot",
        "Le site demande une vérification anti-robot pour l'adresse du serveur "
        "(réputation d'IP). CARSO ne présente ni cookie ni jeton de connexion : "
        "réessayez plus tard, ou ouvrez la vidéo depuis votre poste.",
    ),
    (
        "Unsupported URL",
        "Cette adresse n'est pas prise en charge : aucun extracteur ne sait en lire "
        "le contenu. Vérifiez qu'il s'agit bien d'une page vidéo et non d'un lien "
        "vers autre chose.",
    ),
    (
        "No suitable extractor",
        "Cette adresse n'est pas prise en charge : aucun extracteur ne sait en lire "
        "le contenu. Vérifiez qu'il s'agit bien d'une page vidéo et non d'un lien "
        "vers autre chose.",
    ),
    (
        "Video unavailable",
        "Le site déclare cette vidéo indisponible (retirée, privée ou réservée à "
        "une région).",
    ),
    (
        "Private video",
        "Cette vidéo est privée : elle exige une connexion que CARSO ne présente pas.",
    ),
)


@dataclass(frozen=True, slots=True)
class PisteFlux:
    """Une piste telle que le site la sert — jamais recomposée, jamais inventée.

    Les deux drapeaux ``contient_video``/``contient_audio`` viennent des codecs
    annoncés par le site : un format dont ``vcodec`` vaut ``none`` est une piste
    **audio**, et réciproquement. Un manifeste HLS/DASH porte une adresse, mais
    elle se lit avec un lecteur : ``telechargeable_directement`` est faux.
    """

    format_id: str
    #: Adresse réelle annoncée par le site (``None`` si l'extracteur n'en donne pas).
    url: str | None
    extension: str
    protocole: str
    vcodec: str
    acodec: str
    hauteur: int | None
    fps: int | None
    #: Débit annoncé en kbit/s (``tbr``), ou ``None``.
    debit_kbits: float | None
    taille_octets: int | None
    contient_video: bool
    contient_audio: bool
    telechargeable_directement: bool


@dataclass(frozen=True, slots=True)
class ResolutionFlux:
    """Une résolution proposée au client, avec les adresses réelles de ses pistes.

    ``combine`` non nul ⇒ la source sert **un seul** flux image+son (rare) ;
    ``video`` et ``audio`` restent alors nuls pour ne pas décrire deux fois les
    mêmes octets. Sinon, ``video`` et ``audio`` sont les pistes distinctes — et
    une « 720p MP4 » n'est **jamais** fabriquée à partir de deux adresses : elle
    n'existe pas chez la source. Le relais, lui, assemble les deux pistes au
    téléchargement (``muxage_requis`` dit quand c'est le cas).
    """

    hauteur: int
    #: Taille estimée **totale** (image + son), ou ``None`` quand le site ne
    #: l'annonce pas (manifeste). Une estimation inconnue reste inconnue.
    taille_estimee_octets: int | None
    video: PisteFlux | None
    audio: PisteFlux | None
    combine: PisteFlux | None

    @property
    def muxage_requis(self) -> bool:
        """Vrai si image et son vivent dans **deux** fichiers distincts.

        Le relais assemble alors les deux pistes en un seul fichier lisible.
        """
        return self.combine is None and self.video is not None and self.audio is not None

    @property
    def telechargement_possible(self) -> bool:
        """Vrai si **l'image de cette résolution** est un fichier téléchargeable.

        C'est la condition pour que l'écran propose un bouton : un manifeste HLS
        ou DASH porte une adresse, mais pas un fichier — rien à télécharger, et
        une résolution n'est jamais annoncée téléchargeable sur une promesse. La
        piste son est un **second** fichier : elle ne rend pas téléchargeable une
        image qui, elle, ne l'est pas.
        """
        piste = self.combine if self.combine is not None else self.video
        return piste is not None and piste.telechargeable_directement


@dataclass(frozen=True, slots=True)
class VideoFlux:
    """Ce qu'une page vidéo propose : métadonnées d'affichage et résolutions réelles.

    ``adresse_temporaire`` est vrai pour les plateformes (YouTube, Vimeo…) : leurs
    adresses sont **signées et expirantes**. Rien ici ne les présente comme un
    lien CARSO permanent — CARSO n'héberge aucun de ces octets.
    """

    url: str
    titre: str
    auteur: str | None
    duree_secondes: int | None
    extracteur: str
    miniature: str | None
    resolutions: tuple[ResolutionFlux, ...]
    adresse_temporaire: bool = True

    @property
    def telechargeable(self) -> bool:
        """Vrai si **au moins une** résolution propose une adresse de fichier.

        Faux ⇒ l'écran dit que cette vidéo n'est pas téléchargeable (la source
        n'expose qu'un lecteur ou un manifeste), plutôt que d'afficher des liens
        qui ne mèneraient à rien.
        """
        return any(resolution.telechargement_possible for resolution in self.resolutions)

    def piste_pour(self, hauteur: int, genre: str) -> PisteFlux | None:
        """La piste d'une résolution, retrouvée par hauteur et par genre.

        Sert au **relais de téléchargement** : quand une adresse est refusée par
        la source (403/410 — adresse expirée ou liée à une autre IP), la page est
        réanalysée et la **même** piste doit être retrouvée pour reprendre le
        fichier, sans changer ni la résolution ni la nature du fichier.

        Args:
            hauteur: hauteur visée par le lien signé (``720``, ``240``…).
            genre: ``combine`` (un seul fichier vidéo+son), ``video`` ou ``audio``.

        Returns:
            La piste si la source la publie encore avec une adresse de fichier,
            sinon ``None`` — le relais s'arrête alors au lieu de servir autre chose.
        """
        for resolution in self.resolutions:
            if resolution.hauteur != hauteur:
                continue
            piste = getattr(resolution, genre, None)
            if piste is not None and piste.url and piste.telechargeable_directement:
                return piste
            return None
        return None


def paquet_disponible() -> bool:
    """Vrai si ``yt-dlp`` est installé (extra ``video`` du projet)."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def commande_analyse(url: str) -> list[str]:
    """Ligne de commande d'yt-dlp : **lecture seule** de la page, rien de plus.

    ``--dump-single-json`` n'écrit aucun fichier, par construction : il n'y a ni
    ``--output`` ni ``--merge-output-format``, donc la lecture ne peut pas laisser
    de vidéo sur le disque. Exposée pour être vérifiée contre le vrai analyseur
    d'options d'yt-dlp : une faute d'orthographe y fait refuser la commande
    entière, avec un message générique (l'API Python dit ``noplaylist``, la ligne
    de commande ``--no-playlist`` — un test le verrouille).

    Aucun client de plateforme n'est imposé : le client par défaut a rendu l'échelle
    complète des hauteurs sur les vidéos mesurées, et forcer une liste de clients
    n'a rien apporté d'exploitable (le format muxé qu'elle ajoutait est refusé par
    le CDN — voir le module).
    """
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        "--no-plugin-dirs",
        "--no-progress",
        "--no-playlist",
        "--socket-timeout",
        str(_SOCKET_TIMEOUT),
        "--dump-single-json",
        url,
    ]


def _drapeaux_processus() -> dict[str, Any]:
    """Drapeaux de création : aucune console visible sous Windows.

    Un serveur n'a pas de console ; sans ce drapeau, chaque appel ferait clignoter
    une fenêtre sur le poste qui héberge le backend.
    """
    if os.name != "nt":  # pragma: no cover - dépend du système
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _tuer(processus: subprocess.Popen[str]) -> None:
    """Tue le sous-processus **et ses enfants**, puis le processus lui-même.

    Un délai dépassé ne doit pas laisser d'extraction orpheline qui continuerait
    de travailler après le refus rendu à l'utilisateur.
    """
    if processus.poll() is not None:
        return
    try:
        if os.name == "nt":  # pragma: no cover - dépend du système
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(processus.pid)],
                capture_output=True,
                check=False,
                **_drapeaux_processus(),
            )
        else:  # pragma: no cover - dépend du système
            os.killpg(os.getpgid(processus.pid), 9)
    except Exception:  # noqa: BLE001 - l'échec du nettoyage ne masque pas le refus
        pass
    finally:
        processus.kill()


def _executer(commande: list[str], *, timeout: float) -> tuple[str, str]:
    """Exécute yt-dlp sous **délai dur**, sans shell, et rend ``(stdout, stderr)``.

    Aucune concaténation : la commande est une liste d'arguments, l'adresse y
    occupe une position et n'est jamais interprétée par un shell.

    Raises:
        ValidationError: délai dépassé (le processus et ses enfants sont tués) ou
            exécutable introuvable.
    """
    depart = time.monotonic()
    try:
        processus = subprocess.Popen(
            commande,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_drapeaux_processus(),
        )
    except OSError as exc:  # pragma: no cover - dépend du système
        raise ValidationError(
            "Impossible de lancer le lecteur vidéo sur ce serveur.",
            details={"erreur": str(exc)},
        ) from exc

    try:
        sortie, erreurs = processus.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _tuer(processus)
        raise ValidationError(
            "Délai dépassé : le site n'a pas répondu à temps (ou la lecture est trop "
            f"longue pour la limite de {int(timeout)} s). Réessayez, ou augmentez "
            "VIDEO_ANALYSE_TIMEOUT_SECONDS.",
            details={
                "delai_secondes": int(timeout),
                "duree_secondes": round(time.monotonic() - depart),
            },
        ) from exc
    return sortie or "", erreurs or ""


def _motif_refus(erreurs: str, code: int) -> str:
    """Motif de refus lisible, pris dans la sortie réelle d'yt-dlp.

    Le message du site est préféré à un « échec » générique : c'est lui qui dit
    quoi faire (« vérification anti-robot », « vidéo indisponible », extracteur
    absent). Les motifs connus reçoivent en plus un conseil.
    """
    texte = " ".join(erreurs.split())
    for motif, conseil in _REFUS_CONNUS:
        if motif.lower() in texte.lower():
            return conseil
    lignes = [ligne.strip() for ligne in erreurs.splitlines() if "ERROR:" in ligne]
    if lignes:
        return lignes[-1].replace("ERROR:", "").strip()[:300]
    return f"L'extracteur a échoué (code {code}) sans message exploitable."


def _codec(valeur: Any) -> str:
    """Codec d'une piste : ``""`` quand le site annonce ``none`` (piste absente)."""
    texte = str(valeur or "").strip()
    return "" if texte.lower() == "none" else texte


def _entier(valeur: Any) -> int | None:
    """Entier exploitable, ou ``None`` (jamais un zéro de remplacement)."""
    return int(valeur) if isinstance(valeur, (int, float)) and valeur == int(valeur) else None


def _reel(valeur: Any) -> float | None:
    """Flottant exploitable, ou ``None``."""
    return float(valeur) if isinstance(valeur, (int, float)) else None


def _taille(format: dict[str, Any]) -> int | None:
    """Taille annoncée d'un format (exacte ou approximative), ou ``None``."""
    for cle in ("filesize", "filesize_approx"):
        valeur = format.get(cle)
        if isinstance(valeur, int) and valeur > 0:
            return valeur
    return None


def _extrait(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Formats annoncés par l'extracteur, réduits à ceux exploitables."""
    return [format for format in (info.get("formats") or []) if isinstance(format, dict)]


def _piste(format: dict[str, Any], *, hauteur: int | None = None) -> PisteFlux:
    """Décrit une piste **telle qu'elle est** : codecs, protocole, adresse.

    ``contient_video``/``contient_audio`` viennent des codecs annoncés — un
    ``vcodec`` à ``none`` est une piste audio, un ``acodec`` à ``none`` une piste
    vidéo, les deux renseignés un flux combiné. L'adresse n'est réputée directe
    que si le protocole est ``http(s)`` : un manifeste HLS/DASH n'est pas un
    fichier.
    """
    url_brute = format.get("url")
    url = str(url_brute) if isinstance(url_brute, str) and url_brute else None
    protocole = str(format.get("protocol") or "")
    vcodec = _codec(format.get("vcodec"))
    acodec = _codec(format.get("acodec"))
    return PisteFlux(
        format_id=str(format.get("format_id") or "?"),
        url=url,
        extension=str(format.get("ext") or ""),
        protocole=protocole,
        vcodec=vcodec,
        acodec=acodec,
        hauteur=hauteur if hauteur is not None else _entier(format.get("height")),
        fps=_entier(format.get("fps")),
        debit_kbits=_reel(format.get("tbr")),
        taille_octets=_taille(format),
        # ``none`` (piste absente) et ``None`` (non annoncé) ne veulent pas dire
        # la même chose : le premier est un fait, le second une ignorance.
        contient_video=bool(vcodec),
        contient_audio=bool(acodec),
        telechargeable_directement=url is not None and protocole in _PROTOCOLES_DIRECTS,
    )


def _variantes_video(formats: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Formats **vidéo** réellement lisibles, groupés par hauteur.

    Écartés : les démonstrations (``mhtml``, vignettes animées qui portent une
    « hauteur »), les formats sans piste vidéo, et tout format dont la hauteur
    n'est pas un entier exploitable.
    """
    groupes: dict[int, list[dict[str, Any]]] = {}
    for format in formats:
        if str(format.get("protocol")) in _PROTOCOLES_EXCLUS:
            continue
        if format.get("vcodec") in (None, "none"):
            continue
        if str(format.get("ext")) not in _EXTENSIONS_VIDEO:
            continue
        hauteur = format.get("height")
        if not isinstance(hauteur, int) or hauteur <= 0:
            continue
        groupes.setdefault(hauteur, []).append(format)
    return groupes


def _meilleure_variante(variantes: list[dict[str, Any]]) -> dict[str, Any]:
    """Variante retenue pour une hauteur : celle qui sert le mieux le client.

    Trois critères, dans cet ordre — chacun vient d'une mesure, pas d'un goût :

    1. **une adresse de fichier** (``http(s)``) passe avant un manifeste : un
       manifeste n'est pas un fichier, donc pas un téléchargement possible. Le
       rôle de cette préférence est mesuré — sans elle, un ``m3u8_native``
       l'emportait sur l'image mp4 de la même hauteur et la résolution devenait
       intéléchargeable alors qu'un fichier existait ;
    2. une piste **image seule** passe avant un flux muxé : mesuré le 09/10/2026,
       le format muxé que ces plateformes listent encore (MP4 progressif ``18``
       du client ``android_vr``) est refusé par leur CDN (PO Token exigé) — le
       retenir ici proposerait un lien mort alors qu'une image exploitable
       existe à la même hauteur. Le son, lui, est assemblé par le relais au
       téléchargement : l'écran n'a pas besoin du format muxé ;
    3. ``avc1`` (H.264) se lit partout, puis un autre mp4 (AV1), puis un webm.
    """

    def rang(format: dict[str, Any]) -> tuple[int, int, int, int, float]:
        ext = str(format.get("ext"))
        vcodec = str(format.get("vcodec") or "")
        direct = 0 if format.get("protocol") in _PROTOCOLES_DIRECTS else 1
        image_seule = 0 if format.get("acodec") in (None, "none") else 1
        priorite = 0 if vcodec.startswith("avc1") else (1 if ext == "mp4" else 2)
        return (
            direct,
            image_seule,
            priorite,
            0 if format.get("url") else 1,
            -(format.get("tbr") or 0.0),
        )

    return sorted(variantes, key=rang)[0]


def _meilleur_audio(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Meilleure piste audio disponible (préférence ``m4a``, conteneur du mp4)."""
    audios = [
        format
        for format in formats
        if format.get("acodec") not in (None, "none") and format.get("vcodec") in (None, "none")
    ]
    if not audios:
        return None
    m4a = [format for format in audios if format.get("ext") == "m4a"]
    return sorted(m4a or audios, key=lambda f: f.get("abr") or 0, reverse=True)[0]


def _titre(info: dict[str, Any], url: str) -> str:
    """Titre annoncé par le site, ou l'adresse — jamais un titre inventé."""
    return str(info.get("title") or "").strip() or url


def _extracteur(info: dict[str, Any]) -> str:
    """Nom de l'extracteur qui a lu la page (``Youtube``, ``Generic``…)."""
    return str(info.get("extractor_key") or info.get("extractor") or "")


def _obtenir_info(url: str, *, timeout: float) -> dict[str, Any]:
    """Métadonnées brutes du site : ``yt-dlp --dump-single-json``, **sans download**.

    C'est la seule lecture du module : une commande, qui ne demande aucune
    écriture de fichier (pas de ``--output``).

    Raises:
        ValidationError: ``yt-dlp`` absent, adresse sans extracteur, site qui
            refuse le serveur, réponse illisible, ou délai dépassé.
    """
    if not paquet_disponible():
        raise ValidationError(
            "La lecture des vidéos du web n'est pas installée sur ce serveur : "
            "pip install 'carso-backend[video]'.",
            details={"extra": "video"},
        )

    sortie, erreurs = _executer(commande_analyse(url), timeout=timeout)
    try:
        info = json.loads(sortie)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            _motif_refus(erreurs, -1),
            details={"url": url, "sortie": sortie[:200]},
        ) from exc
    if not isinstance(info, dict):
        # yt-dlp répond ``null`` — et rien d'autre — quand aucun extracteur ne
        # prend l'adresse en charge : le motif réel est alors dans la sortie
        # d'erreur (« Unsupported URL »). Le rendre plutôt qu'un « réponse
        # inattendue » qui ne dit pas quoi corriger.
        raise ValidationError(
            _motif_refus(erreurs, -1),
            details={"url": url, "sortie": sortie[:200]},
        )
    return info


def extraire_flux_video(url: str, *, timeout: float) -> VideoFlux:
    """Ce que la page propose : toutes les résolutions, avec leurs adresses réelles.

    Aucun octet de vidéo ne passe par ce serveur : la fonction lit les métadonnées
    et rend les URL des pistes, que le relais retransmettra au navigateur du
    client — en les **assemblant** quand image et son sont deux fichiers
    (``app.documents.muxage_video``). Aucun fichier n'est créé, aucune option
    d'écriture n'est transmise à yt-dlp, et ffmpeg n'est ni requis ni invoqué
    **ici** : l'assemblage appartient au relais, au moment du téléchargement.

    Les résolutions sont rendues **toutes**, de la plus haute à la plus basse :
    c'est l'écran qui les propose. Chacune dit ce qu'elle vaut : combien d'octets
    (quand la source l'annonce), si elle est téléchargeable directement, et si
    image et son sont deux fichiers distincts.

    Args:
        url: adresse de la page vidéo (déjà validée par l'appelant).
        timeout: délai dur de la lecture.

    Returns:
        Les métadonnées d'affichage, toutes les résolutions réelles, et l'aveu que
        ces adresses sont temporaires.

    Raises:
        ValidationError: support absent, adresse refusée ou sans extracteur, site
            en échec, aucune résolution vidéo exploitable, ou délai dépassé.
    """
    info = _obtenir_info(url, timeout=timeout)
    formats = _extrait(info)

    variantes = _variantes_video(formats)
    if not variantes:
        raise ValidationError(
            "Cette adresse ne propose aucune résolution vidéo exploitable "
            "(elle n'expose peut-être pas de fichier, seulement un lecteur).",
            details={"url": url},
        )

    audio = _meilleur_audio(formats)
    taille_audio = _taille(audio) if audio else None

    resolutions: list[ResolutionFlux] = []
    for hauteur in sorted(variantes, reverse=True):
        variante = _meilleure_variante(variantes[hauteur])
        piste_video = _piste(variante, hauteur=hauteur)
        # Flux combiné : la variante porte l'image **et** le son. Sinon, l'audio
        # vient d'une autre piste — et s'il n'en existe aucune, la vidéo est
        # muette, ce que le résultat dit (piste audio absente plutôt qu'inventée).
        combine = piste_video if piste_video.contient_audio else None
        piste_audio = None if combine else (_piste(audio) if audio else None)

        taille_video = piste_video.taille_octets
        estimee = None
        if taille_video is not None:
            # Un flux combiné contient **déjà** son audio : y ajouter la piste
            # audio séparée compterait deux fois le même son. Et s'il n'existe
            # aucune piste audio, la vidéo est muette — sa taille est alors le
            # total, pas un minorant.
            estimee = taille_video if combine else taille_video + (taille_audio or 0)

        resolutions.append(
            ResolutionFlux(
                hauteur=hauteur,
                taille_estimee_octets=estimee,
                video=None if combine else piste_video,
                audio=piste_audio,
                combine=combine,
            )
        )

    duree = info.get("duration")
    return VideoFlux(
        url=url,
        titre=_titre(info, url),
        auteur=str(info.get("uploader") or info.get("channel") or "").strip() or None,
        duree_secondes=int(duree) if isinstance(duree, (int, float)) else None,
        extracteur=_extracteur(info),
        miniature=str(info.get("thumbnail") or "").strip() or None,
        resolutions=tuple(resolutions),
    )
