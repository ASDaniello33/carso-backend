"""Renderer DOCX—``DocumentSpec`` → octets OOXML (ADR 0005, Phases 6-7).

Le renderer ne décide de **rien** sur le fond—il traduit une structure et des
tokens de style en un document Word professionnel. Tout ce que python-docx ne
sait pas exprimer est délégué à ``app.documents.docx_xml`` (couche isolée).

Pris en charge—page de couverture (titre, sous-titre, organisation, logo,
champs), titres 1→4, paragraphes (corps/chapeau/note), listes à puces et
numérotées imbriquées, tableaux (en-tête simple ou à étages, fusions
horizontales et verticales, trames par cellule, largeurs explicites, ligne de
total, en-tête répété en haut de page), images légendées, encadrés, citations,
blocs libellé/valeur, sauts de page, en-tête et pied de page distincts sur la
première page, numérotation de page.

Un tableau **sans en-tête déclaré** n'en reçoit aucun—le rendu n'invente
jamais de titre de colonne (une bande d'en-tête vide a déjà été produite par le
passé, et un tableau bancal est refusé par ``Tableau.grille``).

Règles tenues par ce module :

- **aucune valeur inventée**—un bloc de statut ``inconnu``/``manquant`` est
  rendu avec le marqueur ``(à compléter)``, jamais avec un blanc silencieux ;
- **aucune dépendance obligatoire**—python-docx est importé paresseusement via
  ``require_library``—une librairie absente produit une ``GenerationError``
  explicite, jamais un fichier vide.

Limites documentées—les listes utilisent les styles Word ``List Bullet`` /
``List Number`` (deux listes numérotées successives peuvent poursuivre la
numérotation) ; le nombre de pages d'un DOCX n'est pas calculable sans moteur de
rendu—c'est le PDF dérivé qui donne la pagination réelle.
"""

from __future__ import annotations

import io
from typing import Any, NamedTuple

from app.core.errors import CarsoError, GenerationError, ValidationError
from app.documents import docx_xml as xml
from app.documents.assets import FournisseurImages, resoudre_image
from app.documents.renderers.commun import (
    alignement_cellule,
    couleur_cellule,
    fond_cellule,
    gras_cellule,
)
from app.documents.spec import (
    MARQUEUR_A_COMPLETER,
    MARQUEUR_A_CONFIRMER,
    MARQUEUR_RE,
    Bloc,
    Citation,
    Couverture,
    DocumentSpec,
    Encadre,
    Espacement,
    Forme,
    Graphique,
    Illustration,
    Image,
    Liste,
    Paires,
    Paragraphe,
    SautDePage,
    Segment,
    Sommaire,
    Tableau,
    Titre,
)
from app.documents.styles import DocumentStyle, StyleTexte, couleur_rgb

__all__ = ["rendre_docx"]

_ALIGNEMENTS = {
    "gauche": "LEFT",
    "centre": "CENTER",
    "droite": "RIGHT",
    "justifie": "JUSTIFY",
}

#: Motif de surlignage (vocabulaire du spec → constante Word).
_SURLIGNAGES = {
    "jaune": "YELLOW",
    "vert": "BRIGHT_GREEN",
    "cyan": "TURQUOISE",
    "magenta": "PINK",
    "rouge": "RED",
    "bleu": "BLUE",
    "gris": "GRAY_25",
    "noir": "BLACK",
}

#: Épaisseurs de filets des tableaux (points), alignées sur le vocabulaire du spec.
_EPAISSEURS = {"aucune": 0.0, "fine": 0.5, "moyenne": 0.75, "epaisse": 1.5}


class _Libs(NamedTuple):
    """Points d'entrée python-docx utilisés par le renderer (imports groupés)."""

    Document: Any
    Mm: Any
    Pt: Any
    RGBColor: Any
    alignements: Any
    orientations: Any
    surlignages: Any


def _libs() -> _Libs:
    """Importe python-docx et ses unités (librairie optionnelle, extra ``documents``).

    Raises:
        GenerationError: python-docx absent.
    """
    try:
        from app.documents.extraction.base import require_library

        docx = require_library("docx")
        from docx.enum.section import WD_ORIENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
        from docx.shared import Mm, Pt, RGBColor
    except CarsoError as erreur:
        raise GenerationError(str(erreur)) from erreur
    return _Libs(
        Document=docx.Document,
        Mm=Mm,
        Pt=Pt,
        RGBColor=RGBColor,
        alignements=WD_ALIGN_PARAGRAPH,
        orientations=WD_ORIENT,
        surlignages=WD_COLOR_INDEX,
    )


