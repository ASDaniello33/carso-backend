"""Extraction de texte des documents (instruction/06 §1).

Protocole ``TextExtractor`` + registre par extension. Les librairies tierces
sont importées **paresseusement**, au moment de l'extraction : une dépendance
absente produit une erreur explicite, jamais un texte vide silencieux.

Sémantique des échecs (volontairement distincte) :

- type non supporté → ``ValidationError`` (422) : le fichier ne peut pas être
  traité par le système tel qu'il est ;
- librairie d'extraction absente → ``CarsoError`` (500) : problème de
  configuration du serveur (``pip install -e '.[documents]'``) ;
- contenu illisible (PDF chiffré, archive corrompue) → ``ValidationError`` ;
- aucun texte extractible (PDF scanné) → ``ValidationError`` — l'OCR est hors
  périmètre de cette phase, on ne fabrique pas de texte.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import CarsoError, ValidationError
from app.documents.paths import extension_of


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    """Texte extrait d'un document, avec sa provenance.

    ``nb_caracteres`` est la longueur du texte **retourné** (donc bornée par
    ``max_chars``) ; ``tronque`` indique qu'une partie du document n'est pas
    incluse — soit le plafond de service, soit l'arrêt volontaire de
    l'adaptateur (feuille de calcul volumineuse).
    """

    texte: str
    adaptateur: str
    nb_caracteres: int = 0
    nb_pages: int | None = None
    nb_feuilles: int | None = None
    feuilles: tuple[str, ...] = ()
    tronque: bool = False


class TextExtractor(Protocol):
    """Extracteur d'un format documentaire donné."""

    extension: str
    adaptateur: str
    #: Librairie utilisée (``"stdlib"`` si l'extraction n'en requiert aucune).
    librairie: str

    def extract(self, path: Path, *, max_chars: int) -> ExtractedContent: ...


def require_library(module_name: str) -> Any:
    """Importe une librairie d'extraction ou échoue explicitement.

    Raises:
        CarsoError: librairie absente — le serveur n'est pas correctement
            configuré pour ce format (extra ``documents``).
    """
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        msg = (
            f"Extraction indisponible : la librairie '{module_name}' n'est pas "
            "installée (pip install -e '.[documents]')"
        )
        raise CarsoError(msg, details={"librairie": module_name}) from exc


def extract_text(path: Path, *, nom_fichier: str, max_chars: int) -> ExtractedContent:
    """Extrait le texte d'un fichier selon son extension.

    Args:
        path: fichier physique (déjà validé par la couche storage).
        nom_fichier: nom logique portant l'extension (``rapport.pdf``).
        max_chars: plafond de caractères retournés (borne de service).

    Returns:
        Le contenu extrait, ``tronque=True`` si une partie du document n'est pas
        incluse (plafond de service ou arrêt volontaire de l'adaptateur).

    Raises:
        ValidationError: extension sans extracteur disponible.
        CarsoError: librairie d'extraction absente.
    """
    extension = extension_of(nom_fichier)
    extracteur = _EXTRACTORS.get(extension)
    if extracteur is None:
        msg = f"Aucun extracteur de texte pour l'extension .{extension or '?'}"
        raise ValidationError(
            msg, details={"extensions_supportees": sorted(_EXTRACTORS)}
        )

    contenu = extracteur.extract(path, max_chars=max_chars)
    if len(contenu.texte) > max_chars:
        return replace(
            contenu,
            texte=contenu.texte[:max_chars],
            nb_caracteres=max_chars,
            tronque=True,
        )
    return replace(contenu, nb_caracteres=len(contenu.texte))


def supported_extensions() -> frozenset[str]:
    """Extensions disposant d'un extracteur."""
    return frozenset(_EXTRACTORS)


def _build_registry() -> dict[str, TextExtractor]:
    """Construit le registre des extracteurs (imports de modules, sans libs tierces)."""
    from app.documents.extraction.docx import DocxExtractor
    from app.documents.extraction.pdf import PdfExtractor
    from app.documents.extraction.text import TextFileExtractor
    from app.documents.extraction.xlsx import XlsxExtractor

    registre: dict[str, TextExtractor] = {
        "pdf": PdfExtractor(),
        "docx": DocxExtractor(),
        "xlsx": XlsxExtractor(),
    }
    for extension in ("csv", "txt"):
        registre[extension] = TextFileExtractor(extension=extension)
    return registre


_EXTRACTORS: dict[str, TextExtractor] = _build_registry()
