"""Rendu DOCX natif : le HTML est converti en vrais objets Word (styles, tableaux, champs).

Contrairement à une conversion HTML -> DOCX générique (qui perd le CSS), chaque composant
du thème (couverture, KPI, encadrés, tableaux...) a ici son équivalent Word dédié.
Le document reste entièrement modifiable dans Word.
"""

from __future__ import annotations

import base64
import datetime
import io
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from PIL import Image

from .html_builder import DocumentPrepare
from .rasterisation import dimension_px
from .themes import BORDURE, CALLOUT_VARIANTES, MUTED, TEXTE, Theme, get_theme

POLICE = "Calibri"
BLANC = "#FFFFFF"
INLINE = {"a", "abbr", "b", "br", "code", "em", "i", "img", "label", "mark", "s", "small",
          "span", "strong", "sub", "sup", "time", "u"}
SANS_BORDURE = {c: None for c in ("top", "left", "bottom", "right", "insideH", "insideV")}

# Ordre imposé par le schéma OOXML (Word refuse parfois un ordre différent)
ORDRE_TCPR = [
    "w:cnfStyle", "w:tcW", "w:gridSpan", "w:hMerge", "w:vMerge", "w:tcBorders", "w:shd",
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark",
]
ORDRE_TBLPR = [
    "w:tblStyle", "w:tblpPr", "w:tblOverlap", "w:bidiVisual", "w:tblStyleRowBandSize",
    "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing", "w:tblInd",
    "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption",
    "w:tblDescription",
]
ORDRE_PPR = [
    "w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore", "w:framePr",
    "w:widowControl", "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd", "w:tabs",
    "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct", "w:topLinePunct",
    "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd", "w:snapToGrid",
    "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap",
    "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
    "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
]
SUIVANTS_UPDATEFIELDS = {
    "hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids", "mathPr",
    "attachedSchema", "themeFontLang", "clrSchemeMapping", "doNotIncludeSubdocsInStats",
    "doNotAutoCompressPictures", "forceUpgrade", "captions", "readModeInkLockDown",
    "smartTagType", "schemaLibrary", "shapeDefaults", "doNotEmbedSmartTags",
    "decimalSymbol", "listSeparator",
}


# --------------------------------------------------------------------------- helpers XML
def _rgb(couleur: str) -> RGBColor:
    return RGBColor.from_string(couleur.lstrip("#").upper())


def _placer(parent, element, ordre: list[str]):
    """Insère `element` dans `parent` en respectant l'ordre du schéma (remplace l'existant)."""
    noms = [qn(n) for n in ordre]
    for ancien in parent.findall(element.tag):
        parent.remove(ancien)
    suivants = set(noms[noms.index(element.tag) + 1:])
    for i, enfant in enumerate(parent):
        if enfant.tag in suivants:
            parent.insert(i, element)
            return element
    parent.append(element)
    return element


def _bordures(nom: str, cotes: dict) -> Any:
    conteneur = OxmlElement(nom)
    for cote in ("top", "left", "bottom", "right", "insideH", "insideV"):
        if cote not in cotes:
            continue
        e = OxmlElement(f"w:{cote}")
        valeur = cotes[cote]
        if valeur is None:
            e.set(qn("w:val"), "nil")
        else:
            taille, couleur = valeur
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), str(taille))
            e.set(qn("w:space"), "0")
            e.set(qn("w:color"), couleur.lstrip("#"))
        conteneur.append(e)
    return conteneur


def _marges(nom: str, haut: int, gauche: int, bas: int, droite: int) -> Any:
    conteneur = OxmlElement(nom)
    for cote, valeur in (("top", haut), ("left", gauche), ("bottom", bas), ("right", droite)):
        e = OxmlElement(f"w:{cote}")
        e.set(qn("w:w"), str(valeur))
        e.set(qn("w:type"), "dxa")
        conteneur.append(e)
    return conteneur


def _ombrage(couleur: str) -> Any:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), couleur.lstrip("#"))
    return shd


def _definir_police(rpr, nom: str) -> None:
    rfonts = rpr.get_or_add_rFonts()
    for attribut in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attribut), None)
    for attribut in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attribut), nom)