def rendre_docx(spec: DocumentSpec, *, images: FournisseurImages | None = None) -> bytes:
    """Rend un ``DocumentSpec`` en octets DOCX.

    Args:
        spec: le document structuré à rendre.
        images: fournisseur d'images (``None`` si le document n'en contient pas).

    Raises:
        GenerationError: python-docx absent ou échec de construction.
        ValidationError: image référencée mais introuvable.
    """
    libs = _libs()
    style = spec.style_effectif()
    try:
        document = libs.Document()
        _configurer_styles_base(document, libs, style)
        section = document.sections[0]
        couverture = spec.couverture()
        _configurer_page(section, libs, spec, couverture is not None)
        _configurer_entete_pied(section, libs, spec, style)

        if couverture is not None:
            _rendre_couverture(document, libs, spec, style, couverture, images)
        for bloc in spec.blocs:
            _rendre_bloc(document, libs, bloc, spec, style, images)
        if any(isinstance(bloc, Sommaire) for bloc in spec.blocs):
            # Un sommaire n'a de contenu qu'une fois les champs recalculés.
            xml.maj_champs_a_ouverture(document)

        buffer = io.BytesIO()
        document.save(buffer)
    except (ValidationError, GenerationError):
        raise
    except Exception as erreur:  # pragma: no cover - défensif (bibliothèque tierce)
        raise GenerationError(f"Échec du rendu DOCX : {erreur}") from erreur
    return buffer.getvalue()


# --- Configuration du document -----------------------------------------------


def _configurer_styles_base(document: Any, libs: _Libs, style: DocumentStyle) -> None:
    """Aligne le style ``Normal`` sur les tokens du corps de texte."""
    normal = document.styles["Normal"]
    normal.font.name = style.corps.police
    normal.font.size = libs.Pt(style.corps.taille_pt)
    normal.font.color.rgb = libs.RGBColor(*couleur_rgb(style.corps.couleur))


def _configurer_page(
    section: Any, libs: _Libs, spec: DocumentSpec, avec_couverture: bool
) -> None:
    """Applique format, orientation, marges et règle de première page."""
    geometrie = spec.geometrie()
    section.page_width = libs.Mm(geometrie.largeur_mm)
    section.page_height = libs.Mm(geometrie.hauteur_mm)
    section.orientation = (
        libs.orientations.LANDSCAPE
        if geometrie.orientation == "paysage"
        else libs.orientations.PORTRAIT
    )
    section.left_margin = libs.Mm(geometrie.marges.gauche_mm)
    section.right_margin = libs.Mm(geometrie.marges.droite_mm)
    section.top_margin = libs.Mm(geometrie.marges.haut_mm)
    section.bottom_margin = libs.Mm(geometrie.marges.bas_mm)
    # Une couverture ne porte ni en-tête ni pied de page : elle reste sobre.
    section.different_first_page_header_footer = avec_couverture
    debut = spec.mise_en_page.numero_de_page_debut
    if debut is not None:
        xml.debut_numerotation(section, numero=debut)


def _configurer_entete_pied(
    section: Any, libs: _Libs, spec: DocumentSpec, style: DocumentStyle
) -> None:
    """Écrit l'en-tête et le pied de page (texte, filet, numéro de page)."""
    mention = spec.mise_en_page.mention_proposition
    texte_entete = spec.mise_en_page.entete_texte or style.entete.texte or mention or ""
    entete = section.header.paragraphs[0]
    entete.text = ""
    if texte_entete:
        run = entete.add_run(texte_entete)
        _styler_run(run, libs, style.entete.comme_style_texte())
    _aligner(entete, libs, style.entete.alignement)
    xml.espacement_paragraphe(entete, avant_pt=0.0, apres_pt=0.0)

    pied = section.footer.paragraphs[0]
    pied.text = ""
    texte_pied = spec.mise_en_page.pied_texte or style.pied.texte or ""
    if texte_pied:
        pied.add_run(texte_pied)
    numero = (
        spec.mise_en_page.numero_de_page
        if spec.mise_en_page.numero_de_page is not None
        else style.pied.numero_page
    )
    if numero:
        if texte_pied:
            pied.add_run("  —  ")
        xml.champ_numero_page(pied)
    for run in pied.runs:
        _styler_run(run, libs, style.pied.comme_style_texte())
    _aligner(pied, libs, style.pied.alignement)
    if style.pied.filet:
        xml.filet_haut_paragraphe(pied, couleur=style.pied.couleur, taille_pt=0.5)
    xml.espacement_paragraphe(pied, avant_pt=2.0, apres_pt=0.0)


