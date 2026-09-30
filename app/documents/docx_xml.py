"""Couche XML DOCX isolée (ADR 0005, Phase 6).

python-docx couvre l'essentiel (styles, paragraphes, tableaux, images, sections)
mais **pas** tout ce qui fait un livrable professionnel : répétition de la ligne
d'en-tête d'un tableau, trames de cellules, filets de tableau, largeurs de
colonnes en mise en page fixe, espacements inter-paragraphes exacts, champ de
numérotation de page, filet de pied de page.

Ces cas sont regroupés ici, derrière des fonctions pures et testables, plutôt
que dispersés dans le renderer. Chaque fonction reçoit un objet python-docx
(``Table``, ``_Cell``, ``Paragraph``) et n'écrit que le XML nécessaire — aucune
règle métier, aucun accès disque, aucune dépendance à Pydantic.

Les paramètres sont typés ``Any`` volontairement : python-docx expose ses
éléments XML via des API privées (``_tbl``, ``_p``, ``_tc``) dont le type exact
change d'une version mineure à l'autre. Le contrat de ces fonctions est
**testé** sur un document réel (``tests/test_documents_docx_xml.py``).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from app.documents.styles import MM_PAR_POUCE

__all__ = [
    "bordures_tableau",
    "champ_numero_page",
    "espacement_paragraphe",
    "filet_haut_paragraphe",
    "largeurs_colonnes",
    "mise_en_page_fixe",
    "ombrer_cellule",
    "repetition_entete",
]

_TWIPS_PAR_MM = 1440.0 / MM_PAR_POUCE
_SZ_PAR_POINT = 8  # ``w:sz`` s'exprime en huitièmes de point


def _twips(millimetres: float) -> int:
    """Millimètres → twips (unité de mesure OOXML pour les longueurs)."""
    return int(round(millimetres * _TWIPS_PAR_MM))


def _points(valeur: float) -> int:
    """Points → centièmes de point (``w:before``/``w:after``)."""
    return int(round(valeur * 20))


def _element(nom: str, **attributs: str) -> Any:
    """Crée un élément OOXML du namespace ``w`` (``"spacing"`` → ``<w:spacing>``)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    balise = nom if ":" in nom else f"w:{nom}"
    element = OxmlElement(balise)
    for cle, valeur in attributs.items():
        element.set(qn(f"w:{cle}"), valeur)
    return element


def mise_en_page_fixe(tableau: Any) -> None:
    """Fixe la mise en page du tableau (les largeurs déclarées sont respectées).

    Sans ``w:tblLayout type="fixed"``, Word recalcule les largeurs selon le
    contenu : une colonne « Montant » se retrouve écrasée.
    """
    tbl_pr = tableau._tbl.tblPr
    for existant in tbl_pr.findall(_qn("w:tblLayout")):
        tbl_pr.remove(existant)
    tbl_pr.append(_element("w:tblLayout", type="fixed"))


def largeurs_colonnes(tableau: Any, largeurs_mm: list[float]) -> None:
    """Applique des largeurs de colonnes (mm) : grille **et** cellules.

    Word n'utilise pas toujours la grille seule : on écrit aussi ``w:tcW`` sur
    chaque cellule pour que le rendu soit identique dans Word et LibreOffice.
    """
    if not largeurs_mm:
        return
    nb_colonnes = len(tableau.columns)
    if len(largeurs_mm) != nb_colonnes:
        raise ValidationError(
            "Nombre de largeurs différent du nombre de colonnes",
            details={"largeurs": len(largeurs_mm), "colonnes": nb_colonnes},
        )
    grille = tableau._tbl.find(_qn("w:tblGrid"))
    if grille is not None:
        for index, colonne in enumerate(grille.findall(_qn("w:gridCol"))):
            colonne.set(_qn("w:w"), str(_twips(largeurs_mm[index])))
    for ligne in tableau.rows:
        for index, cellule in enumerate(ligne.cells):
            cellule.width = _emu(largeurs_mm[index])
            tc_pr = cellule._tc.get_or_add_tcPr()
            for existant in tc_pr.findall(_qn("w:tcW")):
                tc_pr.remove(existant)
            tc_pr.append(_element("tcW", w=str(_twips(largeurs_mm[index])), type="dxa"))


