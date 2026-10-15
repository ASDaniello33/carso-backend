"""Validation des fichiers téléversés (instruction/08 §4).

Politique—**allowlist** d'extensions, puis contrôle du **contenu réel**.

Le ``Content-Type`` déclaré par le client n'est jamais utilisé pour autoriser un
fichier—il est trivialement falsifiable. Seules la signature binaire et, pour
les formats OOXML, la structure de l'archive font foi. Le MIME renvoyé par
``inspect_file`` est celui **déterminé par le serveur** et c'est lui qui est
persisté dans ``documents.mime_type``.

Contrôles effectués :

- extension dans l'allowlist ;
- signature binaire cohérente (``%PDF``, ``PK\\x03\\x04``, OLE, PNG, JPEG, mais
  aussi GIF, WebP, MP4 et WebM—un média rapatrié du web est validé par le
  **même** mécanisme qu'un dépôt humain) ;
- archive OOXML réellement structurée (``word/document.xml``, ``xl/workbook.xml``)
—un ``.docx`` renommé ``.xlsx`` est refusé ;
- texte décodable (UTF-8 ou cp1252) et sans octet nul ;
- plafond de taille (appliqué en flux par le storage, pas ici).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationError
from app.documents.paths import extension_of

_PDF_SIGNATURE = b"%PDF"
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_SIGNATURE = b"PK\x03\x04"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
#: Deux versions de GIF partagent les mêmes quatre premiers octets.
_GIF_SIGNATURE = b"GIF8"
#: WebP : conteneur RIFF, le marqueur « WEBP » arrive après la taille RIFF.
_WEBP_SIGNATURE = b"WEBP"
#: ISO BMFF (MP4, MOV, M4V) : la boîte ``ftyp`` commence au 5ᵉ octet.
_MP4_SIGNATURE = b"ftyp"
_WEBM_SIGNATURE = b"\x1a\x45\xdf\xa3"
_OGG_SIGNATURE = b"OggS"

_TEXT_SAMPLE_BYTES = 64 * 1024

#: Octets lus pour comparer une signature : la plus lointaine (WebP) commence
#: à l'octet 8 et fait 4 octets.
_LECTURE_ENTETE = 16

#: Extensions **média** reconnues par signature, dans l'ordre de détection.
#: C'est la source unique du type réel d'un média : un fichier n'est jamais
#: classé sur son ``Content-Type`` déclaré ni sur son nom (annexe/05 §4).
DETECTION_MEDIA: tuple[str, ...] = ("png", "jpg", "gif", "webp", "mp4", "webm", "ogv")


@dataclass(frozen=True, slots=True)
class FileTypeRule:
    """Règle de validation d'un type de fichier autorisé."""

    extension: str
    mime_type: str
    signature: bytes | None = None
    #: Décalage de la signature : 0 pour un en-tête classique, 4 pour ``ftyp``
    #: (ISO BMFF), 8 pour le marqueur WebP du conteneur RIFF.
    signature_offset: int = 0
    zip_entries: tuple[str, ...] = ()
    is_text: bool = False

    @property
    def est_media(self) -> bool:
        """Règle d'un média (image ou vidéo), par opposition à un document."""
        return self.mime_type.startswith(("image/", "video/"))


#: Allowlist d'extensions. Volontairement en **code** (et non en configuration) :
#: c'est un contrôle de sécurité, il ne doit pas être élargissable par un
#: fichier d'environnement. Les types non listés (exécutables, scripts, HTML)
#: sont refusés par défaut.
FILE_TYPES: dict[str, FileTypeRule] = {
    "pdf": FileTypeRule("pdf", "application/pdf", _PDF_SIGNATURE),
    "docx": FileTypeRule(
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _ZIP_SIGNATURE,
        zip_entries=("word/document.xml",),
    ),
    "xlsx": FileTypeRule(
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _ZIP_SIGNATURE,
        zip_entries=("xl/workbook.xml",),
    ),
    "doc": FileTypeRule("doc", "application/msword", _OLE_SIGNATURE),
    "xls": FileTypeRule("xls", "application/vnd.ms-excel", _OLE_SIGNATURE),
    "csv": FileTypeRule("csv", "text/csv", is_text=True),
    "txt": FileTypeRule("txt", "text/plain", is_text=True),
    "png": FileTypeRule("png", "image/png", _PNG_SIGNATURE),
    "jpg": FileTypeRule("jpg", "image/jpeg", _JPEG_SIGNATURE),
    "jpeg": FileTypeRule("jpeg", "image/jpeg", _JPEG_SIGNATURE),
    "gif": FileTypeRule("gif", "image/gif", _GIF_SIGNATURE),
    "webp": FileTypeRule(
        "webp", "image/webp", _WEBP_SIGNATURE, signature_offset=8
    ),
    "mp4": FileTypeRule("mp4", "video/mp4", _MP4_SIGNATURE, signature_offset=4),
    "webm": FileTypeRule("webm", "video/webm", _WEBM_SIGNATURE),
    "ogv": FileTypeRule("ogv", "video/ogg", _OGG_SIGNATURE),
}

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(FILE_TYPES)