def _rendre_couverture(
    document: Any,
    libs: _Libs,
    spec: DocumentSpec,
    style: DocumentStyle,
    couverture: Couverture,
    images: FournisseurImages | None,
) -> None:
    """Rend la page de couverture, puis un saut de page."""
    tokens = style.couverture
    document.add_paragraph()
    document.add_paragraph()
    if couverture.logo_document_id is not None or couverture.logo_chemin:
        _inserer_image(
            document,
            libs,
            Image(
                document_id=couverture.logo_document_id,
                chemin=couverture.logo_chemin,
                largeur_mm=tokens.logo_largeur_mm,
            ),
            images,
            centrer=True,
        )
    titre = couverture.titre or spec.metadata.titre
    _ajouter_paragraphe(
        document,
        libs,
        titre,
        tokens=StyleTexte(
            police=style.titre.police,
            taille_pt=tokens.titre_taille_pt,
            gras=True,
            couleur=tokens.couleur_accent,
            alignement=tokens.alignement,
            espace_apres_pt=6.0,
        ),
    )
    sous_titre = couverture.sous_titre or spec.metadata.sous_titre
    if sous_titre:
        _ajouter_paragraphe(
            document,
            libs,
            sous_titre,
            tokens=StyleTexte(
                police=style.corps.police,
                taille_pt=tokens.sous_titre_taille_pt,
                couleur=style.corps.couleur,
                alignement=tokens.alignement,
                espace_apres_pt=10.0,
            ),
        )
    if tokens.bandeau:
        _ajouter_paragraphe(
            document,
            libs,
            "—" * 24,
            tokens=StyleTexte(
                taille_pt=tokens.sous_titre_taille_pt,
                couleur=tokens.couleur_accent,
                alignement=tokens.alignement,
                espace_apres_pt=10.0,
            ),
        )
    organisation = couverture.organisation or spec.metadata.organisation
    if organisation:
        _ajouter_paragraphe(
            document,
            libs,
            organisation,
            tokens=StyleTexte(
                taille_pt=style.corps.taille_pt + 1,
                gras=True,
                alignement=tokens.alignement,
                espace_apres_pt=4.0,
            ),
        )
    for paire in couverture.champs:
        _ajouter_paragraphe(
            document,
            libs,
            f"{paire.libelle} : {paire.valeur}",
            tokens=StyleTexte(
                taille_pt=style.corps.taille_pt,
                couleur=style.corps.couleur,
                alignement=tokens.alignement,
                espace_apres_pt=2.0,
            ),
        )
    if couverture.date_ligne:
        _ajouter_paragraphe(
            document,
            libs,
            couverture.date_ligne,
            tokens=StyleTexte(
                taille_pt=style.corps.taille_pt,
                italique=True,
                alignement=tokens.alignement,
                espace_apres_pt=2.0,
            ),
        )
    if spec.mise_en_page.mention_proposition:
        _ajouter_paragraphe(
            document,
            libs,
            spec.mise_en_page.mention_proposition,
            tokens=StyleTexte(
                taille_pt=8.5,
                italique=True,
                couleur="#6B7280",
                alignement=tokens.alignement,
                espace_avant_pt=24.0,
                espace_apres_pt=0.0,
            ),
        )
    document.add_page_break()


# --- Rendu des blocs ---------------------------------------------------------


