"""Classeur XLSX d'une table, **sans dépendance tierce** (bibliothèque standard).

Pourquoi ce module : ``openpyxl`` est bloqué dans l'exécution des scripts de
l'agent (nom de bibliothèque interdit comme ``shutil``, sur lequel il s'appuie),
et son installation au démarrage d'un script n'est pas une option acceptable
sur le serveur. Un classeur d'une seule table — le cas réel des fiches
opérationnelles (présence, liste de bénéficiaires, budget) — s'écrit pourtant
avec ``zipfile`` seul : le format OOXML est un ZIP de parties XML.

Sortie volontairement **non compressée** (``ZIP_STORED``) : la compression
``zipfile`` passe par le module natif de Windows, bloqué par la garde d'audit.
Le fichier reste un vrai ``.xlsx`` lisible par Excel, LibreOffice et
``openpyxl``.

Les données sont toujours écrites en texte (``inlineStr``) : aucune valeur n'est
convertie ni calculée ici, exactement comme le reste de la chaîne documentaire.
Une valeur absente reste vide, jamais inventée.

```text
[Content_Types].xml        types des parties
_rels/.rels                point d'entrée
xl/workbook.xml            feuille(s)
xl/styles.xml              police, remplissages, bordures (thème du document)
xl/worksheets/sheet1.xml   cellules, fusions éventuelles
```
"""

from __future__ import annotations

import io
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from app.documents.styles import DocumentStyle

__all__ = ["classeur_depuis_table", "xml_cellule"]

#: Feuille d'une table : la première ligne est l'en-tête.
_ENTETE_LIGNE = 1

#: Indices des styles posés dans ``styles.xml`` (``cellXfs``) : 0 = normal,
#: 1 = en-tête (fond, gras, blanc), 2 = bande alternée, 3 = bordure seule.
_STYLE_NORMAL = 0
_STYLE_ENTETE = 1
_STYLE_BANDE = 2
_STYLE_FILE = 3


def xml_cellule(reference: str, texte: str, style: int) -> str:
    """Balise ``<c>`` d'une cellule texte (``inlineStr``, style explicite)."""
    attribut = f' s="{style}"' if style else ""
    if not texte:
        return f'<c r="{reference}"{attribut}/>'
    return (
        f'<c r="{reference}"{attribut} t="inlineStr"><is><t xml:space="preserve">'
        f"{escape(texte)}</t></is></c>"
    )


def _lettre_colonne(index: int) -> str:
    """1 → A, 26 → Z, 27 → AA (indices 1-based comme dans un tableur)."""
    lettres = ""
    valeur = index
    while valeur > 0:
        valeur, reste = divmod(valeur - 1, 26)
        lettres = chr(65 + reste) + lettres
    return lettres or "A"


def _largeur_caracteres(lignes: list[list[Any]]) -> list[int]:
    """Largeur de colonne crédible : le plus long texte, borné pour rester lisible."""
    largeur = max((len(ligne) for ligne in lignes), default=0)
    tailles = [0] * largeur
    for ligne in lignes:
        for index, valeur in enumerate(ligne):
            tailles[index] = max(tailles[index], len(str(valeur)) if valeur is not None else 0)
    return [min(max(taille + 2, 8), 60) for taille in tailles]


def _xml_styles(style: DocumentStyle) -> str:
    """Feuille de styles minimale : le thème du document, pas un design inventé."""
    tokens = style.tableau
    fond_entete = tokens.entete_fond.lstrip("#").upper()
    couleur_entete = tokens.entete_couleur.lstrip("#").upper()
    bordure = tokens.bordure_couleur.lstrip("#").upper()
    fond_bande = tokens.bande_fond.lstrip("#").upper()
    taille = max(5, min(72, int(round(tokens.taille_pt * 2))))
    trait = (
        '    <left style="thin"><color rgb="FF' + bordure + '"/></left>\n'
        '    <right style="thin"><color rgb="FF' + bordure + '"/></right>\n'
        '    <top style="thin"><color rgb="FF' + bordure + '"/></top>\n'
        '    <bottom style="thin"><color rgb="FF' + bordure + '"/></bottom>\n'
    )
    filet = "<border>" + trait + "</border>" if tokens.bordures else "<border/>"
    remplissage = (
        "<fill><patternFill patternType='solid'>"
        "<fgColor rgb='FF{rgb}'/>"
        "<bgColor indexed='64'/></patternFill></fill>"
    )
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        "<styleSheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>\n"
        f"<fonts count='3'>"
        f"<font><sz val='{taille / 2:g}'/><name val='Calibri'/></font>"
        f"<font><b/><sz val='{taille / 2:g}'/><name val='Calibri'/></font>"
        f"<font><sz val='{taille / 2:g}'/><color rgb='FF{couleur_entete}'/>"
        "<name val='Calibri'/></font>"
        "</fonts>\n"
        f"<fills count='4'><fill><patternFill patternType='none'/></fill>"
        "<fill><patternFill patternType='gray125'/></fill>"
        + remplissage.format(rgb=fond_entete)
        + remplissage.format(rgb=fond_bande)
        + "</fills>\n"
        "<borders count='2'><border/>"
        + filet
        + "</borders>\n"
        "<cellStyleXfs count='1'><xf numFmtId='0' fontId='0' fillId='0' "
        "borderId='0'/></cellStyleXfs>\n"
        "<cellXfs count='4'>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='0' xfId='0' "
        "applyAlignment='1'><alignment vertical='center' wrapText='1'/></xf>"
        "<xf numFmtId='0' fontId='1' fillId='2' borderId='1' xfId='0' "
        "applyFont='1' applyFill='1' applyBorder='1' applyAlignment='1'>"
        "<alignment vertical='center' wrapText='1'/></xf>"
        "<xf numFmtId='0' fontId='0' fillId='3' borderId='1' xfId='0' "
        "applyFill='1' applyBorder='1' applyAlignment='1'>"
        "<alignment vertical='center' wrapText='1'/></xf>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='1' xfId='0' "
        "applyBorder='1' applyAlignment='1'>"
        "<alignment vertical='center' wrapText='1'/></xf>"
        "</cellXfs>\n"
        "<cellStyles count='1'><cellStyle name='Normal' xfId='0' builtinId='0'/>"
        "</cellStyles>\n"
        "</styleSheet>"
    )


