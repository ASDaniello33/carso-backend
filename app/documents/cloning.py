"""Clonage de structure documentaire (Lot T1).

Réaliste : on copie ce qui est fiable sans moteur de rendu propriétaire.

- DOCX : la **référence sert de base** (styles, numérotation, sections,
  en-têtes et pieds conservés) ; le corps est vidé puis rempli du contenu fourni
  (INC-125 : auparavant un document neuf ne gardait que la taille de page).
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


def _vider_corps(document: Any) -> None:
    """Retire le corps du document en gardant sections, en-têtes et pieds.

    ``sectPr`` porte la mise en page (format, marges, en-têtes/pieds) : il reste
    en place. Tout le reste (paragraphes et tableaux du corps) part, pour que le
    contenu fourni s'installe dans le **modèle**, pas dans un document neuf.
    """
    body = document.element.body
    for enfant in list(body):
        if enfant.tag.endswith("sectPr"):
            continue
        body.remove(enfant)


def cloner_docx(reference: Path, *, titre: str, paragraphes: list[str] | None) -> bytes:
    """DOCX produit **dans** la référence : ses styles, entêtes et pieds restent.

    Avant l'INC-125, un document **neuf** était créé : seules la taille de page et
    le style « Normal » du document par défaut survivaient — les styles, la
    numérotation, les en-têtes, les pieds et la couverture du modèle étaient
    perdus. La base est désormais le fichier de référence lui-même.
    """
    docx = require_library("docx")
    try:
        cible = docx.Document(str(reference))
    except Exception as exc:  # noqa: BLE001 - la cause réelle est transmise
        raise ValidationError("DOCX de référence illisible") from exc

    styles_source = {style.name for style in cible.styles}
    _vider_corps(cible)

    try:
        cible.add_heading(titre, level=0)
    except Exception:  # pragma: no cover - style Title absent du modèle
        cible.add_paragraph(titre)

    for texte in paragraphes or []:
        paragraphe = cible.add_paragraph(texte)
        if "Normal" in styles_source:
            try:
                paragraphe.style = cible.styles["Normal"]
            except (KeyError, ValueError):  # pragma: no cover
                pass

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