def _rendre_bloc(
    document: Any,
    libs: _Libs,
    bloc: Bloc,
    spec: DocumentSpec,
    style: DocumentStyle,
    images: FournisseurImages | None,
) -> None:
    """Traduit un bloc du spec en contenu DOCX."""
    if isinstance(bloc, Titre):
        paragraphe = document.add_paragraph(bloc.texte, style=f"Heading {bloc.niveau}")
        tokens = style.titre_niveau(bloc.niveau)
        _appliquer(paragraphe, libs, tokens)
        _ajouter_marqueur(
            paragraphe,
            libs,
            tokens,
            bloc.statut,
            deja_marque=MARQUEUR_RE.search(bloc.texte),
        )
        return

    if isinstance(bloc, Paragraphe):
        tokens = style.citation if bloc.role == "note" else style.corps
        if bloc.role == "chapeau":
            tokens = tokens.model_copy(
                update={
                    "taille_pt": tokens.taille_pt + 1.0,
                    "espace_apres_pt": tokens.espace_apres_pt + 4.0,
                }
            )
        tokens = _tokens_paragraphe(bloc, tokens)
        paragraphe = _ajouter_paragraphe(
            document, libs, bloc.texte, tokens=tokens, runs=bloc.runs
        )
        if bloc.retrait_mm is not None:
            paragraphe.paragraph_format.left_indent = libs.Mm(bloc.retrait_mm)
        if bloc.retrait_premiere_ligne_mm is not None:
            paragraphe.paragraph_format.first_line_indent = libs.Mm(
                bloc.retrait_premiere_ligne_mm
            )
        texte_marque = bloc.texte or "".join(segment.texte for segment in bloc.runs or [])
        _ajouter_marqueur(
            paragraphe,
            libs,
            tokens,
            bloc.statut,
            deja_marque=MARQUEUR_RE.search(texte_marque),
        )
        return

    if isinstance(bloc, Liste):
        _rendre_liste(document, libs, bloc, style)
        return

    if isinstance(bloc, Tableau):
        _rendre_tableau(document, libs, bloc, spec, style)
        return

    if isinstance(bloc, Image):
        if bloc.svg:
            # SVG écrit par l'agent (INC-123) : Word ne le lit pas, resvg le
            # convertit. L'assainissement est fait par ``figures``.
            _inserer_figure(
                document,
                libs,
                bloc.svg,
                bloc.legende,
                bloc.largeur_mm,
                centrer=True,
            )
            return
        _inserer_image(document, libs, bloc, images, centrer=True)
        return

    if isinstance(bloc, (Graphique, Illustration)):
        _inserer_figure(
            document,
            libs,
            bloc,
            bloc.legende,
            bloc.largeur_mm,
            centrer=True,
        )
        return

    if isinstance(bloc, Encadre):
        _rendre_encadre(document, libs, bloc, style)
        return

    if isinstance(bloc, Citation):
        paragraphe = _ajouter_paragraphe(document, libs, f"« {bloc.texte} »", tokens=style.citation)
        _ajouter_marqueur(paragraphe, libs, style.citation, bloc.statut, deja_marque=False)
        if bloc.attribution:
            _ajouter_paragraphe(
                document,
                libs,
                f"— {bloc.attribution}",
                tokens=style.citation.model_copy(
                    update={"alignement": "droite", "espace_apres_pt": 8.0}
                ),
            )
        return

    if isinstance(bloc, Paires):
        _rendre_paires(document, libs, bloc, style)
        return

    if isinstance(bloc, SautDePage):
        document.add_page_break()
        return

    if isinstance(bloc, Sommaire):
        _rendre_sommaire(document, libs, bloc, style)
        return

    if isinstance(bloc, Forme):
        from app.documents.figures import svg_forme

        largeur = bloc.largeur_mm or spec.geometrie().largeur_utile_mm()
        _inserer_figure(
            document,
            libs,
            svg_forme(bloc),
            None,
            largeur,
            centrer=bloc.largeur_mm is None,
        )
        return

    if isinstance(bloc, Espacement):
        # Blanc vertical exact : un paragraphe vide dont l'interligne vaut la
        # hauteur demandée (``line_spacing`` en points → ``w:lineRule="exact"``).
        paragraphe = document.add_paragraph()
        paragraphe.paragraph_format.space_before = libs.Pt(0)
        paragraphe.paragraph_format.space_after = libs.Pt(0)
        paragraphe.paragraph_format.line_spacing = libs.Pt(bloc.hauteur_mm * 72.0 / 25.4)
        return

    raise GenerationError(f"Type de bloc non rendable : {type(bloc).__name__}")