def _xml_feuille(
    titre_feuille: str,
    lignes: list[list[Any]],
    style: DocumentStyle,
) -> str:
    colonnes = _largeur_caracteres(lignes)
    lignes_xml: list[str] = []
    for index_ligne, ligne in enumerate(lignes, start=1):
        en_entete = index_ligne == _ENTETE_LIGNE
        # Meme alternance que le renderer openpyxl : index 0 = en-tete, 1 =
        # premiere donnee (coloree), 2 = deuxieme (blanche)...
        bande = bool(
            style.tableau.bandes_alternees
            and index_ligne > _ENTETE_LIGNE
            and (index_ligne - 1) % 2 == 1
        )
        style_ligne = _STYLE_ENTETE if en_entete else (_STYLE_BANDE if bande else _STYLE_FILE)
        cellules = "".join(
            xml_cellule(
                f"{_lettre_colonne(index_colonne)}{index_ligne}",
                "" if valeur is None else str(valeur),
                style_ligne,
            )
            for index_colonne, valeur in enumerate(ligne, start=1)
        )
        lignes_xml.append(f'<row r="{index_ligne}">{cellules}</row>')
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{largeur}" customWidth="1"/>'
        for index, largeur in enumerate(colonnes, start=1)
    )
    derniere = _lettre_colonne(max(len(colonnes), 1)) + str(max(len(lignes), 1))
    defaite = (
        f'<dimension ref="A1:{derniere}"/>'
        f'<sheetViews><sheetView workbookViewId="0">'
        f'<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols}</cols>"
        "<sheetData>" + "".join(lignes_xml) + "</sheetData>"
        f'<autoFilter ref="A1:{derniere}"/>'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '<pageSetup orientation="portrait" fitToWidth="1"/>'
    )
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        + defaite
        + "</worksheet>"
    )


def classeur_depuis_table(
    *,
    titre_feuille: str,
    lignes: list[list[Any]],
    style: DocumentStyle,
) -> bytes:
    """Octets d'un ``.xlsx`` d'une feuille stylée (aucun calcul, aucune invention).

    Args:
        titre_feuille: nom de l'onglet (nettoyé des caractères interdits Excel).
        lignes: lignes brutes ; la première est l'en-tête.
        style: thème du document (en-tête, bandes, bordures, taille).

    Returns:
        Les octets du classeur, prêts à être déposés.
    """
    nom = "".join(c for c in (titre_feuille or "Feuille") if c not in "[]:*?/\\")[:31]
    nom_sheet = escape(nom.strip() or "Feuille")
    contenu: dict[str, str] = {
        "[Content_Types].xml": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package."
            "relationships+xml'/>"
            "<Default Extension='xml' ContentType='application/xml'/>"
            "<Override PartName='/xl/workbook.xml' ContentType='application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet.main+xml'/>"
            "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/vnd."
            "openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
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
            "<cp:coreProperties xmlns:cp='http://schemas.openxmlformats.org/package/2006/"
            "metadata/core-properties' xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            "<dc:creator>CARSO</dc:creator>"
            f"<dc:title>{escape(nom)}</dc:title>"
            "</cp:coreProperties>"
        ),
        "xl/workbook.xml": (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
            "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
            f"<sheets><sheet name='{nom_sheet}' sheetId='1' r:id='rId1'/></sheets>"
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
        "xl/styles.xml": _xml_styles(style),
        "xl/worksheets/sheet1.xml": _xml_feuille(nom, lignes, style),
    }
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression=zipfile.ZIP_STORED) as archive:
        for chemin, texte in contenu.items():
            archive.writestr(chemin, texte.encode("utf-8"))
    return tampon.getvalue()