def inspect_file(path: Path, *, nom_fichier: str) -> FileTypeRule:
    """Valide le contenu d'un fichier et renvoie sa règle de type.

    Le fichier doit être un fichier temporaire **déjà écrit** mais pas encore
    publié sous son nom définitif : rien d'invalide n'apparaît dans
    l'arborescence documentaire.

    Args:
        path: chemin du fichier temporaire à inspecter.
        nom_fichier: nom définitif (détermine l'extension attendue).

    Returns:
        La ``FileTypeRule`` correspondante (MIME de confiance côté serveur).

    Raises:
        ValidationError: extension hors allowlist, signature incohérente,
            archive OOXML invalide, ou texte non décodable.
    """
    extension = extension_of(nom_fichier)
    regle = FILE_TYPES.get(extension)
    if regle is None:
        msg = f"Extension de fichier non autorisée : .{extension or '?'}"
        raise ValidationError(msg, details={"extensions_autorisees": sorted(ALLOWED_EXTENSIONS)})

    entete = _read_head(path, taille=_LECTURE_ENTETE)
    if not signature_valide(entete, regle):
        msg = (
            "Le contenu du fichier ne correspond pas à son extension "
            f"(.{extension}) : signature binaire invalide"
        )
        raise ValidationError(msg, details={"extension": extension})

    if regle.zip_entries:
        _check_zip_entries(path, regle)
    elif regle.is_text:
        _check_text(path, regle)

    return regle


def _read_head(path: Path, taille: int = _LECTURE_ENTETE) -> bytes:
    try:
        with path.open("rb") as fichier:
            return fichier.read(taille)
    except OSError as exc:  # pragma: no cover - dépend du système de fichiers
        msg = "Fichier téléversé illisible"
        raise ValidationError(msg) from exc


def signature_valide(entete: bytes, regle: FileTypeRule) -> bool:
    """La signature attendue est-elle présente à son décalage ?

    Une règle **sans** signature n'a rien à vérifier ici : c'est le cas des
    fichiers texte (``csv``, ``txt``), dont le contenu est validé plus loin par
    sa décodabilité — l'absence de signature n'est pas un échec.
    """
    if regle.signature is None:
        return True
    debut = regle.signature_offset
    return entete[debut : debut + len(regle.signature)] == regle.signature


def detecter_media(entete: bytes) -> FileTypeRule | None:
    """Règle de type d'un **média** reconnu à sa signature, ou ``None``.

    Utilisé au rapatriement d'un fichier externe : le type annoncé par le
    serveur distant (``Content-Type``) est déclaratif et falsifiable, et le nom
    d'URL peut être trompeur. Seule la signature décide — et un contenu qui
    n'est pas un média est refusé, jamais deviné.

    Returns:
        La règle du premier type de média reconnu (extension canonique : une
        image JPEG est classée ``jpg``, un ISO BMFF ``mp4``), sinon ``None``.
    """
    for extension in DETECTION_MEDIA:
        regle = FILE_TYPES[extension]
        if signature_valide(entete, regle):
            return regle
    return None


def _check_zip_entries(path: Path, regle: FileTypeRule) -> None:
    """Vérifie qu'une archive OOXML contient bien les parties attendues."""
    try:
        with zipfile.ZipFile(path) as archive:
            noms = set(archive.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        msg = f"Archive .{regle.extension} illisible"
        raise ValidationError(msg, details={"extension": regle.extension}) from exc

    manquantes = [entree for entree in regle.zip_entries if entree not in noms]
    if manquantes:
        msg = (
            f"Le contenu du fichier n'est pas un .{regle.extension} valide "
            f"(parties manquantes : {', '.join(manquantes)})"
        )
        raise ValidationError(msg, details={"extension": regle.extension})


def _check_text(path: Path, regle: FileTypeRule) -> None:
    """Vérifie qu'un fichier texte est décodable et exempt d'octets nuls."""
    try:
        with path.open("rb") as fichier:
            echantillon = fichier.read(_TEXT_SAMPLE_BYTES)
    except OSError as exc:  # pragma: no cover - dépend du système de fichiers
        msg = "Fichier téléversé illisible"
        raise ValidationError(msg) from exc

    if b"\x00" in echantillon:
        msg = f"Le fichier .{regle.extension} contient des octets nuls (binaire ?)"
        raise ValidationError(msg, details={"extension": regle.extension})

    for encodage in ("utf-8", "cp1252"):
        try:
            echantillon.decode(encodage)
            return
        except UnicodeDecodeError:
            continue

    msg = f"Le fichier .{regle.extension} n'est pas un texte décodable"
    raise ValidationError(msg, details={"extension": regle.extension})