def _bordure_paragraphe(ppr, cote: str, couleur: str, taille: int = 6, espace: int = 1) -> None:
    pbdr = ppr.find(qn("w:pBdr"))
    if pbdr is None:
        pbdr = _placer(ppr, OxmlElement("w:pBdr"), ORDRE_PPR)
    ancien = pbdr.find(qn(f"w:{cote}"))
    if ancien is not None:
        pbdr.remove(ancien)
    e = OxmlElement(f"w:{cote}")
    e.set(qn("w:val"), "single")
    e.set(qn("w:sz"), str(taille))
    e.set(qn("w:space"), str(espace))
    e.set(qn("w:color"), couleur.lstrip("#"))
    pbdr.append(e)


def _champ(paragraphe, instruction: str, texte_defaut: str = ""):
    """Ajoute un champ Word (PAGE, NUMPAGES, TOC...) et retourne le run du texte affiché."""
    def fld(type_):
        run = paragraphe.add_run()
        e = OxmlElement("w:fldChar")
        e.set(qn("w:fldCharType"), type_)
        run._r.append(e)

    fld("begin")
    run = paragraphe.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run._r.append(instr)
    fld("separate")
    affiche = paragraphe.add_run(texte_defaut)
    fld("end")
    return affiche


def _colspan(cellule: Tag) -> int:
    try:
        return max(1, int(str(cellule.get("colspan") or 1)))
    except ValueError:
        return 1


def _classes(el: Tag) -> list[str]:
    """Classes CSS d'un élément (BeautifulSoup renvoie une liste, ou une chaîne)."""
    valeur: Any = el.get("class") or []
    if isinstance(valeur, str):
        return valeur.split()
    return [str(classe) for classe in valeur]


def _nettoyer_cellule(cell) -> None:
    """Supprime le paragraphe vide initial d'une cellule (une cellule finit par un paragraphe)."""
    tc = cell._tc
    blocs: list[Any] = [e for e in tc if e.tag in (qn("w:p"), qn("w:tbl"))]
    if len(blocs) > 1 and blocs[0].tag == qn("w:p") and blocs[0].find(qn("w:r")) is None:
        tc.remove(blocs[0])
        blocs = blocs[1:]
    if blocs and blocs[-1].tag == qn("w:tbl"):
        tc.append(OxmlElement("w:p"))


# --------------------------------------------------------------------------- styles & page
def _configurer_styles(doc, t: Theme) -> None:
    styles = doc.styles
    normal = styles["Normal"]
    _definir_police(normal.element.get_or_add_rPr(), POLICE)
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = _rgb(TEXTE)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    for nom, taille, couleur, avant, apres in (
        ("Heading 1", 20, t.primary, 18, 8),
        ("Heading 2", 14, t.primary, 14, 4),
        ("Heading 3", 11.5, t.accent, 10, 3),
    ):
        style = styles[nom]
        _definir_police(style.element.get_or_add_rPr(), POLICE)
        style.font.size = Pt(taille)
        style.font.bold = True
        style.font.italic = False
        style.font.color.rgb = _rgb(couleur)
        style.paragraph_format.space_before = Pt(avant)
        style.paragraph_format.space_after = Pt(apres)
        style.paragraph_format.keep_with_next = True
    _bordure_paragraphe(styles["Heading 1"].element.get_or_add_pPr(), "bottom", t.accent, 12, 4)

    lead = styles.add_style("Lead", WD_STYLE_TYPE.PARAGRAPH)
    lead.base_style = normal
    lead.font.size = Pt(12)
    lead.font.color.rgb = _rgb(MUTED)
    lead.paragraph_format.space_after = Pt(10)

    legende = styles.add_style("Legende", WD_STYLE_TYPE.PARAGRAPH)
    legende.base_style = normal
    legende.font.size = Pt(8.5)
    legende.font.italic = True
    legende.font.color.rgb = _rgb(MUTED)
    legende.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    legende.paragraph_format.space_after = Pt(8)

    for nom in (
        "List Bullet",
        "List Number",
        "List Bullet 2",
        "List Number 2",
        "List Bullet 3",
        "List Number 3",
    ):
        try:
            styles[nom].paragraph_format.space_after = Pt(3)
        except KeyError:
            pass