def _emu(millimetres: float) -> int:
    """Millimètres → EMU (python-docx attend des EMU pour ``width``)."""
    return int(round(millimetres * 36000.0))


def bordures_tableau(
    tableau: Any, *, couleur: str, taille_pt: float
) -> None:
    """Trace les filets du tableau (extérieur et intérieurs)."""
    tbl_pr = tableau._tbl.tblPr
    for existant in tbl_pr.findall(_qn("w:tblBorders")):
        tbl_pr.remove(existant)
    bordures = _element("tblBorders")
    for cote in ("top", "left", "bottom", "right", "insideH", "insideV"):
        bordure = _element(
            cote,
            val="single",
            sz=str(max(2, int(round(taille_pt * _SZ_PAR_POINT)))),
            space="0",
            color=couleur.lstrip("#").upper(),
        )
        bordures.append(bordure)
    tbl_pr.append(bordures)


def ombrer_cellule(cellule: Any, couleur: str) -> None:
    """Applique une trame de fond à une cellule (``w:shd``)."""
    tc_pr = cellule._tc.get_or_add_tcPr()
    for existant in tc_pr.findall(_qn("w:shd")):
        tc_pr.remove(existant)
    tc_pr.append(_element("shd", val="clear", color="auto", fill=couleur.lstrip("#").upper()))


def repetition_entete(ligne: Any) -> None:
    """Répète une ligne de tableau en haut de chaque page (``w:tblHeader``)."""
    tr_pr = ligne._tr.get_or_add_trPr()
    if not tr_pr.findall(_qn("w:tblHeader")):
        tr_pr.append(_element("tblHeader", val="true"))


def espacement_paragraphe(
    paragraph: Any, *, avant_pt: float = 0.0, apres_pt: float = 0.0, interligne: float = 1.15
) -> None:
    """Espacements exacts d'un paragraphe (avant/après en points, interligne multiple)."""
    p_pr = paragraph._p.get_or_add_pPr()
    for existant in p_pr.findall(_qn("w:spacing")):
        p_pr.remove(existant)
    p_pr.append(
        _element(
            "spacing",
            before=str(_points(avant_pt)),
            after=str(_points(apres_pt)),
            line=str(int(round(240 * interligne))),
            lineRule="auto",
        )
    )


def filet_haut_paragraphe(paragraph: Any, *, couleur: str, taille_pt: float = 0.75) -> None:
    """Trace un filet au-dessus d'un paragraphe (séparateur d'en-tête/pied)."""
    p_pr = paragraph._p.get_or_add_pPr()
    for existant in p_pr.findall(_qn("w:pBdr")):
        p_pr.remove(existant)
    bordures = _element("pBdr")
    bordure = _element(
        "top",
        val="single",
        sz=str(max(2, int(round(taille_pt * _SZ_PAR_POINT)))),
        space="1",
        color=couleur.lstrip("#").upper(),
    )
    bordures.append(bordure)
    p_pr.append(bordures)


def champ_numero_page(paragraph: Any) -> None:
    """Insère le champ Word ``PAGE`` (numéro de page automatique).

    Le champ est un ``w:fldSimple`` : Word le met à jour à l'ouverture. Un texte
    de repli (``1``) est fourni pour les lecteurs qui n'évaluent pas les champs.
    """
    fld = _element("fldSimple")
    fld.set(_qn("w:instr"), " PAGE \\* MERGEFORMAT ")
    run = _element("r")
    texte = _element("t")
    texte.text = "1"
    run.append(texte)
    fld.append(run)
    paragraph._p.append(fld)


def _qn(chemin: str) -> str:
    """Résout un nom qualifié OOXML (``w:tblLayout``)."""
    from docx.oxml.ns import qn

    return qn(chemin)
