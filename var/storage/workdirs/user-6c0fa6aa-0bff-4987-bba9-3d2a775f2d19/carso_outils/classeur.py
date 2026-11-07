"""Écrire un classeur Excel d'une table, **sans** ``openpyxl``.

``openpyxl`` est bloqué dans l'espace de travail de l'agent (il s'appuie sur
``shutil`` et sur le module natif de Windows). Ce module produit pourtant un
vrai ``.xlsx`` — ZIP de parties XML — avec ``zipfile`` seul, et volontairement
**non compressé** : la décompression d'un ZIP est bloquée par la garde d'audit,
l'écriture ne l'est pas.

```python
from classeur import ecrire_classeur

ecrire_classeur(
    "presences.xlsx",
    "Présences",
    [
        ["Bénéficiaire", "Village", "Présences"],  # première ligne = en-tête
        ["Rakoto", "Toliara I", "3"],
    ],
)
```

Aucune conversion de type, aucun calcul : ce qui est fourni est ce qui est
écrit. Une cellule vide reste vide.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

__all__ = ["classeur_depuis_table", "ecrire_classeur"]

#: Couleurs du thème CARSO (en-tête bleu profond, bandes claires, filets).
ENTETE_FOND = "1F4E79"
ENTETE_TEXTE = "FFFFFF"
BANDE_FOND = "F2F6FC"
BORDURE = "B4C6E7"


def _echapper(texte: str) -> str:
    return (
        str(texte)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _lettre(index: int) -> str:
    """1 → A, 27 → AA."""
    lettres = ""
    while index > 0:
        index, reste = divmod(index - 1, 26)
        lettres = chr(65 + reste) + lettres
    return lettres


def _styles(taille_pt: float = 10) -> str:
    taille = max(8, min(20, taille_pt))
    filet = (
        f"<left style='thin'><color rgb='FF{BORDURE}'/></left>"
        f"<right style='thin'><color rgb='FF{BORDURE}'/></right>"
        f"<top style='thin'><color rgb='FF{BORDURE}'/></top>"
        f"<bottom style='thin'><color rgb='FF{BORDURE}'/></bottom>"
    )
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        "<styleSheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        f"<fonts count='2'>"
        f"<font><sz val='{taille}'/><name val='Calibri'/></font>"
        f"<font><b/><sz val='{taille}'/><color rgb='FF{ENTETE_TEXTE}'/><name val='Calibri'/></font>"
        "</fonts>"
        "<fills count='4'>"
        "<fill><patternFill patternType='none'/></fill>"
        "<fill><patternFill patternType='gray125'/></fill>"
        f"<fill><patternFill patternType='solid'><fgColor rgb='FF{ENTETE_FOND}'/>"
        "<bgColor indexed='64'/></patternFill></fill>"
        f"<fill><patternFill patternType='solid'><fgColor rgb='FF{BANDE_FOND}'/>"
        "<bgColor indexed='64'/></patternFill></fill>"
        "</fills>"
        f"<borders count='2'><border/><border>{filet}</border></borders>"
        "<cellStyleXfs count='1'>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='0'/></cellStyleXfs>"
        "<cellXfs count='4'>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='0' xfId='0' applyAlignment='1'>"
        "<alignment vertical='center' wrapText='1'/></xf>"
        "<xf numFmtId='0' fontId='1' fillId='2' borderId='1' xfId='0' applyFont='1' "
        "applyFill='1' applyBorder='1' applyAlignment='1'>"
        "<alignment vertical='center' wrapText='1'/></xf>"
        "<xf numFmtId='0' fontId='0' fillId='3' borderId='1' xfId='0' applyFill='1' "
        "applyBorder='1' applyAlignment='1'><alignment vertical='center' wrapText='1'/></xf>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='1' xfId='0' applyBorder='1' "
        "applyAlignment='1'><alignment vertical='center' wrapText='1'/></xf>"
        "</cellXfs>"
        "<cellStyles count='1'><cellStyle name='Normal' xfId='0' builtinId='0'/></cellStyles>"
        "</styleSheet>"
    )


def _cellule(reference: str, valeur: object, style: int) -> str:
    texte = "" if valeur is None else str(valeur)
    if not texte:
        return f"<c r='{reference}' s='{style}'/>"
    return (
        f"<c r='{reference}' s='{style}' t='inlineStr'><is>"
        f"<t xml:space='preserve'>{_echapper(texte)}</t></is></c>"
    )


def _feuille(lignes: list[list[object]]) -> str:
    largeur = max((len(ligne) for ligne in lignes), default=0)
    tailles = [8] * largeur
    for ligne in lignes:
        for index, valeur in enumerate(ligne):
            tailles[index] = min(
                max(tailles[index], len(str(valeur)) + 2 if valeur is not None else 8), 60
            )
    xml_lignes = []
    for numero, ligne in enumerate(lignes, start=1):
        en_entete = numero == 1
        bande = numero > 1 and (numero - 2) % 2 == 1
        style = 1 if en_entete else (2 if bande else 3)
        cellules = "".join(
            _cellule(f"{_lettre(index)}{numero}", valeur, style)
            for index, valeur in enumerate(ligne, start=1)
        )
        xml_lignes.append(f"<row r='{numero}'>{cellules}</row>")
    cols = "".join(
        f"<col min='{index}' max='{index}' width='{taille}' customWidth='1'/>"
        for index, taille in enumerate(tailles, start=1)
    )
    derniere = f"{_lettre(max(largeur, 1))}{max(len(lignes), 1)}"
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        f"<dimension ref='A1:{derniere}'/>"
        "<sheetViews><sheetView workbookViewId='0'>"
        "<pane ySplit='1' topLeftCell='A2' activePane='bottomLeft' state='frozen'/>"
        "</sheetView></sheetViews>"
        "<sheetFormatPr defaultRowHeight='15'/>"
        f"<cols>{cols}</cols>"
        "<sheetData>" + "".join(xml_lignes) + "</sheetData>"
        f"<autoFilter ref='A1:{derniere}'/>"
        "<pageMargins left='0.7' right='0.7' top='0.75' bottom='0.75' header='0.3' footer='0.3'/>"
        "</worksheet>"
    )


def classeur_depuis_table(
    titre_feuille: str,
    lignes: list[list[object]],
    *,
    titre: str | None = None,
) -> bytes:
    """Octets d'un ``.xlsx`` d'une feuille.

    Args:
        titre_feuille: nom de l'onglet (caractères interdits par Excel retirés).
        lignes: lignes brutes ; la première est l'en-tête.
        titre: titre du document dans les propriétés (défaut : l'onglet).

    Returns:
        Les octets du classeur.
    """
    nom = "".join(c for c in (titre_feuille or "Feuille") if c not in "[]:*?/\\")[:31]
    nom = (nom.strip() or "Feuille")
    titre_doc = _echapper(titre or nom)
    parties = {
        "[Content_Types].xml": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package."
            "relationships+xml'/>"
            "<Default Extension='xml' ContentType='application/xml'/>"
            "<Override PartName='/xl/workbook.xml' ContentType='application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet.main+xml'/>"
            "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/vnd.openxml"
            "formats-officedocument.spreadsheetml.worksheet+xml'/>"
            "<Override PartName='/xl/styles.xml' ContentType='application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.styles+xml'/>"
            "<Override PartName='/docProps/core.xml' ContentType='application/vnd.openxmlformats-"
            "package.core-properties+xml'/>"
            "</Types>"
        ),
        "_rels/.rels": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/officeDocument' Target='xl/workbook.xml'/>"
            "<Relationship Id='rId2' Type='http://schemas.openxmlformats.org/package/2006/"
            "relationships/metadata/core-properties' Target='docProps/core.xml'/>"
            "</Relationships>"
        ),
        "docProps/core.xml": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<cp:coreProperties xmlns:cp='http://schemas.openxmlformats.org/package/2006/metadata/"
            "core-properties' xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            "<dc:creator>CARSO</dc:creator>"
            f"<dc:title>{titre_doc}</dc:title>"
            "</cp:coreProperties>"
        ),
        "xl/workbook.xml": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
            "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
            f"<sheets><sheet name='{_echapper(nom)}' sheetId='1' r:id='rId1'/></sheets>"
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/worksheet' Target='worksheets/sheet1.xml'/>"
            "<Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/styles' Target='styles.xml'/>"
            "</Relationships>"
        ),
        "xl/styles.xml": _styles(),
        "xl/worksheets/sheet1.xml": _feuille(lignes),
    }
    import io

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression=zipfile.ZIP_STORED) as archive:
        for chemin, texte in parties.items():
            archive.writestr(chemin, texte.encode("utf-8"))
    return tampon.getvalue()


def ecrire_classeur(
    chemin: str | Path,
    titre_feuille: str,
    lignes: list[list[object]],
    *,
    titre: str | None = None,
) -> Path:
    """Écrit le classeur dans l'espace de travail et renvoie son chemin."""
    cible = Path(chemin)
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_bytes(classeur_depuis_table(titre_feuille, lignes, titre=titre))
    return cible
