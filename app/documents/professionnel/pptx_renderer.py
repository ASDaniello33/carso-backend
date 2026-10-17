"""Rendu PowerPoint natif (``python-pptx``) — 16:9, thème CARSO (incrément 71).

Le deck est construit à partir du modèle ``presentation`` : la mise en page, les
espacements et les tailles de police sont **décidés ici**, jamais par l'agent.
Ce qui ne tient pas est refusé avec un message actionnable (« scinde la
diapositive ») plutôt que rogné en silence — c'est la promesse du point 5.

Choix assumés :

- les graphiques et illustrations sont des **images PNG** (rasterisées par
  resvg) : rendu identique au PDF, mais pas de graphique PowerPoint éditable ;
- les SVG de l'agent ont déjà été convertis en PNG par ``html_builder`` avant
  d'arriver ici — Word et PowerPoint ne lisent pas le SVG ;
- aucun fichier n'est écrit hors du chemin demandé, aucune ressource distante
  n'est téléchargée.
"""

from __future__ import annotations

import base64
import io
import math
import re
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Emu, Inches, Pt

from app.core.errors import ValidationError

from .presentation import Bloc, Diapositive, Figure, Tableau
from .themes import BORDURE, CALLOUT_VARIANTES, MUTED, TEXTE, Theme, get_theme

__all__ = ["HAUTEUR_IN", "LARGEUR_IN", "rendre_pptx"]

#: Format 16:9 standard PowerPoint (13,333 × 7,5 pouces).
LARGEUR_IN = 13.333
HAUTEUR_IN = 7.5

MARGE_IN = 0.75
#: Corps : du haut du titre (0,55) au pied (6,95).
CORPS_HAUT_IN = 2.12
CORPS_BAS_IN = 6.72
CORPS_HAUTEUR_IN = CORPS_BAS_IN - CORPS_HAUT_IN
LARGEUR_UTILE_IN = LARGEUR_IN - 2 * MARGE_IN

POLICE = "Calibri"
BLANC = "#FFFFFF"
#: Tailles candidates, de la plus confortable à la plus dense.
TAILLES_CORPS = (16.0, 14.0, 12.5, 11.0)

#: Géométrie du colonnage quand une figure occupe la moitié droite.
LARGEUR_TEXTE_AVEC_FIGURE_IN = 5.05
ECART_COLONNES_IN = 0.45

#: Une figure « bandeau » (frise, cycle, organigramme horizontal : hauteur/
#: largeur en dessous de ce seuil) ne s'écrase pas dans une demi-colonne :
#: elle passe PLEINE LARGEUR, empilée sous le texte — comme dans un document.
RATIO_BANDEAU = 0.45

#: Réserve de hauteur du texte quand une figure bandeau est empilée dessous.
HAUTEUR_TEXTE_EMPILE_IN = 2.0


# --------------------------------------------------------------------------- couleurs/forme
def _rgb(couleur: str) -> RGBColor:
    return RGBColor.from_string(couleur.lstrip("#").upper())


def _rectangle(slide, x: float, y: float, largeur: float, hauteur: float, fond: str, *,
               contour: str | None = None, arrondi: bool = False):
    forme = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if arrondi else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(largeur), Inches(hauteur),
    )
    forme.fill.solid()
    forme.fill.fore_color.rgb = _rgb(fond)
    if contour is None:
        forme.line.fill.background()
    else:
        forme.line.color.rgb = _rgb(contour)
        forme.line.width = Pt(0.75)
    forme.shadow.inherit = False
    if arrondi:
        try:
            forme.adjustments[0] = 0.08
        except (IndexError, ValueError):
            pass
    return forme


def _cadre_texte(
    slide,
    x: float,
    y: float,
    largeur: float,
    hauteur: float,
    *,
    ancre=MSO_ANCHOR.TOP,
):
    zone = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(largeur), Inches(hauteur))
    cadre = zone.text_frame
    cadre.word_wrap = True
    cadre.margin_left = 0
    cadre.margin_right = 0
    cadre.margin_top = 0
    cadre.margin_bottom = 0
    cadre.vertical_anchor = ancre
    return cadre