def _rendre_sommaire(
    document: Any, libs: _Libs, bloc: Sommaire, style: DocumentStyle
) -> None:
    """Sommaire : intitulé stylé puis champ Word ``TOC`` (paginé par le lecteur)."""
    if bloc.titre:
        titre = document.add_paragraph()
        titre.add_run(bloc.titre)
        _appliquer(titre, libs, style.titre_niveau(2))
    champ = document.add_paragraph()
    xml.champ_sommaire(
        champ, niveaux=bloc.niveaux, avec_pages=bloc.avec_numeros_de_page
    )


def _rendre_liste(document: Any, libs: _Libs, bloc: Liste, style: DocumentStyle) -> None:
    """Liste à puces ou numérotée, indentée selon sa profondeur."""
    modele = "List Number" if bloc.numerotee else "List Bullet"
    for item in bloc.items:
        paragraphe = document.add_paragraph(item, style=modele)
        _appliquer(paragraphe, libs, style.liste)
        if bloc.profondeur:
            paragraphe.paragraph_format.left_indent = libs.Mm(
                6.0 + 4.0 * bloc.profondeur
            )
        _ajouter_marqueur(
            paragraphe, libs, style.liste, bloc.statut, deja_marque=MARQUEUR_RE.search(item)
        )


def _rendre_tableau(
    document: Any, libs: _Libs, bloc: Tableau, spec: DocumentSpec, style: DocumentStyle
) -> None:
    """Tableau : en-tête stylé et répété, largeurs explicites, bandes alternées."""
    tokens = style.tableau
    if bloc.titre_tableau:
        _ajouter_paragraphe(
            document,
            libs,
            bloc.titre_tableau,
            tokens=style.corps.model_copy(
                update={"gras": True, "espace_avant_pt": 8.0, "espace_apres_pt": 3.0}
            ),
        )
    grille = bloc.grille()
    nb_colonnes = bloc.largeur_grille()
    nb_entete = bloc.nb_lignes_entete()

    table = document.add_table(rows=len(grille), cols=nb_colonnes)
    table.style = "Table Grid"

    # 1) Fusions d'abord : une cellule fusionnée ne contient alors qu'un seul
    #    paragraphe (sinon les paragraphes vides des cellules absorbées restent).
    for rangees in grille:
        for placee in rangees:
            if placee.colonnes == 1 and placee.lignes == 1:
                continue
            table.cell(placee.ligne, placee.colonne).merge(
                table.cell(placee.ligne + placee.lignes - 1, placee.colonne + placee.colonnes - 1)
            )

    # 2) Contenu et style, aux seules positions d'ancrage.
    bandes = (
        bloc.bandes_alternees
        if bloc.bandes_alternees is not None
        else tokens.bandes_alternees
    )
    for index_ligne, rangees in enumerate(grille):
        ligne_docx = table.rows[index_ligne]
        en_entete = index_ligne < nb_entete
        index_donnee = index_ligne - nb_entete
        total = bloc.total_ligne is not None and index_donnee == bloc.total_ligne
        alternance = bool(bandes and index_donnee % 2 == 1 and not total)
        if en_entete and tokens.repeter_entete and bloc.repeter_entete:
            xml.repetition_entete(ligne_docx)
        if bloc.hauteur_lignes_mm:
            xml.hauteur_ligne(ligne_docx, hauteur_mm=bloc.hauteur_lignes_mm)
        for placee in rangees:
            cellule = table.cell(placee.ligne, placee.colonne)
            cellule.text = ""
            paragraphe_cellule = cellule.paragraphs[0]
            tokens_cellule = StyleTexte(
                police=style.corps.police,
                taille_pt=placee.cellule.taille_pt or tokens.taille_pt,
                gras=gras_cellule(
                    placee.cellule, en_entete=en_entete, total=total, tokens=tokens
                ),
                italique=bool(placee.cellule.italique),
                couleur=couleur_cellule(
                    placee.cellule, en_entete=en_entete, style=style, tokens=tokens
                ),
            )
            if placee.cellule.runs:
                for segment in placee.cellule.runs:
                    _styler_run(
                        paragraphe_cellule.add_run(segment.texte),
                        libs,
                        tokens_cellule,
                        segment,
                    )
            else:
                _styler_run(
                    paragraphe_cellule.add_run(placee.cellule.texte),
                    libs,
                    tokens_cellule,
                )
            if placee.cellule.souligne:
                for run in paragraphe_cellule.runs:
                    run.font.underline = True
            fond = fond_cellule(
                placee.cellule,
                en_entete=en_entete,
                total=total,
                alternance=alternance,
                tokens=tokens,
            )
            if en_entete and bloc.couleur_entete and placee.cellule.fond is None:
                fond = bloc.couleur_entete
            if fond:
                xml.ombrer_cellule(cellule, fond)
            if bloc.marges_cellules_mm is not None or placee.cellule.marge_interne_mm:
                xml.marges_cellule(
                    cellule,
                    marge_mm=tokens_padding(bloc, placee),
                )
            if placee.cellule.alignement_vertical:
                xml.alignement_vertical_cellule(
                    cellule, _VERTICAUX[placee.cellule.alignement_vertical]
                )
            _aligner_cellule(cellule, libs, bloc, placee)

    xml.mise_en_page_fixe(table)
    xml.largeurs_colonnes(table, bloc.largeurs_mm or _largeurs_par_defaut(spec, nb_colonnes))
    if bloc.bordures == "aucune":
        xml.bordures_tableau(
            table,
            couleur=bloc.couleur_bordures or tokens.bordure_couleur,
            taille_pt=0.0,
            motif="nil",
        )
    elif bloc.bordures:
        xml.bordures_tableau(
            table,
            couleur=bloc.couleur_bordures or tokens.bordure_couleur,
            taille_pt=_EPAISSEURS[bloc.bordures],
        )
    elif tokens.bordures:
        xml.bordures_tableau(
            table,
            couleur=bloc.couleur_bordures or tokens.bordure_couleur,
            taille_pt=tokens.bordure_taille_pt,
        )
    document.add_paragraph()


