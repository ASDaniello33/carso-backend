"""Extraction de texte PDF (pypdf).

Un PDF sans couche texte (document scanné) n'est **pas** silencieusement
retourné vide : l'OCR est hors périmètre, l'appelant reçoit une erreur explicite.

L'extraction s'arrête dès que le plafond de caractères est atteint
(``tronque=True``) : les documents volumineux restent bornés.
"""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ValidationError
from app.documents.extraction.base import ExtractedContent, require_library


class PdfExtractor:
    """Extracteur PDF basé sur pypdf."""

    extension = "pdf"
    adaptateur = "pypdf"
    librairie: str = "pypdf"

    def extract(self, path: Path, *, max_chars: int) -> ExtractedContent:
        pypdf = require_library(self.librairie)

        try:
            lecteur = pypdf.PdfReader(str(path))
        except Exception as exc:  # pypdf lève des erreurs variées sur fichier corrompu
            msg = "PDF illisible (fichier corrompu ou protégé)"
            raise ValidationError(msg) from exc

        if lecteur.is_encrypted:
            msg = "PDF chiffré : extraction impossible sans mot de passe"
            raise ValidationError(msg)

        pages: list[str] = []
        longueur = 0
        tronque = False
        for page in lecteur.pages:
            texte_page = self._texte_page(page)
            pages.append(texte_page)
            longueur += len(texte_page)
            if longueur >= max_chars:
                tronque = True
                break

        texte = "\n\n".join(page.strip() for page in pages).strip()
        if not texte:
            msg = (
                "Aucun texte extractible dans ce PDF (document probablement scanné ; "
                "l'OCR n'est pas pris en charge)"
            )
            raise ValidationError(msg)

        return ExtractedContent(
            texte=texte,
            adaptateur=self.adaptateur,
            nb_pages=len(pages),
            tronque=tronque,
        )

    def _texte_page(self, page: object) -> str:
        """Texte d'une page ; une page atypique ne fait pas échouer le document."""
        try:
            return str(page.extract_text() or "")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - robustesse sur PDF hétérogènes
            return ""