def _configurer_page(doc, titre: str, paysage: bool, avec_couverture: bool) -> None:
    section = doc.sections[0]
    section.page_width, section.page_height = (Mm(297), Mm(210)) if paysage else (Mm(210), Mm(297))
    section.orientation = WD_ORIENT.LANDSCAPE if paysage else WD_ORIENT.PORTRAIT
    section.top_margin, section.bottom_margin = Mm(24), Mm(20)
    section.left_margin = section.right_margin = Mm(18)
    section.header_distance = section.footer_distance = Mm(10)
    section.different_first_page_header_footer = avec_couverture

    entete = section.header.paragraphs[0]
    run = entete.add_run(titre)
    run.font.size = Pt(8)
    run.font.color.rgb = _rgb(MUTED)
    _bordure_paragraphe(entete._p.get_or_add_pPr(), "bottom", BORDURE, 4, 4)

    pied = section.footer.paragraphs[0]
    pied.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _champ(pied, "PAGE", "1")
    pied.add_run(" / ")
    _champ(pied, "NUMPAGES", "1")
    for r in pied.runs:
        r.font.size = Pt(8)
        r.font.color.rgb = _rgb(MUTED)


def _activer_maj_champs(doc) -> None:
    """Demande à Word de mettre à jour les champs (sommaire) à l'ouverture."""
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    element = OxmlElement("w:updateFields")
    element.set(qn("w:val"), "true")
    for i, enfant in enumerate(settings):
        if isinstance(enfant.tag, str) and enfant.tag.split("}")[-1] in SUIVANTS_UPDATEFIELDS:
            settings.insert(i, element)
            return
    settings.append(element)


def _metadonnees(doc, titre: str, auteur: str) -> None:
    maintenant = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    cp = doc.core_properties
    cp.title = titre
    cp.author = auteur or ""
    cp.last_modified_by = auteur or ""
    cp.created = maintenant
    cp.modified = maintenant
    cp.revision = 1
    cp.comments = ""
    cp.subject = ""
    cp.keywords = ""
    cp.category = ""


def _taille_declaree(el: Tag) -> tuple[float, float] | None:
    """``width``/``height`` d'une balise en pixels logiques, si exploitables.

    Une image née d'un SVG porte sa taille logique ici (voir
    ``html_builder.convertir_svg_en_png``) : c'est elle qui dit la place que le
    schéma doit occuper, et non les pixels du PNG embarqué (rendu au double pour
    la netteté à l'impression).
    """

    largeur, hauteur = dimension_px(el.get("width")), dimension_px(el.get("height"))
    if largeur is None or hauteur is None:
        return None
    return largeur, hauteur