def _ecrire(
    paragraphe,
    texte: str,
    *,
    taille: float,
    couleur: str = TEXTE,
    gras: bool = False,
    italique: bool = False,
    alignement=PP_ALIGN.LEFT,
) -> None:
    paragraphe.alignment = alignement
    run = paragraphe.add_run()
    run.text = texte
    run.font.name = POLICE
    run.font.size = Pt(taille)
    run.font.bold = gras
    run.font.italic = italique
    run.font.color.rgb = _rgb(couleur)


def _puce(paragraphe, *, retrait_in: float = 0.26) -> None:
    """Puce PowerPoint réelle (a:buChar), pas un caractère dans le texte."""
    ppr = paragraphe._p.get_or_add_pPr()
    ppr.set("marL", str(int(Inches(retrait_in))))
    ppr.set("indent", str(-int(Inches(retrait_in))))
    police = OxmlElement("a:buFont")
    police.set("typeface", "Arial")
    caractere = OxmlElement("a:buChar")
    caractere.set("char", "•")
    ppr.append(police)
    ppr.append(caractere)


# --------------------------------------------------------------------------- texte
def _blocs_texte(blocs: tuple[Bloc, ...]) -> list[Bloc]:
    return [bloc for bloc in blocs if bloc.genre in {"sous_titre", "paragraphe", "puces"}]


def _encadres(blocs: tuple[Bloc, ...]) -> list[Bloc]:
    return [bloc for bloc in blocs if bloc.genre == "encadre"]


def _tableaux(blocs: tuple[Bloc, ...]) -> list[Bloc]:
    return [bloc for bloc in blocs if bloc.genre == "tableau" and bloc.tableau is not None]


def _kpis(blocs: tuple[Bloc, ...]) -> list[Bloc]:
    return [bloc for bloc in blocs if bloc.genre == "kpi" and bloc.kpis]


def _estimation_lignes(blocs: list[Bloc], taille: float, largeur_in: float) -> int:
    """Estimation conservatrice du nombre de lignes (aucune police mesurée)."""
    caracteres_par_ligne = max(12, int((largeur_in * 72) / (taille * 0.52)))
    lignes = 0
    for bloc in blocs:
        if bloc.genre == "sous_titre":
            lignes += max(1, math.ceil(len(bloc.texte) / caracteres_par_ligne))
        elif bloc.genre == "paragraphe":
            lignes += max(1, math.ceil(len(bloc.texte) / caracteres_par_ligne))
        elif bloc.genre == "puces":
            for item in bloc.items:
                lignes += max(1, math.ceil(len(item) / caracteres_par_ligne))
    return lignes


def _taille_corps(
    blocs: list[Bloc],
    largeur_in: float,
    hauteur_in: float,
    titre_diapositive: str,
) -> float:
    """Choisit la plus grande taille qui tient, ou refuse la diapositive."""
    for taille in TAILLES_CORPS:
        lignes = _estimation_lignes(blocs, taille, largeur_in)
        capacite = int((hauteur_in * 72) / (taille * 1.38))
        if lignes + len(blocs) - 1 <= capacite:
            return taille
    raise ValidationError(
        f"Diapositive « {titre_diapositive or 'sans titre'} » trop chargée : le texte "
        "déborderait même à 11 pt. Réduis le nombre de puces ou scinde la "
        "diapositive en deux.",
        details={"taille_minimum": TAILLES_CORPS[-1]},
    )


# --------------------------------------------------------------------------- images
def _image_bytes(figure: Figure, base_dir: Path) -> bytes:
    src = figure.src
    if not src:
        raise ValidationError("Figure sans image : l'emplacement est vide.")
    if src.startswith("data:image"):
        entete, separateur, donnees = src.partition(",")
        if not separateur or "base64" not in entete:
            raise ValidationError("Image data URI invalide (base64 attendu).")
        try:
            return base64.b64decode(donnees)
        except (ValueError, TypeError) as erreur:
            raise ValidationError("Image data URI illisible (base64 invalide).") from erreur
    if re.match(r"^https?://", src):
        raise ValidationError(
            "Image distante non intégrée dans un PPTX : utilise un fichier local "
            "ou une image data:.",
            details={"src": src[:200]},
        )
    chemin = Path(re.sub(r"^file:/*", "", src)) if src.startswith("file:") else Path(src)
    if not chemin.is_absolute():
        chemin = base_dir / chemin
    if not chemin.is_file():
        raise ValidationError(
            f"Image introuvable pour la diapositive : {figure.libelle or src[:80]}",
            details={"conseil": "Dépose l'image ou retire la figure."},
        )
    return chemin.read_bytes()