def _rendre_encadre(document: Any, libs: _Libs, bloc: Encadre, style: DocumentStyle) -> None:
    """Encadré : tableau d'une cellule tramé et bordé (robuste Word/LibreOffice)."""
    tokens = style.encadre(bloc.genre)
    table = document.add_table(rows=1, cols=1)
    cellule = table.cell(0, 0)
    cellule.text = ""
    paragraphe = cellule.paragraphs[0]
    libelle = bloc.libelle_affiche(tokens.libelle)
    if libelle:
        run = paragraphe.add_run(f"{libelle} — ")
        _styler_run(
            run,
            libs,
            StyleTexte(
                police=style.corps.police,
                taille_pt=style.corps.taille_pt,
                gras=True,
                couleur=tokens.couleur_titre,
            ),
        )
    run = paragraphe.add_run(bloc.texte)
    _styler_run(
        run,
        libs,
        StyleTexte(
            police=style.corps.police,
            taille_pt=style.corps.taille_pt,
            couleur=tokens.couleur_texte,
        ),
    )
    xml.ombrer_cellule(cellule, tokens.fond)
    xml.bordures_tableau(table, couleur=tokens.bordure, taille_pt=0.75)
    document.add_paragraph()


def _rendre_paires(document: Any, libs: _Libs, bloc: Paires, style: DocumentStyle) -> None:
    """Bloc libellé/valeur : tableau sans filets, libellés en gras."""
    nb_colonnes = bloc.colonnes * 2
    table = document.add_table(rows=0, cols=nb_colonnes)
    paires = list(bloc.paires)
    for debut in range(0, len(paires), bloc.colonnes):
        ligne = table.add_row()
        for index in range(bloc.colonnes):
            position = debut + index
            cellule_libelle = ligne.cells[index * 2]
            cellule_valeur = ligne.cells[index * 2 + 1]
            cellule_libelle.text = ""
            cellule_valeur.text = ""
            if position >= len(paires):
                continue
            paire = paires[position]
            run = cellule_libelle.paragraphs[0].add_run(f"{paire.libelle} :")
            _styler_run(
                run,
                libs,
                StyleTexte(
                    police=style.corps.police,
                    taille_pt=style.tableau.taille_pt,
                    gras=True,
                    couleur=style.corps.couleur,
                ),
            )
            run = cellule_valeur.paragraphs[0].add_run(paire.valeur)
            _styler_run(
                run,
                libs,
                StyleTexte(
                    police=style.corps.police,
                    taille_pt=style.tableau.taille_pt,
                    couleur=style.corps.couleur,
                ),
            )
    document.add_paragraph()