# ---------------------------------------------------------- conversion HTML -> Word
class _Rendu:
    def __init__(
        self,
        doc,
        theme: Theme,
        base_dir: Path,
        largeur_utile_mm: float,
        hauteur_couverture_mm: float,
        hauteur_utile_mm: float,
    ):
        self.doc = doc
        self.t = theme
        self.base_dir = base_dir
        self.largeur_utile_mm = largeur_utile_mm
        self.hauteur_couverture_mm = hauteur_couverture_mm
        self.hauteur_utile_mm = hauteur_utile_mm
        self.a_sommaire = False

    # ---- utilitaires
    def _style(self, nom: str):
        try:
            return self.doc.styles[nom]
        except KeyError:
            return None

    def _espace(self, cont) -> None:
        p = cont.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = Pt(6)

    @staticmethod
    def _rogner(p) -> None:
        if p.runs and p.runs[-1].text.endswith(" "):
            p.runs[-1].text = p.runs[-1].text.rstrip()

    def _style_cellule(self, cell, fond=None, bordures=None, marges=None) -> None:
        tcpr = cell._tc.get_or_add_tcPr()
        if bordures is not None:
            _placer(tcpr, _bordures("w:tcBorders", bordures), ORDRE_TCPR)
        if fond:
            _placer(tcpr, _ombrage(fond), ORDRE_TCPR)
        if marges:
            _placer(tcpr, _marges("w:tcMar", *marges), ORDRE_TCPR)

    def _preparer_table(
        self,
        table,
        bordures: dict,
        marges=(60, 100, 60, 100),
        espacement: int | None = None,
    ):
        tblpr = table._tbl.tblPr
        largeur = OxmlElement("w:tblW")
        largeur.set(qn("w:w"), "5000")
        largeur.set(qn("w:type"), "pct")
        _placer(tblpr, largeur, ORDRE_TBLPR)
        if espacement:
            e = OxmlElement("w:tblCellSpacing")
            e.set(qn("w:w"), str(espacement))
            e.set(qn("w:type"), "dxa")
            _placer(tblpr, e, ORDRE_TBLPR)
        _placer(tblpr, _bordures("w:tblBorders", bordures), ORDRE_TBLPR)
        _placer(tblpr, _marges("w:tblCellMar", *marges), ORDRE_TBLPR)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

    def _remplir(self, cell, element: Tag) -> None:
        self.blocs(cell, element.contents)
        _nettoyer_cellule(cell)

    def _paragraphe(self, cont, noeuds, style=None, fmt=None):
        p = cont.add_paragraph(style=style)
        self.inline(p, noeuds, fmt)
        self._rogner(p)
        return p

    # ---- niveau bloc
    def blocs(self, cont, noeuds, niveau_liste: int = 0) -> None:
        tampon: list = []

        def vider():
            a_contenu = any(
                (isinstance(n, NavigableString) and n.strip()) or isinstance(n, Tag)
                for n in tampon
            )
            if a_contenu:
                self._paragraphe(cont, list(tampon))
            tampon.clear()

        for n in noeuds:
            if isinstance(n, Comment):
                continue
            if isinstance(n, NavigableString):
                tampon.append(n)
            elif isinstance(n, Tag):
                if n.name in INLINE and n.name != "img":
                    tampon.append(n)
                else:
                    vider()
                    self.bloc(cont, n, niveau_liste)
        vider()

    def bloc(self, cont, el: Tag, niveau_liste: int = 0) -> None:
        nom = el.name
        classes = _classes(el)
        if nom in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._paragraphe(cont, el.contents, style=f"Heading {min(int(nom[1]), 3)}")
        elif nom == "p":
            self._paragraphe(cont, el.contents, style="Lead" if "lead" in classes else None)
        elif nom in ("ul", "ol"):
            self.liste(cont, el, nom == "ol", niveau_liste)
        elif nom == "table":
            if "kpi" in classes:
                self.kpi(cont, el)
            elif "layout" in classes:
                self.layout(cont, el)
            else:
                self.tableau(cont, el)
        elif "page-break" in classes:
            cont.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        elif "callout" in classes or nom == "blockquote":
            self.callout(cont, el)
        elif nom == "nav" and "toc" in classes:
            self.sommaire(cont, el)
        elif nom == "section" and "cover" in classes:
            self.couverture(cont, el)
        elif "signature" in classes:
            self.signature(cont, el)
        elif nom == "figure":
            self.figure(cont, el)
        elif nom == "img":
            self.image(cont, el)
        elif nom == "hr":
            p = cont.add_paragraph()
            _bordure_paragraphe(p._p.get_or_add_pPr(), "bottom", BORDURE, 6)
        elif nom == "pre":
            p = cont.add_paragraph()
            for i, ligne in enumerate(el.get_text().split("\n")):
                if i:
                    p.add_run().add_break()
                run = p.add_run(ligne)
                _definir_police(run._r.get_or_add_rPr(), "Consolas")
                run.font.size = Pt(9)
        else:  # div, section, article, header, footer, main...
            self.blocs(cont, el.contents, niveau_liste)

    # ---- niveau texte
    def inline(self, p, noeuds, fmt: dict | None = None) -> None:
        fmt = fmt or {}
        for n in noeuds:
            if isinstance(n, Comment):
                continue
            if isinstance(n, NavigableString):
                texte = re.sub(r"\s+", " ", str(n))
                if not p.text or p.text.endswith((" ", "\n")):
                    texte = texte.lstrip()
                if texte:
                    self._appliquer(p.add_run(texte), fmt)
                continue
            if not isinstance(n, Tag):
                continue
            nom = n.name
            classes = _classes(n)
            f = dict(fmt)
            if nom == "br":
                p.add_run().add_break()
                continue
            if nom == "img":
                self._image_dans(p, n)
                continue
            if nom == "span" and "badge" in classes:
                run = p.add_run(f" {n.get_text(' ', strip=True)} ")
                run.bold = True
                run.font.size = Pt(8)
                run.font.color.rgb = _rgb(BLANC)
                run._r.get_or_add_rPr().append(_ombrage(self.t.accent))
                continue
            if nom in ("p", "div", "li") and p.text.strip():
                p.add_run().add_break()
            if nom in ("strong", "b"):
                f["bold"] = True
                f.setdefault("couleur", self.t.primary)
            elif nom in ("em", "i"):
                f["italic"] = True
            elif nom == "u":
                f["underline"] = True
            elif nom == "a":
                f.setdefault("couleur", self.t.accent)
            elif nom == "code":
                f["police"] = "Consolas"
            elif nom == "sup":
                f["exposant"] = True
            elif nom == "sub":
                f["indice"] = True
            elif nom == "small":
                f["taille"] = 8.5
            self.inline(p, n.contents, f)

    @staticmethod
    def _appliquer(run, f: dict) -> None:
        if f.get("bold"):
            run.bold = True
        if f.get("italic"):
            run.italic = True
        if f.get("underline"):
            run.underline = True
        if f.get("couleur"):
            run.font.color.rgb = _rgb(f["couleur"])
        if f.get("taille"):
            run.font.size = Pt(f["taille"])
        if f.get("police"):
            _definir_police(run._r.get_or_add_rPr(), f["police"])
        if f.get("exposant"):
            run.font.superscript = True
        if f.get("indice"):
            run.font.subscript = True

    # ---- listes
    def _nouvelle_numerotation(self, style):
        """Crée une nouvelle instance de numérotation pour que chaque <ol> recommence à 1."""
        try:
            numbering = self.doc.part.numbering_part.element
            num_id = style.element.pPr.numPr.numId.val
            abstract_id = numbering.num_having_numId(num_id).abstractNumId.val
            nouveau = numbering.add_num(abstract_id)
            nouveau.add_lvlOverride(ilvl=0).add_startOverride(1)
            return nouveau.numId
        except Exception:  # noqa: BLE001 - modèle sans numérotation : on garde le style seul
            return None

    def liste(self, cont, el: Tag, ordonnee: bool, niveau: int) -> None:
        base = "List Number" if ordonnee else "List Bullet"
        choisi = base if niveau == 0 else f"{base} {min(niveau + 1, 3)}"
        style = self._style(choisi) or self._style(base)
        num_id = (
            self._nouvelle_numerotation(style)
            if ordonnee and niveau == 0 and style is not None
            else None
        )
        for li in el.find_all("li", recursive=False):
            contenu = [
                c
                for c in li.contents
                if not (isinstance(c, Tag) and c.name in ("ul", "ol"))
            ]
            p = self._paragraphe(cont, contenu, style=style)
            if num_id is not None:
                numpr = p._p.get_or_add_pPr().get_or_add_numPr()
                numpr.get_or_add_ilvl().val = 0
                numpr.get_or_add_numId().val = num_id
            for sous in li.find_all(["ul", "ol"], recursive=False):
                self.liste(cont, sous, sous.name == "ol", niveau + 1)

    # ---- tableaux
    def tableau(self, cont, el: Tag) -> None:
        lignes: list[tuple[str, list[Any]]] = []
        for tr in el.find_all("tr"):
            if tr.find_parent("table") is not el:
                continue
            cellules_ligne: list[Any] = list(
                tr.find_all(["td", "th"], recursive=False)
            )
            if not cellules_ligne:
                continue
            nom_parent = tr.parent.name if tr.parent is not None else ""
            section = nom_parent if nom_parent in ("thead", "tfoot") else "tbody"
            if (
                section == "tbody"
                and not lignes
                and el.find("thead") is None
                and all(c.name == "th" for c in cellules_ligne)
            ):
                section = "thead"
            lignes.append((section, cellules_ligne))
        if not lignes:
            return
        lignes = [item for item in lignes if item[0] != "tfoot"] + [
            item for item in lignes if item[0] == "tfoot"
        ]
        ncols = max(sum(_colspan(c) for c in cs) for _, cs in lignes)

        table = cont.add_table(rows=len(lignes), cols=ncols)
        self._preparer_table(table, {
            "top": None, "left": None, "right": None, "insideV": None,
            "bottom": (8, self.t.primary), "insideH": (4, BORDURE),
        })
        index_corps = 0
        for i, (section, cellules) in enumerate(lignes):
            trpr = table.rows[i]._tr.get_or_add_trPr()
            trpr.append(OxmlElement("w:cantSplit"))
            fond = None
            if section == "thead":
                trpr.append(OxmlElement("w:tblHeader"))  # en-tête répété sur chaque page
                fond = self.t.primary
            elif section == "tbody":
                fond = self.t.soft if index_corps % 2 == 1 else None
                index_corps += 1
            j = 0
            for c in cellules:
                if j >= ncols:
                    break
                span = min(_colspan(c), ncols - j)
                cell = table.cell(i, j)
                if span > 1:
                    cell = cell.merge(table.cell(i, j + span - 1))
                bordures_ligne = (
                    {"top": (12, self.t.primary)} if section == "tfoot" else None
                )
                self._style_cellule(cell, fond=fond, bordures=bordures_ligne)
                self._remplir(cell, c)
                droite = "num" in _classes(c)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(0)
                    if droite:
                        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    for r in p.runs:
                        if section == "thead":
                            r.bold = True
                            r.font.color.rgb = _rgb(BLANC)
                        elif section == "tfoot":
                            r.bold = True
                j += span
        self._espace(cont)

    def kpi(self, cont, el: Tag) -> None:
        lignes = [tr for tr in el.find_all("tr") if tr.find_parent("table") is el]
        lignes = [tr for tr in lignes if tr.find_all(["td", "th"], recursive=False)]
        if not lignes:
            return
        ncols = max(len(tr.find_all(["td", "th"], recursive=False)) for tr in lignes)
        table = cont.add_table(rows=len(lignes), cols=ncols)
        self._preparer_table(table, SANS_BORDURE, marges=(0, 0, 0, 0), espacement=80)
        for i, tr in enumerate(lignes):
            for j, td in enumerate(tr.find_all(["td", "th"], recursive=False)):
                cell = table.cell(i, j)
                self._style_cellule(
                    cell,
                    fond=self.t.soft,
                    bordures={
                        "top": (24, self.t.accent),
                        "left": None,
                        "bottom": None,
                        "right": None,
                    },
                    marges=(160, 80, 160, 80),
                )
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                valeur = td.find(class_="kpi-value")
                label = td.find(class_="kpi-label") if valeur is not None else None
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(2)
                run = p.add_run((valeur or td).get_text(" ", strip=True))
                run.bold = True
                run.font.size = Pt(20)
                run.font.color.rgb = _rgb(self.t.primary)
                if label is not None:
                    p2 = cell.add_paragraph()
                    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p2.paragraph_format.space_after = Pt(0)
                    r2 = p2.add_run(label.get_text(" ", strip=True).upper())
                    r2.font.size = Pt(8)
                    r2.font.color.rgb = _rgb(MUTED)
        self._espace(cont)

    def layout(self, cont, el: Tag) -> None:
        lignes = [tr for tr in el.find_all("tr") if tr.find_parent("table") is el]
        lignes = [tr for tr in lignes if tr.find_all(["td", "th"], recursive=False)]
        if not lignes:
            return
        ncols = max(len(tr.find_all(["td", "th"], recursive=False)) for tr in lignes)
        table = cont.add_table(rows=len(lignes), cols=ncols)
        self._preparer_table(table, SANS_BORDURE, marges=(0, 0, 0, 160))
        for i, tr in enumerate(lignes):
            for j, td in enumerate(tr.find_all(["td", "th"], recursive=False)):
                cell = table.cell(i, j)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                self._remplir(cell, td)
        self._espace(cont)

    # ---- composants
    def callout(self, cont, el: Tag) -> None:
        classes = _classes(el)
        bord, fond = next((CALLOUT_VARIANTES[v] for v in CALLOUT_VARIANTES if v in classes),
                          (self.t.accent, self.t.callout))
        table = cont.add_table(rows=1, cols=1)
        self._preparer_table(table, SANS_BORDURE, marges=(100, 160, 100, 160))
        cell = table.cell(0, 0)
        self._style_cellule(
            cell,
            fond=fond,
            bordures={"top": None, "left": (24, bord), "bottom": None, "right": None},
        )
        self._remplir(cell, el)
        paragraphes = cell.paragraphs
        for k, p in enumerate(paragraphes):
            p.paragraph_format.space_after = Pt(0 if k == len(paragraphes) - 1 else 4)
        self._espace(cont)

    def couverture(self, cont, el: Tag) -> None:
        table = cont.add_table(rows=1, cols=1)
        self._preparer_table(table, SANS_BORDURE, marges=(0, 0, 0, 0))
        ligne = table.rows[0]
        ligne.height = Mm(self.hauteur_couverture_mm)
        ligne.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        cell = table.cell(0, 0)
        self._style_cellule(cell, fond=self.t.primary, marges=(0, 800, 0, 800))
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
        blanc = {"couleur": BLANC}

        label = el.find(class_="cover-label")
        titre = el.find(class_="cover-title") or el.find("h1")
        sous_titre = el.find(class_="cover-subtitle")
        meta = el.find(class_="cover-meta")
        connus = [x for x in (label, titre, sous_titre, meta) if x is not None]

        premier = cell.paragraphs[0]
        premier.paragraph_format.space_before = Mm(self.hauteur_couverture_mm * 0.22)
        premier.paragraph_format.space_after = Pt(10)
        if label is not None:
            run = premier.add_run(label.get_text(" ", strip=True).upper())
            run.bold = True
            run.font.size = Pt(9)
            run.font.color.rgb = _rgb(BLANC)
        if titre is not None:
            p = cell.add_paragraph()
            p.paragraph_format.space_after = Pt(12)
            p.paragraph_format.line_spacing = 1.0
            self.inline(p, titre.contents, {**blanc, "bold": True, "taille": 32})
        if sous_titre is not None:
            p = cell.add_paragraph()
            self.inline(p, sous_titre.contents, {**blanc, "taille": 14})
        for autre in el.find_all(recursive=False):
            if autre not in connus:
                p = cell.add_paragraph()
                self.inline(p, autre.contents, dict(blanc))
        if meta is not None:
            p = cell.add_paragraph()
            p.paragraph_format.space_before = Mm(self.hauteur_couverture_mm * 0.30)
            _bordure_paragraphe(p._p.get_or_add_pPr(), "top", BLANC, 4, 6)
            self.inline(p, meta.contents, {**blanc, "taille": 10})

    def sommaire(self, cont, el: Tag) -> None:
        titre = el.find(["h1", "h2", "h3"])
        p = cont.add_paragraph()
        p.paragraph_format.space_after = Pt(10)
        run = p.add_run(titre.get_text(" ", strip=True) if titre else "Sommaire")
        run.bold = True
        run.font.size = Pt(16)
        run.font.color.rgb = _rgb(self.t.primary)
        p2 = cont.add_paragraph()
        texte = _champ(p2, 'TOC \\o "1-2" \\h \\z \\u',
                       "Clic droit puis « Mettre à jour les champs » pour afficher le sommaire.")
        texte.italic = True
        texte.font.color.rgb = _rgb(MUTED)
        self.a_sommaire = True

    def signature(self, cont, el: Tag) -> None:
        avant = len(cont.paragraphs)
        self.blocs(cont, el.contents)
        nouveaux = cont.paragraphs[avant:]
        for k, p in enumerate(nouveaux):
            p.paragraph_format.left_indent = Mm(self.largeur_utile_mm * 0.55)
            if k == 0:
                p.paragraph_format.space_before = Pt(28)

    def figure(self, cont, el: Tag) -> None:
        for enfant in el.children:
            if not isinstance(enfant, Tag):
                continue
            if enfant.name == "img":
                self.image(cont, enfant)
            elif enfant.name == "figcaption":
                self._paragraphe(cont, enfant.contents, style="Legende")
            else:
                self.bloc(cont, enfant)

    # ---- images
    def _source_image(self, src: str):
        if not src:
            return None
        if src.startswith("data:image"):
            try:
                return base64.b64decode(src.split(",", 1)[1])
            except (IndexError, ValueError):
                return None
        if re.match(r"^https?://", src):
            return None
        chemin = Path(re.sub(r"^file:/*", "", src)) if src.startswith("file:") else Path(src)
        if not chemin.is_absolute():
            chemin = self.base_dir / chemin
        return chemin.read_bytes() if chemin.is_file() else None

    def _mesure_mm(self, el: Tag, donnees: bytes) -> tuple[float, float]:
        """Taille d'une image en millimètres, bornée à la page (ratio conservé).

        La **taille déclarée** par la balise (pixels logiques, convention CSS
        96 dpi) prime ; à défaut, la taille réelle du fichier à son dpi —
        comportement d'origine pour les images déposées par l'agent.

        La hauteur est bornée comme la largeur : un schéma vertical plus haut que
        la zone de texte n'a pas de mise en page possible dans Word.
        """
        with Image.open(io.BytesIO(donnees)) as image:
            largeur_px, hauteur_px = image.width, image.height
            dpi = float((image.info.get("dpi") or (96, 96))[0]) or 96.0
        declaree = _taille_declaree(el)
        if declaree is not None:
            largeur_mm = declaree[0] / 96 * 25.4
            ratio = declaree[1] / declaree[0]
        else:
            largeur_mm = largeur_px / dpi * 25.4
            ratio = hauteur_px / largeur_px
        hauteur_mm = largeur_mm * ratio
        facteur = min(
            1.0,
            self.largeur_utile_mm / largeur_mm,
            self.hauteur_utile_mm / hauteur_mm,
        )
        return largeur_mm * facteur, hauteur_mm * facteur

    def _image_dans(self, p, el: Tag) -> bool:
        donnees = self._source_image(str(el.get("src", "")))
        if donnees is None:
            run = p.add_run(f"Image non intégrée : {el.get('alt') or el.get('src', '')}")
            run.italic = True
            run.font.color.rgb = _rgb(MUTED)
            return False
        try:
            largeur_mm, hauteur_mm = self._mesure_mm(el, donnees)
            forme = p.add_run().add_picture(
                io.BytesIO(donnees), width=Mm(largeur_mm), height=Mm(hauteur_mm)
            )
            libelle = str(el.get("alt") or "").strip()
            if libelle:
                # Le texte alternatif du HTML devient la **description de l'image
                # Word** : c'est ce qu'annonce un lecteur d'écran sur un DOCX.
                forme._inline.docPr.set("descr", libelle)
            return True
        except Exception:  # noqa: BLE001 - format non supporté (SVG...)
            run = p.add_run(f"Image non intégrée (format non supporté) : {el.get('alt', '')}")
            run.italic = True
            run.font.color.rgb = _rgb(MUTED)
            return False

    def image(self, cont, el: Tag) -> None:
        p = cont.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._image_dans(p, el)


