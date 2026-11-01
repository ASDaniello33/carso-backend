"""Stockage des fichiers d'ouvrages (répertoire géré, sous la racine documentaire).

Réutilise les garanties du stockage documentaire — nom validé, écriture
atomique, plafond de taille, checksum — sans élargir l'allowlist **globale**
des documents : un ``.md`` est un format de connaissance, pas un format de
document métier. Les extensions déjà connues de ``app.documents.validation``
y passent par le même contrôle de contenu (signature binaire), les autres par
un simple contrôle de décodabilité texte.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.connaissances.racine import resoudre_fichier
from app.core.errors import CarsoError, ConflictError, ValidationError
from app.documents.paths import extension_of, sanitize_filename
from app.documents.validation import ALLOWED_EXTENSIONS, inspect_file

_TAILLE_LOT = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FichierConnaissance:
    """Résultat d'un dépôt : de quoi remplir la fiche, jamais le chemin physique."""

    chemin_logique: str
    nom_fichier: str
    checksum_sha256: str
    taille_octets: int
    mime_type: str


class StockageConnaissances:
    """Écrit un ouvrage sous ``{racine}/{document_id}/{nom}``, sans écrasement."""

    def __init__(self, racine: Path, *, max_bytes: int) -> None:
        self._racine = Path(racine)
        self._max_bytes = int(max_bytes)

    @property
    def racine(self) -> Path:
        return self._racine

    def depot(
        self, *, document_id: str, nom_fichier: str, flux: BinaryIO
    ) -> FichierConnaissance:
        """Valide, écrit et mesure un fichier d'ouvrage.

        Raises:
            ValidationError: extension hors périmètre, contenu incohérent,
                fichier vide ou plafond dépassé.
            ConflictError: un fichier occupe déjà ce chemin.
        """
        nom = sanitize_filename(nom_fichier)
        if extension_of(nom) not in {"pdf", "docx", "xlsx", "txt", "md", "csv"}:
            raise ValidationError(
                f"Format non indexable : .{extension_of(nom)}",
                details={"formats": ["pdf", "docx", "xlsx", "txt", "md", "csv"]},
            )
        chemin_logique = f"{document_id}/{nom}"
        definitif = resoudre_fichier(self._racine, chemin_logique)
        if definitif.exists():
            raise ConflictError(
                "Un fichier occupe déjà cet emplacement", details={"nom": nom}
            )

        try:
            definitif.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CarsoError(
                "Répertoire de la base de connaissance inutilisable "
                "(vérifier KNOWLEDGE_STORAGE_PATH et les droits du disque)"
            ) from exc

        temporaire = definitif.with_name(f".part-{uuid4().hex}{definitif.suffix}")
        digest = hashlib.sha256()
        taille = 0
        try:
            with temporaire.open("wb") as sortie:
                while morceau := flux.read(_TAILLE_LOT):
                    taille += len(morceau)
                    if taille > self._max_bytes:
                        raise ValidationError(
                            f"Ouvrage trop volumineux (plafond {self._max_bytes} octets)",
                            details={"max_octets": self._max_bytes},
                        )
                    digest.update(morceau)
                    sortie.write(morceau)
            if taille == 0:
                raise ValidationError("Fichier vide : aucun contenu à indexer")
            mime = self._valider(nom, temporaire)
            os.replace(temporaire, definitif)
        except BaseException:
            temporaire.unlink(missing_ok=True)
            raise
        return FichierConnaissance(
            chemin_logique=chemin_logique,
            nom_fichier=nom,
            checksum_sha256=digest.hexdigest(),
            taille_octets=taille,
            mime_type=mime,
        )

    def _valider(self, nom: str, chemin: Path) -> str:
        """MIME + contrôle du contenu (signature binaire réutilisée si connue)."""
        extension = extension_of(nom)
        if extension in ALLOWED_EXTENSIONS:
            regle = inspect_file(chemin, nom_fichier=nom)
            return regle.mime_type
        # ``md`` : texte brut. Doit se décoder, jamais d'octets binaires.
        octets = chemin.read_bytes()
        for encodage in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                octets.decode(encodage)
                return "text/markdown"
            except UnicodeDecodeError:
                continue
        raise ValidationError(
            "Le fichier de connaissance n'est pas un texte décodable",
            details={"extension": extension},
        )


__all__ = ["FichierConnaissance", "StockageConnaissances"]