def _ratio_figure(figure: Figure, base_dir: Path) -> float:
    """Ratio hauteur/largeur de l'image (lit les pixels réels, jamais l'attribut)."""
    donnees = _image_bytes(figure, base_dir)
    try:
        with Image.open(io.BytesIO(donnees)) as image:
            largeur_px, hauteur_px = image.width, image.height
    except Exception as erreur:  # noqa: BLE001 - le format réel est nommé
        raise ValidationError(
            f"Image illisible ({type(erreur).__name__}) : {figure.libelle or 'figure'}"
        ) from erreur
    if largeur_px <= 0 or hauteur_px <= 0:
        raise ValidationError("Image de dimensions nulles.")
    return hauteur_px / largeur_px


def _image(slide, figure: Figure, base_dir: Path, x: float, y: float,
           largeur_max: float, hauteur_max: float, legende: bool,
           *, ratio: float | None = None) -> None:
    donnees = _image_bytes(figure, base_dir)
    if ratio is None:
        ratio = _ratio_figure(figure, base_dir)
    largeur = largeur_max
    hauteur = largeur * ratio
    if hauteur > hauteur_max:
        hauteur = hauteur_max
        largeur = hauteur / ratio
    gauche = x + (largeur_max - largeur) / 2
    haut = y + (hauteur_max - hauteur) / 2
    forme = slide.shapes.add_picture(
        io.BytesIO(donnees), Inches(gauche), Inches(haut), Inches(largeur), Inches(hauteur)
    )
    libelle = figure.libelle.strip()
    if libelle:
        # Le texte alternatif devient la description lue par PowerPoint.
        forme._element.nvPicPr.cNvPr.set("descr", libelle[:250])
    if legende and libelle:
        cadre = _cadre_texte(slide, x, haut + hauteur + 0.06, largeur_max, 0.35,
                             ancre=MSO_ANCHOR.TOP)
        _ecrire(cadre.paragraphs[0], libelle, taille=9, couleur=MUTED,
                italique=True, alignement=PP_ALIGN.CENTER)


# --------------------------------------------------------------------------- blocs
def _texte_colonne(
    slide,
    blocs: list[Bloc],
    x: float,
    y: float,
    largeur: float,
    hauteur: float,
    *,
    taille: float,
    accent: str,
    couleur_texte: str = TEXTE,
) -> None:
    cadre = _cadre_texte(slide, x, y, largeur, hauteur)
    premier = True
    for bloc in blocs:
        if bloc.genre == "sous_titre":
            paragraphe = cadre.paragraphs[0] if premier else cadre.add_paragraph()
            _ecrire(paragraphe, bloc.texte, taille=taille - 1.5, couleur=accent, gras=True)
            paragraphe.space_before = Pt(6)
            paragraphe.space_after = Pt(2)
            premier = False
        elif bloc.genre == "paragraphe":
            paragraphe = cadre.paragraphs[0] if premier else cadre.add_paragraph()
            _ecrire(paragraphe, bloc.texte, taille=taille, couleur=couleur_texte)
            paragraphe.space_after = Pt(7)
            premier = False
        elif bloc.genre == "puces":
            for item in bloc.items:
                paragraphe = cadre.paragraphs[0] if premier else cadre.add_paragraph()
                _ecrire(paragraphe, item, taille=taille, couleur=couleur_texte)
                _puce(paragraphe)
                paragraphe.space_after = Pt(6)
                paragraphe.line_spacing = 1.05
                premier = False