# --- Blocs simples ----------------------------------------------------------


def _inserer_image(
    document: Any,
    libs: _Libs,
    bloc: Image,
    images: FournisseurImages | None,
    *,
    centrer: bool,
) -> None:
    """Insère une image (ou lève une erreur explicite si elle est introuvable)."""
    contenu = resoudre_image(bloc, images)
    if contenu is None:
        raise ValidationError(
            "Image référencée sans fournisseur d'images",
            details={"document_id": str(bloc.document_id or ""), "chemin": bloc.chemin},
        )
    octets, nom = contenu
    _inserer_octets_image(
        document, libs, octets, nom, bloc.largeur_mm, bloc.legende, centrer=centrer
    )


def _inserer_figure(
    document: Any,
    libs: _Libs,
    figure: Graphique | Illustration | str,
    legende: str | None,
    largeur_mm: float,
    *,
    centrer: bool,
) -> None:
    """Insère une figure SVG (graphique, illustration ou schéma) dans le DOCX.

    Word ne lit pas le SVG : la figure est rasterisée par ``resvg-py`` (aucun
    navigateur requis). Une rasterisation impossible lève ``GenerationError``
    avec la consigne d'installation — jamais un document privé de sa figure.
    """
    from app.documents.figures import png_depuis_svg, svg_depuis_bloc

    svg = figure if isinstance(figure, str) else svg_depuis_bloc(figure)
    octets, _largeur_px, _hauteur_px = png_depuis_svg(svg)
    _inserer_octets_image(
        document, libs, octets, "figure.png", largeur_mm, legende, centrer=centrer
    )


def _inserer_octets_image(
    document: Any,
    libs: _Libs,
    octets: bytes,
    nom: str,
    largeur_mm: float,
    legende: str | None,
    *,
    centrer: bool,
) -> None:
    """Pose une image centrée et sa légende (chemin commun image/figure)."""
    try:
        paragraphe = document.add_paragraph()
        paragraphe.add_run().add_picture(io.BytesIO(octets), width=libs.Mm(largeur_mm))
    except Exception as erreur:
        raise ValidationError(f"Image illisible : {nom}") from erreur
    if centrer:
        _aligner(paragraphe, libs, "centre")
    if legende:
        _ajouter_paragraphe(
            document,
            libs,
            legende,
            tokens=StyleTexte(
                taille_pt=8.5,
                italique=True,
                couleur="#6B7280",
                alignement="centre",
                espace_apres_pt=8.0,
            ),
        )


def _ajouter_paragraphe(
    document: Any,
    libs: _Libs,
    texte: str,
    tokens: StyleTexte | None = None,
    runs: list[Segment] | None = None,
) -> Any:
    """Ajoute un paragraphe stylé (texte simple ou fragments stylés)."""
    paragraphe = document.add_paragraph()
    _appliquer_paragraphe(paragraphe, libs, tokens)
    if runs:
        for segment in runs:
            run = paragraphe.add_run(segment.texte)
            if tokens is not None:
                _styler_run(run, libs, tokens, segment)
        return paragraphe
    run = paragraphe.add_run(texte)
    if tokens is not None:
        _styler_run(run, libs, tokens)
    return paragraphe


def _tokens_paragraphe(bloc: Paragraphe, tokens: StyleTexte) -> StyleTexte:
    """Applique les surcharges typographiques du bloc sur son rôle."""
    changements = {
        champ: getattr(bloc, champ)
        for champ in (
            "alignement",
            "police",
            "taille_pt",
            "couleur",
            "interligne",
            "espace_avant_pt",
            "espace_apres_pt",
        )
        if getattr(bloc, champ) is not None
    }
    return tokens.model_copy(update=changements) if changements else tokens


def tokens_padding(bloc: Tableau, placee: Any) -> float:
    """Padding effectif d'une cellule : le sien, sinon celui du tableau."""
    if placee.cellule.marge_interne_mm is not None:
        return placee.cellule.marge_interne_mm
    return bloc.marges_cellules_mm or 0.0


