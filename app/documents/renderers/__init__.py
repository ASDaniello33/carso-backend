"""Renderers — ``DocumentSpec`` → DOCX / PDF / HTML / XLSX (ADR 0005, 0007).

Un seul point d'entrée par format, et une façade ``rendre`` qui sélectionne le
renderer à partir du format demandé. Les imports sont paresseux : un format
inutilisé n'impose pas sa librairie au processus.
"""

from __future__ import annotations

from app.core.errors import ValidationError
from app.documents.assets import FournisseurImages
from app.documents.spec import DocumentSpec

__all__ = ["FORMATS_RENDUS", "rendre"]

#: Formats supportés par le pipeline ``DocumentSpec``.
FORMATS_RENDUS: dict[str, tuple[str, str]] = {
    "docx": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "pdf": (".pdf", "application/pdf"),
    "html": (".html", "text/html"),
    "xlsx": (
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


def rendre(
    spec: DocumentSpec,
    *,
    format: str,
    images: FournisseurImages | None = None,
) -> bytes:
    """Rend un document dans le format demandé.

    Args:
        spec: document structuré.
        format: ``docx`` | ``pdf`` | ``html`` | ``xlsx``.
        images: fournisseur d'images (facultatif ; un classeur ne les intègre pas).

    Returns:
        Les octets du document rendu.

    Raises:
        ValidationError: format inconnu (jamais de repli silencieux).
    """
    cle = str(format).strip().lower().lstrip(".")
    if cle not in FORMATS_RENDUS:
        raise ValidationError(
            f"Format de rendu inconnu : {format!r}",
            details={"formats_disponibles": sorted(FORMATS_RENDUS)},
        )
    if cle == "docx":
        from app.documents.renderers.docx import rendre_docx

        return rendre_docx(spec, images=images)
    if cle == "pdf":
        from app.documents.renderers.pdf import rendre_pdf

        return rendre_pdf(spec, images=images)
    if cle == "xlsx":
        from app.documents.renderers.xlsx import rendre_xlsx

        return rendre_xlsx(spec, images=images)
    from app.documents.renderers.html import rendre_html

    return rendre_html(spec, images=images)


def extension_et_mime(format: str) -> tuple[str, str]:
    """Extension et type MIME d'un format de rendu.

    Raises:
        ValidationError: format inconnu.
    """
    cle = str(format).strip().lower().lstrip(".")
    regle = FORMATS_RENDUS.get(cle)
    if regle is None:
        raise ValidationError(f"Format de rendu inconnu : {format!r}")
    return regle