def _tableau(slide, tableau: Tableau, t: Theme, x: float, y: float,
             largeur: float, hauteur_max: float) -> None:
    lignes = 1 + len(tableau.lignes)
    colonnes = len(tableau.entetes)
    if colonnes == 0:
        return
    hauteur = min(hauteur_max, max(0.4, 0.33 * lignes))
    if 0.33 * lignes > hauteur_max:
        raise ValidationError(
            f"Tableau de {len(tableau.lignes)} lignes trop haut pour une diapositive : "
            "réduis-le ou scinde-le.",
            details={"lignes": lignes, "hauteur_max_in": hauteur_max},
        )
    forme = slide.shapes.add_table(
        lignes, colonnes, Inches(x), Inches(y), Inches(largeur), Inches(hauteur)
    )
    table = forme.table
    # Largeurs de colonnes égales, sauf la première (souvent un libellé).
    for index in range(colonnes):
        table.columns[index].width = Inches(largeur / colonnes)
    taille = 10.5 if lignes <= 7 else 9.5
    for index, entete in enumerate(tableau.entetes):
        cellule = table.cell(0, index)
        cellule.fill.solid()
        cellule.fill.fore_color.rgb = _rgb(t.primary)
        cellule.vertical_anchor = MSO_ANCHOR.MIDDLE
        cadre = cellule.text_frame
        cadre.margin_left = Inches(0.08)
        cadre.margin_right = Inches(0.08)
        paragraphe = cadre.paragraphs[0]
        _ecrire(paragraphe, entete, taille=taille, couleur=BLANC, gras=True,
                alignement=PP_ALIGN.RIGHT if index in tableau.colonnes_num else PP_ALIGN.LEFT)
    for rang, ligne in enumerate(tableau.lignes, start=1):
        for index, valeur in enumerate(ligne):
            cellule = table.cell(rang, index)
            cellule.fill.solid()
            cellule.fill.fore_color.rgb = _rgb(t.soft if rang % 2 == 0 else BLANC)
            cellule.vertical_anchor = MSO_ANCHOR.MIDDLE
            cadre = cellule.text_frame
            cadre.margin_left = Inches(0.08)
            cadre.margin_right = Inches(0.08)
            _ecrire(
                cadre.paragraphs[0],
                valeur,
                taille=taille,
                couleur=TEXTE,
                alignement=PP_ALIGN.RIGHT if index in tableau.colonnes_num else PP_ALIGN.LEFT,
            )


def _kpi(slide, kpis: tuple[tuple[str, str], ...], t: Theme, x: float, y: float,
         largeur: float) -> None:
    nombre = len(kpis)
    if nombre == 0:
        return
    ecart = 0.16
    largeur_carte = (largeur - ecart * (nombre - 1)) / nombre
    hauteur = 1.12
    for index, (valeur, label) in enumerate(kpis):
        gauche = x + index * (largeur_carte + ecart)
        _rectangle(slide, gauche, y, largeur_carte, hauteur, t.soft)
        _rectangle(slide, gauche, y, largeur_carte, 0.035, t.accent)
        cadre = _cadre_texte(slide, gauche + 0.12, y + 0.2, largeur_carte - 0.24, hauteur - 0.28)
        paragraphe = cadre.paragraphs[0]
        _ecrire(paragraphe, valeur, taille=24, couleur=t.primary, gras=True,
                alignement=PP_ALIGN.CENTER)
        paragraphe.space_after = Pt(2)
        if label:
            _ecrire(cadre.add_paragraph(), label.upper(), taille=8.5, couleur=MUTED,
                    alignement=PP_ALIGN.CENTER)


def _encadre(slide, texte: str, variante: str, t: Theme, x: float, y: float,
             largeur: float, hauteur: float) -> None:
    bord, fond = CALLOUT_VARIANTES.get(variante, (t.accent, t.callout))
    _rectangle(slide, x, y, largeur, hauteur, fond)
    _rectangle(slide, x, y, 0.055, hauteur, bord)
    cadre = _cadre_texte(slide, x + 0.2, y + 0.1, largeur - 0.3, hauteur - 0.16,
                         ancre=MSO_ANCHOR.MIDDLE)
    _ecrire(cadre.paragraphs[0], texte, taille=12.5, couleur=TEXTE)


