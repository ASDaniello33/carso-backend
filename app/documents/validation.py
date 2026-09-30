"""Validation des fichiers téléversés (instruction/08 §4).

Politique : **allowlist** d'extensions, puis contrôle du **contenu réel**.

Le ``Content-Type`` déclaré par le client n'est jamais utilisé pour autoriser un
fichier — il est trivialement falsifiable. Seules la signature binaire et, pour
les formats OOXML, la structure de l'archive font foi. Le MIME renvoyé par
``inspect_file`` est celui **déterminé par le serveur** et c'est lui qui est
persisté dans ``documents.mime_type``.

Contrôles effectués :

- extension dans l'allowlist ;
- signature binaire cohérente (``%PDF``, ``PK\\x03\\x04``, OLE, PNG, JPEG) ;
- archive OOXML réellement structurée (``word/document.xml``, ``xl/workbook.xml``)
  — un ``.docx`` renommé ``.xlsx`` est refusé ;
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

_TEXT_SAMPLE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class FileTypeRule:
    """Règle de validation d'un type de fichier autorisé."""

    extension: str
    mime_type: str
    signature: bytes | None = None
    zip_entries: tuple[str, ...] = ()
    is_text: bool = False


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

    entete = _read_head(path)
    if regle.signature is not None and not entete.startswith(regle.signature):
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


def _read_head(path: Path, taille: int = 8) -> bytes:
    try:
        with path.open("rb") as fichier:
            return fichier.read(taille)
    except OSError as exc:  # pragma: no cover - dépend du système de fichiers
        msg = "Fichier téléversé illisible"
        raise ValidationError(msg) from exc


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
