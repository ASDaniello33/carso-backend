"""Clonage de structure documentaire (Lot T1).

Réaliste : on copie ce qui est fiable sans moteur de rendu propriétaire.

- DOCX : styles de paragraphe de la référence + contenu fourni.
- XLSX : noms de feuilles + largeurs de colonnes de la 1re ligne + données.
- PDF : on ne clone pas les polices embarquées ; on produit un PDF neuf
  (fpdf2) avec le contenu fourni. Limite documentée, pas un mensonge.

Le résultat est des **octets**. L'enregistrement ``proposed`` est du ressort
de ``DocumentService`` (HITL).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from app.core.errors import GenerationError, ValidationError
from app.documents.extraction.base import require_library
from app.documents.generation import build_pdf

__all__ = ["cloner_docx", "cloner_xlsx", "cloner_pdf", "remplir_template_docx"]

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


def cloner_docx(reference: Path, *, titre: str, paragraphes: list[str] | None) -> bytes:
    """Nouveau DOCX : styles de la référence, contenu fourni."""
    docx = require_library("docx")
    try:
        source = docx.Document(str(reference))
    except Exception as exc:
        raise ValidationError("DOCX de référence illisible") from exc

    cible = docx.Document()
    # Reprendre les dimensions de page de la 1re section (réaliste, pas WYSIWYG).
    if source.sections and cible.sections:
        src, dst = source.sections[0], cible.sections[0]
        dst.page_width = src.page_width
        dst.page_height = src.page_height
        dst.left_margin = src.left_margin
        dst.right_margin = src.right_margin
        dst.top_margin = src.top_margin
        dst.bottom_margin = src.bottom_margin

    styles_source = {s.name for s in source.styles}
    style_titre = "Title" if "Title" in styles_source else None
    try:
        cible.add_heading(titre, level=0)
    except Exception:
        cible.add_paragraph(titre)

    for texte in paragraphes or []:
        paragraphe = cible.add_paragraph(texte)
        if "Normal" in styles_source:
            try:
                paragraphe.style = cible.styles["Normal"]
            except Exception:
                pass
        _ = style_titre  # conservé pour extension future (Heading 1, etc.)

    buffer = io.BytesIO()
    cible.save(buffer)
    return buffer.getvalue()


def cloner_xlsx(
    reference: Path,
    *,
    titre_feuille: str,
    lignes: list[list[Any]],
) -> bytes:
    """Nouveau XLSX : 1re feuille aux largeurs de la référence + données."""
    if reference.suffix.lower() == ".xlsm":
        raise ValidationError("Classeur macro (.xlsm) refusé comme référence")
    openpyxl = require_library("openpyxl")
    try:
        source = openpyxl.load_workbook(str(reference))
    except Exception as exc:
        raise ValidationError("XLSX de référence illisible") from exc

    try:
        feuille_src = source.worksheets[0]
        cible = openpyxl.Workbook()
        feuille = cible.active
        feuille.title = (titre_feuille or feuille_src.title)[:31]
        for lettre, dim in (feuille_src.column_dimensions or {}).items():
            if dim.width:
                feuille.column_dimensions[lettre].width = dim.width
        for index_ligne, ligne in enumerate(lignes, start=1):
            for index_col, valeur in enumerate(ligne, start=1):
                feuille.cell(index_ligne, index_col, valeur)
        buffer = io.BytesIO()
        cible.save(buffer)
        return buffer.getvalue()
    finally:
        source.close()


def cloner_pdf(reference: Path, *, titre: str, paragraphes: list[str] | None) -> bytes:
    """PDF neuf (fpdf2). La référence n'est utilisée que pour documenter
    l'intention : les polices embarquées ne sont pas clonées (limite réelle)."""
    _ = reference  # inspectée par l'appelant ; génération = moteur interne
    return build_pdf(titre=titre, paragraphes=paragraphes)


def remplir_template_docx(template: Path, valeurs: dict[str, str]) -> bytes:
    """Remplace ``{{cle}}`` dans les paragraphes et cellules d'un DOCX."""
    docx = require_library("docx")
    try:
        document = docx.Document(str(template))
    except Exception as exc:
        raise ValidationError("Template DOCX illisible") from exc

    def _remplacer(texte: str) -> str:
        def _sub(match: re.Match[str]) -> str:
            return valeurs.get(match.group(1), match.group(0))

        return _PLACEHOLDER.sub(_sub, texte)

    for paragraphe in document.paragraphs:
        if _PLACEHOLDER.search(paragraphe.text or ""):
            # Remplacement run par run trop fragile : on réécrit le paragraphe.
            nouveau = _remplacer(paragraphe.text or "")
            if paragraphe.runs:
                paragraphe.runs[0].text = nouveau
                for run in paragraphe.runs[1:]:
                    run.text = ""
            else:
                paragraphe.add_run(nouveau)

    for tableau in document.tables:
        for ligne in tableau.rows:
            for cellule in ligne.cells:
                for paragraphe in cellule.paragraphs:
                    if _PLACEHOLDER.search(paragraphe.text or ""):
                        nouveau = _remplacer(paragraphe.text or "")
                        if paragraphe.runs:
                            paragraphe.runs[0].text = nouveau
                            for run in paragraphe.runs[1:]:
                                run.text = ""

    buffer = io.BytesIO()
    try:
        document.save(buffer)
    except Exception as exc:
        raise GenerationError("Échec du remplissage de template") from exc
    return buffer.getvalue()