# --------------------------------------------------------------------------- diapositives
def _titre(slide, diapositive: Diapositive, t: Theme, document_titre: str) -> None:
    _rectangle(slide, 0, 0, LARGEUR_IN, HAUTEUR_IN, t.primary)
    _rectangle(slide, MARGE_IN, 1.25, 1.6, 0.07, t.accent)
    y = 1.55
    if diapositive.surtitre:
        cadre = _cadre_texte(slide, MARGE_IN, y, 11.0, 0.4)
        _ecrire(cadre.paragraphs[0], diapositive.surtitre.upper(), taille=12,
                couleur=BLANC, gras=True)
        y += 0.45
    cadre = _cadre_texte(slide, MARGE_IN, y, 11.0, 2.2)
    titre = diapositive.titre or document_titre or "Présentation"
    _ecrire(cadre.paragraphs[0], titre, taille=40, couleur=BLANC, gras=True)
    if diapositive.sous_titre:
        cadre = _cadre_texte(slide, MARGE_IN, 4.35, 10.2, 1.0)
        _ecrire(cadre.paragraphs[0], diapositive.sous_titre, taille=18, couleur=BLANC)
    if diapositive.meta:
        _rectangle(slide, MARGE_IN, 6.35, 11.83, 0.01, "#FFFFFF")
        cadre = _cadre_texte(slide, MARGE_IN, 6.48, 11.83, 0.45)
        _ecrire(cadre.paragraphs[0], diapositive.meta, taille=11, couleur=BLANC)


def _contenu(slide, diapositive: Diapositive, t: Theme, total: int, index: int,
             document_titre: str, base_dir: Path) -> None:
    _rectangle(slide, 0, 0, LARGEUR_IN, 0.09, t.primary)
    cadre = _cadre_texte(slide, MARGE_IN, 0.55, LARGEUR_UTILE_IN, 0.8)
    if diapositive.titre:
        _ecrire(cadre.paragraphs[0], diapositive.titre, taille=28, couleur=t.primary, gras=True)
    _rectangle(slide, MARGE_IN, 1.4, 1.1, 0.04, t.accent)
    if diapositive.lead:
        cadre = _cadre_texte(slide, MARGE_IN, 1.55, LARGEUR_UTILE_IN, 0.5)
        _ecrire(cadre.paragraphs[0], diapositive.lead, taille=14, couleur=MUTED)

    figure = diapositive.figure
    texte = _blocs_texte(diapositive.blocs)
    encadres = _encadres(diapositive.blocs)
    tableaux = _tableaux(diapositive.blocs)
    kpis = _kpis(diapositive.blocs)

    # Placement de la figure : PLEINE LARGEUR quand elle est seule sur la
    # diapositive, ou quand c'est un bandeau large (frise, cycle, schéma
    # horizontal, ratio h/l < RATIO_BANDEAU) — une frise écrasée dans la
    # demi-colonne de droite est illisible, alors qu'un document DOCX/PDF la
    # laisse pleine largeur. Sinon (figure haute/carrée avec du texte) :
    # split 50/50, comme la mise en page « avec-figure » du PDF.
    ratio_figure = _ratio_figure(figure, base_dir) if figure is not None else None
    bandeau = ratio_figure is not None and ratio_figure < RATIO_BANDEAU
    pleine_largeur = figure is not None and (bandeau or not texte and not encadres)
    largeur_texte = (
        LARGEUR_TEXTE_AVEC_FIGURE_IN
        if figure is not None and not pleine_largeur
        else LARGEUR_UTILE_IN
    )

    # Un encadré occupe le bas de la colonne de texte ; le reste est au-dessus.
    hauteur_encadre = 1.05 if encadres else 0.0
    taille = None
    if texte:
        taille = _taille_corps(
            texte, largeur_texte, CORPS_HAUTEUR_IN - hauteur_encadre - 0.12,
            diapositive.titre,
        )
    if pleine_largeur and texte and taille is not None:
        # Texte sur la zone haute, figure bandeau empilée sur le reste : la
        # réserve de texte est la hauteur RÉELLEMENT estimée (une ligne de
        # contexte ne doit pas manger la moitié de la zone figure).
        lignes = _estimation_lignes(texte, taille, largeur_texte)
        hauteur_estimee = lignes * taille * 1.45 / 72 + 0.2
        hauteur_texte = min(
            hauteur_estimee,
            HAUTEUR_TEXTE_EMPILE_IN,
            CORPS_HAUTEUR_IN - hauteur_encadre - 0.12,
        )
    else:
        hauteur_texte = CORPS_HAUTEUR_IN - hauteur_encadre - (0.12 if encadres else 0)
    if texte and taille is not None:
        _texte_colonne(slide, texte, MARGE_IN, CORPS_HAUT_IN, largeur_texte,
                       hauteur_texte, taille=taille, accent=t.accent)
    if encadres:
        _encadre(
            slide,
            "\n".join(bloc.texte for bloc in encadres),
            encadres[0].variante,
            t,
            MARGE_IN,
            CORPS_BAS_IN - hauteur_encadre,
            largeur_texte,
            hauteur_encadre,
        )

    if figure is not None:
        if pleine_largeur:
            haut_figure = CORPS_HAUT_IN + (hauteur_texte + 0.15 if texte else 0.0)
            _image(
                slide,
                figure,
                base_dir,
                MARGE_IN,
                haut_figure,
                LARGEUR_UTILE_IN,
                CORPS_BAS_IN - haut_figure,
                legende=True,
                ratio=ratio_figure,
            )
        else:
            _image(
                slide,
                figure,
                base_dir,
                MARGE_IN + largeur_texte + ECART_COLONNES_IN,
                CORPS_HAUT_IN,
                LARGEUR_UTILE_IN - largeur_texte - ECART_COLONNES_IN,
                CORPS_HAUTEUR_IN,
                legende=True,
                ratio=ratio_figure,
            )
    elif tableaux and tableaux[0].tableau is not None:
        _tableau(slide, tableaux[0].tableau, t, MARGE_IN, CORPS_HAUT_IN + 2.55,
                 LARGEUR_UTILE_IN, CORPS_BAS_IN - CORPS_HAUT_IN - 2.6)
    elif kpis:
        _kpi(slide, kpis[0].kpis, t, MARGE_IN, CORPS_HAUT_IN + 2.35, LARGEUR_UTILE_IN)

    if diapositive.notes:
        slide.notes_slide.notes_text_frame.text = diapositive.notes

    _rectangle(slide, MARGE_IN, 6.95, LARGEUR_UTILE_IN, 0.01, BORDURE)
    cadre = _cadre_texte(slide, MARGE_IN, 7.03, LARGEUR_UTILE_IN - 1.6, 0.3)
    _ecrire(cadre.paragraphs[0], document_titre, taille=8.5, couleur=MUTED)
    cadre = _cadre_texte(slide, LARGEUR_IN - MARGE_IN - 1.6, 7.03, 1.6, 0.3)
    _ecrire(cadre.paragraphs[0], f"{index} / {total}", taille=8.5, couleur=MUTED,
            alignement=PP_ALIGN.RIGHT)


