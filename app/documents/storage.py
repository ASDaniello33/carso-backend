"""Stockage documentaire local (instruction/06 §3, instruction/08 §4).

L'adaptateur ne connaît que des **chemins logiques** (``missions/<uuid>/x.pdf``) :
toute résolution passe par ``resolve_within_root``, donc aucune écriture ni
lecture ne peut sortir de ``storage_root``.

Écriture **atomique** : le flux est d'abord écrit dans un fichier temporaire
``.part-*`` situé dans le répertoire cible (même volume, donc ``os.replace``
est atomique), puis validé (``inspect_file``), puis publié sous son nom
définitif. En cas d'échec — y compris validation refusée ou plafond de taille
dépassé — le temporaire est supprimé : jamais de fichier partiel visible.

**Aucun écrasement** : ``save`` refuse un chemin déjà occupé. Le versioning
applicatif garantit qu'une version n'en remplace jamais une autre (un document
officiel n'est jamais écrasé, instruction/06 §9).

``remove`` n'existe que pour un usage **interne** (compensation après échec de
la transaction, nettoyage de temporaires) : aucune suppression physique de
document officiel n'est exposée par le système, les règles de rétention et de
suppression définitives de CARSO n'étant pas confirmées (AGENTS.md §1.5).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import uuid4

from app.core.errors import CarsoError, ConflictError, NotFoundError, ValidationError
from app.documents.paths import resolve_within_root
from app.documents.validation import FileTypeRule, inspect_file

_CHUNK_SIZE = 1024 * 1024


class UploadTooLargeError(ValidationError):
    """Fichier dépassant le plafond de taille configuré (``max_upload_bytes``)."""

    def __init__(self, message: str, max_bytes: int) -> None:
        super().__init__(message, details={"max_octets": max_bytes})


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Résultat d'un stockage : métadonnées à persister dans ``documents``."""

    logical_path: str
    absolute_path: Path
    checksum_sha256: str
    taille_octets: int
    mime_type: str
    extension: str


class DocumentStorage(Protocol):
    """Port de stockage : implémentable par un disque local, un partage réseau, un S3…"""

    disk_name: str

    def save(self, logical_path: str, stream: BinaryIO) -> StoredFile: ...

    def exists(self, logical_path: str) -> bool: ...

    def read(self, logical_path: str) -> bytes: ...

    def resolve_existing(self, logical_path: str) -> Path: ...

    def remove(self, logical_path: str) -> None: ...


class LocalDocumentStorage:
    """Stockage sur le système de fichiers, sous une racine unique et confinée."""

    disk_name = "local"

    def __init__(self, root: str | Path, *, max_bytes: int) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        """Racine configurée (``settings.storage_root``)."""
        return self._root

    @property
    def max_bytes(self) -> int:
        """Plafond de taille d'un fichier téléversé."""
        return self._max_bytes

    def save(self, logical_path: str, stream: BinaryIO) -> StoredFile:
        """Écrit et valide un flux sous ``logical_path``.

        Raises:
            ConflictError: un fichier existe déjà à ce chemin (aucun écrasement).
            UploadTooLargeError: plafond de taille dépassé.
            ValidationError: extension interdite ou contenu incohérent avec
                l'extension (via ``inspect_file``).
            CarsoError: racine de stockage inutilisable (droits, disque).
        """
        definitif = resolve_within_root(self._root, logical_path)
        if definitif.exists():
            msg = f"Un fichier existe déjà à l'emplacement {logical_path}"
            raise ConflictError(msg, details={"storage_path": logical_path})

        self._prepare_directory(definitif.parent)
        temporaire = definitif.with_name(f".part-{uuid4().hex}{definitif.suffix}")

        digest = hashlib.sha256()
        try:
            taille = self._write_temp(temporaire, stream, digest)
            regle = inspect_file(temporaire, nom_fichier=definitif.name)
            self._publish(temporaire, definitif)
        except BaseException:
            temporaire.unlink(missing_ok=True)
            raise

        return StoredFile(
            logical_path=logical_path,
            absolute_path=definitif,
            checksum_sha256=digest.hexdigest(),
            taille_octets=taille,
            mime_type=regle.mime_type,
            extension=regle.extension,
        )

    def exists(self, logical_path: str) -> bool:
        """Vrai si un fichier occupe ce chemin logique."""
        return resolve_within_root(self._root, logical_path).is_file()

    def read(self, logical_path: str) -> bytes:
        """Lit un fichier documentaire (usage contrôlé : extraction, tests).

        Raises:
            NotFoundError: fichier absent.
        """
        return self.resolve_existing(logical_path).read_bytes()

    def resolve_existing(self, logical_path: str) -> Path:
        """Chemin absolu d'un fichier existant, ou NotFoundError.

        Destiné à la couche de service (téléchargement, extraction). Le chemin
        physique n'est jamais renvoyé dans une réponse d'API.
        """
        chemin = resolve_within_root(self._root, logical_path)
        if not chemin.is_file():
            msg = f"Fichier documentaire absent : {logical_path}"
            raise NotFoundError(msg, details={"storage_path": logical_path})
        return chemin

    def remove(self, logical_path: str) -> None:
        """Supprime un fichier — usage INTERNE uniquement (compensation).

        Aucun cas d'usage applicatif n'expose cette opération : le système
        archive les documents, il ne les détruit pas (règles de rétention CARSO
        non confirmées).
        """
        resolve_within_root(self._root, logical_path).unlink(missing_ok=True)

    # --- internes ---------------------------------------------------------

    def _prepare_directory(self, repertoire: Path) -> None:
        try:
            repertoire.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            msg = (
                "Racine de stockage documentaire inutilisable "
                f"({self._root}) : vérifier STORAGE_ROOT et les droits du disque"
            )
            raise CarsoError(msg) from exc

    def _write_temp(self, temporaire: Path, stream: BinaryIO, digest: Any) -> int:
        """Écrit le flux en flux (checksum + plafond), sans charger en mémoire."""
        taille = 0
        try:
            with temporaire.open("wb") as sortie:
                while morceau := stream.read(_CHUNK_SIZE):
                    taille += len(morceau)
                    if taille > self._max_bytes:
                        msg = f"Fichier trop volumineux (plafond {self._max_bytes} octets)"
                        raise UploadTooLargeError(msg, self._max_bytes)
                    digest.update(morceau)
                    sortie.write(morceau)
        except OSError as exc:
            msg = "Écriture du fichier documentaire impossible"
            raise CarsoError(msg) from exc
        return taille

    def _publish(self, temporaire: Path, definitif: Path) -> None:
        try:
            os.replace(temporaire, definitif)
        except OSError as exc:
            msg = "Publication du fichier documentaire impossible"
            raise CarsoError(msg) from exc


__all__ = [
    "DocumentStorage",
    "FileTypeRule",
    "LocalDocumentStorage",
    "StoredFile",
    "UploadTooLargeError",
]
