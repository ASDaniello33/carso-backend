"""Fourniture des images d'un ``DocumentSpec`` (ADR 0005).

Un ``DocumentSpec`` ne porte **jamais** de chemin absolu : il désigne une image
par un document enregistré (``document_id``) ou par un chemin **logique** confiné
au stockage. Ce module fait le pont, sans que le renderer connaisse la base ni
le service documentaire (séparation des couches, AGENTS.md §2.4).

Deux usages :

- ``ImagesDisque(racine=..., fichiers=...)`` en production : ``racine`` résout
  les chemins logiques via ``resolve_within_root``, ``fichiers`` associe un
  ``document_id`` au fichier physique déjà résolu par ``DocumentService`` ;
- ``ImagesMemoire({...})`` en test : octets fournis directement.

Toute image introuvable lève une ``ValidationError`` explicite : jamais une
image manquante silencieuse dans un livrable.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.core.errors import ValidationError
from app.documents.paths import resolve_within_root
from app.documents.spec import Image

__all__ = ["FournisseurImages", "ImagesDisque", "ImagesMemoire", "resoudre_image"]


class FournisseurImages(Protocol):
    """Port de résolution des images d'un document."""

    def lire(self, image: Image) -> tuple[bytes, str]:
        """Octets et nom de fichier de l'image référencée."""
        ...


class ImagesMemoire:
    """Fournisseur en mémoire (tests, aperçus, documents sans stockage)."""

    def __init__(self, contenus: Mapping[str, bytes] | None = None) -> None:
        self._contenus = dict(contenus or {})

    def ajouter(self, cle: object, contenu: bytes) -> None:
        """Enregistre un contenu sous une clé (``document_id`` ou chemin logique)."""
        self._contenus[str(cle)] = contenu

    def lire(self, image: Image) -> tuple[bytes, str]:
        """Résout depuis la mémoire, sinon erreur explicite."""
        for cle in (image.chemin, str(image.document_id) if image.document_id else None):
            if cle and cle in self._contenus:
                return self._contenus[cle], _nom_depuis_cle(cle)
        raise ValidationError(
            "Image introuvable dans le document",
            details={"chemin": image.chemin, "document_id": str(image.document_id or "")},
        )


class ImagesDisque:
    """Fournisseur disque : chemins logiques confinés + fichiers déjà résolus."""

    def __init__(
        self,
        *,
        racine: str | Path | None = None,
        fichiers: Mapping[UUID | str, Path] | None = None,
    ) -> None:
        self._racine = Path(racine) if racine is not None else None
        self._fichiers = {str(cle): Path(valeur) for cle, valeur in (fichiers or {}).items()}

    def ajouter_fichier(self, cle: UUID | str, chemin: Path) -> None:
        """Déclare le fichier physique d'un document (résolu par le service)."""
        self._fichiers[str(cle)] = Path(chemin)

    def lire(self, image: Image) -> tuple[bytes, str]:
        """Lit l'image : ``document_id`` d'abord, puis chemin logique confiné."""
        if image.document_id is not None:
            chemin = self._fichiers.get(str(image.document_id))
            if chemin is not None:
                if not chemin.is_file():
                    raise ValidationError(
                        f"Image absente sur le disque : {chemin.name}",
                        details={"document_id": str(image.document_id)},
                    )
                return chemin.read_bytes(), chemin.name
        if image.chemin:
            if self._racine is None:
                raise ValidationError(
                    "Chemin d'image logique fourni sans racine de stockage",
                    details={"chemin": image.chemin},
                )
            chemin = resolve_within_root(self._racine, image.chemin)
            if not chemin.is_file():
                raise ValidationError(
                    f"Image introuvable : {image.chemin}", details={"chemin": image.chemin}
                )
            return chemin.read_bytes(), chemin.name
        raise ValidationError(
            "Bloc image sans référence (ni document_id ni chemin)",
            details={"largeur_mm": image.largeur_mm},
        )


def resoudre_image(
    image: Image, fournisseur: FournisseurImages | None
) -> tuple[bytes, str] | None:
    """Résout une image si possible, sinon ``None`` (le renderer signale l'absence).

    Le renderer n'a pas à connaître la politique de résolution : il demande, il
    obtient des octets ou ``None``.
    """
    if fournisseur is None:
        return None
    return fournisseur.lire(image)


def _nom_depuis_cle(cle: str) -> str:
    """Nom de fichier lisible pour un contenu en mémoire."""
    nom = cle.replace("\\", "/").rsplit("/", 1)[-1]
    return nom or "image.png"