# --------------------------------------------------------------------------- point d'entrée
def rendre_pptx(
    diapositives: tuple[Diapositive, ...],
    chemin: str | Path,
    *,
    theme: str | Theme = "corporate",
    titre: str = "",
    auteur: str = "",
    base_dir: str | Path | None = None,
) -> Path:
    """Écrit le fichier PPTX et retourne son chemin.

    Raises:
        ValidationError: diapositive surchargée, tableau trop long, image
            introuvable ou distante — jamais une diapositive rognée en silence.
    """
    if not diapositives:
        raise ValidationError("Présentation vide : aucune diapositive à rendre.")
    t = get_theme(theme)
    presentation = Presentation()
    # Largeur canonique PowerPoint 16:9 : 12 192 000 EMU exactement
    # (13,333 in × 914400 arrondi donnerait un écran 1 EMU trop étroit).
    presentation.slide_width = Emu(12_192_000)
    presentation.slide_height = Inches(HAUTEUR_IN)

    dossier = Path(base_dir or ".")
    total = len(diapositives)
    for index, diapositive in enumerate(diapositives, start=1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if diapositive.est_titre:
            _titre(slide, diapositive, t, titre)
        else:
            _contenu(slide, diapositive, t, total, index, titre, dossier)

    proprietes = presentation.core_properties
    proprietes.title = titre or "Présentation"
    proprietes.author = auteur or ""
    proprietes.last_modified_by = auteur or ""

    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(chemin))
    return chemin
