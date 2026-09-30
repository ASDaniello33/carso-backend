"""Extraction de texte DOCX (python-docx).

Le contenu utile d'un DOCX métier est rarement uniquement dans les paragraphes :
les fiches techniques et checklists CARSO contiennent des tableaux. Les deux sont
donc extraits, les lignes de tableau étant rendues en texte séparé par ``|``.
"""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ValidationError
from app.documents.extraction.base import ExtractedContent, require_library

_CELL_SEPARATOR = " | "


class DocxExtractor:
    """Extracteur DOCX basé sur python-docx."""

    extension = "docx"
    adaptateur = "python-docx"
    librairie: str = "docx"

    def extract(self, path: Path, *, max_chars: int) -> ExtractedContent:
        docx = require_library(self.librairie)

        try:
            document = docx.Document(str(path))
        except Exception as exc:  # python-docx lève des erreurs variées si corrompu
            msg = "DOCX illisible (fichier corrompu ou format inattendu)"
            raise ValidationError(msg) from exc

        blocs: list[str] = []
        longueur = 0
        tronque = False

        for paragraphe in document.paragraphs:
            texte = (paragraphe.text or "").strip()
            if not texte:
                continue
            blocs.append(texte)
            longueur += len(texte) + 1
            if longueur >= max_chars:
                tronque = True
                break

        if not tronque:
            for tableau in document.tables:
                for ligne in tableau.rows:
                    cellules = [(cellule.text or "").strip() for cellule in ligne.cells]
                    texte = _CELL_SEPARATOR.join(cellules).strip()
                    if not texte.strip(" |"):
                        continue
                    blocs.append(texte)
                    longueur += len(texte) + 1
                    if longueur >= max_chars:
                        tronque = True
                        break
                if tronque:
                    break

        contenu = "\n".join(blocs).strip()
        if not contenu:
            msg = "Aucun texte extractible dans ce DOCX"
            raise ValidationError(msg)

        return ExtractedContent(
            texte=contenu,
            adaptateur=self.adaptateur,
            tronque=tronque,
        )