def _ajouter_marqueur(
    paragraphe: Any,
    libs: _Libs,
    tokens: StyleTexte | None,
    statut: str,
    *,
    deja_marque: object,
) -> None:
    """Ajoute le marqueur visible d'une donnée absente (``(à compléter)``)."""
    if deja_marque:
        return
    marqueur = _marqueur_du_statut(statut)
    if not marqueur:
        return
    run = paragraphe.add_run(f" {marqueur}")
    if tokens is not None:
        _styler_run(run, libs, tokens)


def _marqueur_du_statut(statut: str) -> str:
    """Marqueur affiché pour un statut de renseignement non satisfait."""
    if statut in ("inconnu", "manquant"):
        return MARQUEUR_A_COMPLETER
    if statut == "a_confirmer":
        return MARQUEUR_A_CONFIRMER
    return ""


def _appliquer(
    paragraphe: Any, libs: _Libs, tokens: StyleTexte | None, run: Any | None = None
) -> None:
    """Applique un rôle typographique (alignement, espacement, police)."""
    if tokens is None:
        return
    _appliquer_paragraphe(paragraphe, libs, tokens)
    cible = run if run is not None else (paragraphe.runs[0] if paragraphe.runs else None)
    if cible is not None:
        _styler_run(cible, libs, tokens)


def _appliquer_paragraphe(
    paragraphe: Any, libs: _Libs, tokens: StyleTexte | None
) -> None:
    """Applique alignement et espacements d'un paragraphe (sans toucher aux runs)."""
    if tokens is None:
        return
    _aligner(paragraphe, libs, tokens.alignement)
    xml.espacement_paragraphe(
        paragraphe,
        avant_pt=tokens.espace_avant_pt,
        apres_pt=tokens.espace_apres_pt,
        interligne=tokens.interligne,
    )


def _styler_run(
    run: Any, libs: _Libs, tokens: StyleTexte, segment: Segment | None = None
) -> None:
    """Applique le rôle typographique, puis les surcharges d'un fragment.

    Un fragment ne modifie que ce qu'il déclare : un mot en gras dans un
    paragraphe normal reste dans la police et la couleur du paragraphe.
    """
    police = (segment.police if segment and segment.police else tokens.police)
    taille = segment.taille_pt if segment and segment.taille_pt else tokens.taille_pt
    gras = tokens.gras if segment is None or segment.gras is None else segment.gras
    italique = (
        tokens.italique if segment is None or segment.italique is None else segment.italique
    )
    couleur = (segment.couleur if segment and segment.couleur else tokens.couleur)
    run.font.name = police
    run.font.size = libs.Pt(taille)
    run.font.bold = gras
    run.font.italic = italique
    run.font.color.rgb = libs.RGBColor(*couleur_rgb(couleur))
    if segment is None:
        return
    if segment.souligne is not None:
        run.font.underline = segment.souligne
    if segment.barre is not None:
        run.font.strike = segment.barre
    if segment.surligne:
        run.font.highlight_color = getattr(libs.surlignages, _SURLIGNAGES[segment.surligne])


def _aligner(paragraphe: Any, libs: _Libs, alignement: str) -> None:
    """Applique un alignement de paragraphe."""
    paragraphe.alignment = getattr(libs.alignements, _ALIGNEMENTS[alignement])


#: Alignement vertical du spec → valeur OOXML (``w:vAlign``).
_VERTICAUX = {"haut": "top", "centre": "center", "bas": "bottom"}


def _aligner_cellule(cellule: Any, libs: _Libs, bloc: Tableau, placee: Any) -> None:
    """Applique l'alignement de la cellule, sinon celui de sa colonne de départ."""
    alignement = alignement_cellule(placee.cellule, bloc.alignements, placee.colonne)
    if alignement is None:
        return
    for paragraphe in cellule.paragraphs:
        _aligner(paragraphe, libs, alignement)


def _largeurs_par_defaut(spec: DocumentSpec, nb_colonnes: int) -> list[float]:
    """Répartit la largeur utile quand aucune largeur n'est fournie."""
    utile = spec.geometrie().largeur_utile_mm()
    return [round(utile / nb_colonnes, 2)] * nb_colonnes