# --------------------------------------------------------------------------- point d'entrée
def rendre_docx(
    document: DocumentPrepare,
    chemin: str | Path,
    *,
    theme: str | Theme = "corporate",
    titre: str = "",
    auteur: str = "",
    orientation: str = "portrait",
    base_dir: str | Path | None = None,
) -> Path:
    """Génère le fichier DOCX et retourne son chemin."""
    t = get_theme(theme)
    paysage = orientation == "paysage"
    doc = Document()
    for p in list(doc.paragraphs):  # le modèle par défaut peut contenir un paragraphe vide
        if not p.text:
            p._p.getparent().remove(p._p)

    _configurer_styles(doc, t)
    _configurer_page(doc, titre, paysage, bool(document.couverture))
    rendu = _Rendu(
        doc,
        t,
        Path(base_dir or "."),
        largeur_utile_mm=261 if paysage else 174,
        hauteur_couverture_mm=140 if paysage else 225,
        # Zone de texte : A4 moins les marges de page du thème.
        hauteur_utile_mm=184 if paysage else 257,
    )

    if document.couverture:
        section = BeautifulSoup(document.couverture, "html.parser").find("section")
        if section is not None:
            rendu.couverture(doc, section)
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    rendu.blocs(doc, BeautifulSoup(document.corps, "html.parser").contents)
    if not doc.paragraphs or doc.element.body[-2].tag == qn("w:tbl"):
        doc.add_paragraph()
    if rendu.a_sommaire:
        _activer_maj_champs(doc)
    _metadonnees(doc, titre, auteur)

    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(chemin))
    return chemin
