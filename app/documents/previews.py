"""Aperçus de rendu d'un document (ADR 0005, Phase 9).

Les images de page sont des **artefacts dérivés**, pas des documents : elles
vivent à côté du fichier d'origine, dans ``<dossier du document>/previews/<tige>/``
et ne créent aucune ligne dans ``documents`` (le fichier officiel reste la seule
source de vérité).

Conséquences assumées :

- **idempotent** : une page déjà rendue n'est jamais réécrite (une version de
  document est immuable : son aperçu l'est aussi) ;
- **confiné** : le chemin est construit, jamais concaténé depuis une entrée, et
  toute résolution repasse par le stockage (``resolve_within_root``) ;
- **découvrable** : ``chemin_apercu`` est la seule source de vérité du nommage,
  partagée par le service (écriture) et par la route HTTP (lecture).
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from app.documents.storage import DocumentStorage

__all__ = [
    "PageApercu",
    "chemin_apercu",
    "dossier_apercu",
    "enregistrer_apercu",
    "resoudre_apercu",
]

#: Sous-dossier des aperçus, à l'intérieur du dossier du document.
SOUS_DOSSIER = "previews"


@dataclass(frozen=True, slots=True)
class PageApercu:
    """Une page rendue en image, adressable par son chemin logique."""

    numero: int
    chemin_logique: str
    largeur_px: int
    hauteur_px: int
    #: ``False`` si l'image existait déjà (aperçu réutilisé, non réécrit).
    cree: bool = False


def dossier_apercu(storage_path: str) -> str:
    """Dossier logique des aperçus d'un document.

    ``offres/<id>/Offre_X_v2.docx`` → ``offres/<id>/previews/Offre_X_v2``.
    """
    dossier, _, nom = storage_path.rpartition("/")
    return f"{dossier}/{SOUS_DOSSIER}/{Path(nom).stem}"


def chemin_apercu(storage_path: str, page: int) -> str:
    """Chemin logique de l'image d'une page (``p01.png``, ``p02.png``…)."""
    return f"{dossier_apercu(storage_path)}/p{int(page):02d}.png"


def enregistrer_apercu(
    storage: DocumentStorage,
    storage_path: str,
    *,
    page: int,
    octets: bytes,
    largeur_px: int = 0,
    hauteur_px: int = 0,
) -> PageApercu:
    """Publie l'image d'une page, ou réutilise l'image déjà présente.

    Returns:
        La page d'aperçu, avec ``cree=False`` si le fichier existait déjà.
    """
    logique = chemin_apercu(storage_path, page)
    if storage.exists(logique):
        return PageApercu(
            numero=int(page),
            chemin_logique=logique,
            largeur_px=largeur_px,
            hauteur_px=hauteur_px,
            cree=False,
        )
    stocke = storage.save(logique, BytesIO(octets))
    return PageApercu(
        numero=int(page),
        chemin_logique=stocke.logical_path,
        largeur_px=largeur_px,
        hauteur_px=hauteur_px,
        cree=True,
    )


def resoudre_apercu(storage: DocumentStorage, storage_path: str, page: int) -> Path:
    """Chemin physique d'une image d'aperçu existante.

    Raises:
        NotFoundError: aperçu absent (document jamais prévisualisé, ou page
            hors du rendu).
        ValidationError: chemin hors de la racine de stockage.
    """
    return storage.resolve_existing(chemin_apercu(storage_path, page))
