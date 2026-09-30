"""Renderer PDF — ``DocumentSpec`` → octets PDF (ADR 0005, Phases 8).

Le PDF est un **second rendu du même spec**, pas une conversion du DOCX : le
système ne dépend donc d'aucun convertisseur externe (LibreOffice, pandoc). Il
sert deux usages : livrable PDF natif, et support de l'inspection visuelle
(``app.documents.rasterize`` transforme ses pages en images).

Ce que ce renderer gère : format et orientation, marges, couverture, titres,
paragraphes (corps/chapeau/note), listes à puces et numérotées, tableaux
(en-tête simple ou à étages, fusions horizontales et verticales, trames par
cellule, largeurs de colonnes, ligne de total, en-tête répété),
images, encadrés, citations, blocs libellé/valeur, sauts de page, en-tête et
pied de page avec numérotation, métadonnées PDF.

Limite assumée et documentée : une cellule fusionnée verticalement place son
texte en haut de la zone fusionnée (pas de centrage vertical), et le tableau est
dessiné cellule par cellule — fpdf2 ne connaît pas les fusions.

Limite assumée et documentée : les polices de base du format PDF (Helvetica /
Times / Courier) couvrent ISO-8859-1 mais pas l'Unicode complet. Deux parades :
une table de translittération typographique (tirets longs, puces, guillemets
anglais, euro, points de suspension) puis un repli latin-1 ; et l'embarquement
d'une police Unicode si ``DOCUMENT_POLICE_TTF`` est configurée. Un « € » ne
casse jamais un rendu.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, NamedTuple
from unicodedata import normalize

from app.core.errors import CarsoError, GenerationError, ValidationError
from app.documents.assets import FournisseurImages, resoudre_image
from app.documents.spec import (
    MARQUEUR_A_COMPLETER,
    MARQUEUR_A_CONFIRMER,
    MARQUEUR_RE,
    Bloc,
    Citation,
    Couverture,
    DocumentSpec,
    Encadre,
    Image,
    Liste,
    Paires,
    Paragraphe,
    SautDePage,
    Tableau,
    Titre,
)
from app.documents.styles import (
    FORMATS_MM,
    DocumentStyle,
    StyleEntetePied,
    StyleTexte,
    couleur_rgb,
)

__all__ = ["rendre_pdf", "texte_pdf"]

#: Translittération des caractères typographiques absents des polices PDF de base.
_TRANSLITTERATION = {
    "\u2014": "-",  # tiret cadratin
    "\u2013": "-",  # tiret demi-cadratin
    "\u2012": "-",
    "\u2010": "-",
    "\u2022": "-",  # puce
    "\u25aa": "-",
    "\u00a0": " ",  # espace insécable
    "\u202f": " ",
    "\u2009": " ",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u0153": "oe",  # œ
    "\u0152": "OE",  # Œ
    "\u20ac": "EUR",  # €
    "\u2192": "->",
    "\u2265": ">=",
    "\u2264": "<=",
    "\u00d7": "x",
    "\u2713": "v",
    "\u00b7": "-",
}

#: Alignements du domaine → codes fpdf2.
_ALIGNEMENTS = {"gauche": "L", "centre": "C", "droite": "R", "justifie": "J"}

#: Points → millimètres (les tokens sont en points, fpdf2 travaille en mm).
_PT_VERS_MM = 25.4 / 72.0

#: Nom de famille de la police Unicode embarquée éventuelle.
_FAMILLE_UNICODE = "CarsoUnicode"


class _Libs(NamedTuple):
    """Points d'entrée fpdf2 utilisés par le renderer."""

    FPDF: Any
    FontFace: Any
    XPos: Any
    YPos: Any


def _libs() -> _Libs:
    """Importe fpdf2 (librairie optionnelle, extra ``documents``).

    Raises:
        GenerationError: fpdf2 absent.
    """
    try:
        from app.documents.extraction.base import require_library

        fpdf = require_library("fpdf")
        from fpdf.enums import XPos, YPos
    except CarsoError as erreur:
        raise GenerationError(str(erreur)) from erreur
    return _Libs(FPDF=fpdf.FPDF, FontFace=fpdf.FontFace, XPos=XPos, YPos=YPos)


def texte_pdf(texte: str) -> str:
    """Rend un texte sûr pour les polices PDF de base (jamais d'exception).

    Un document réel contient des tirets longs, des puces et parfois un « € » :
    les translittérer est préférable à un rendu qui échoue. Le repli final
    remplace tout caractère restant hors ISO-8859-1 par ``?`` — aucune page ne
    se perd à cause d'un caractère exotique.
    """
    prepare = "".join(_TRANSLITTERATION.get(caractere, caractere) for caractere in texte)
    prepare = normalize("NFC", prepare)
    try:
        prepare.encode("latin-1")
    except UnicodeEncodeError:
        prepare = prepare.encode("latin-1", errors="replace").decode("latin-1")
    return prepare


def _famille(police: str) -> str:
    """Associe une police de style aux familles de base disponibles en PDF."""
    nom = police.strip().lower()
    if any(cle in nom for cle in ("times", "serif", "georgia", "cambria", "garamond")):
        return "Times"
    if any(cle in nom for cle in ("courier", "consola", "mono")):
        return "Courier"
    return "Helvetica"


def _style_police(tokens: StyleTexte) -> str:
    """Code de style fpdf2 (``""``, ``B``, ``I``, ``BI``)."""
    return ("B" if tokens.gras else "") + ("I" if tokens.italique else "")


def _hauteur_ligne(tokens: StyleTexte) -> float:
    """Hauteur de ligne en millimètres, dérivée de la taille et de l'interligne."""
    return round(max(2.0, tokens.taille_pt * _PT_VERS_MM * tokens.interligne), 2)


def _marqueur(statut: str) -> str:
    """Marqueur affiché pour un statut de renseignement non satisfait."""
    if statut in ("inconnu", "manquant"):
        return MARQUEUR_A_COMPLETER
    if statut == "a_confirmer":
        return MARQUEUR_A_CONFIRMER
    return ""


def rendre_pdf(spec: DocumentSpec, *, images: FournisseurImages | None = None) -> bytes:
    """Rend un ``DocumentSpec`` en octets PDF.

    Args:
        spec: le document structuré à rendre.
        images: fournisseur d'images (``None`` si le document n'en contient pas).

    Raises:
        GenerationError: fpdf2 absent ou échec moteur.
        ValidationError: image référencée mais introuvable.
    """
    libs = _libs()
    rendu = _RenduPdf(libs, spec=spec, style=spec.style_effectif(), images=images)
    pdf = rendu.pdf
    try:
        pdf.set_title(texte_pdf(spec.metadata.titre))
        if spec.metadata.auteur:
            pdf.set_author(texte_pdf(spec.metadata.auteur))
        if spec.metadata.objet:
            pdf.set_subject(texte_pdf(spec.metadata.objet))
        if spec.metadata.mots_cles:
            pdf.set_keywords(", ".join(spec.metadata.mots_cles))
        if spec.metadata.reference:
            pdf.set_creator(f"CARSO AI - {spec.metadata.reference}")

        couverture = spec.couverture()
        if couverture is not None:
            rendu.rendre_couverture(couverture)
        rendu.ajouter_page()
        for bloc in spec.blocs:
            rendu.rendre_bloc(bloc)
        octets = bytes(pdf.output())
    except (ValidationError, GenerationError):
        raise
    except Exception as erreur:  # pragma: no cover - défensif (bibliothèque tierce)
        raise GenerationError(f"Échec du rendu PDF : {erreur}") from erreur

    if not octets.startswith(b"%PDF"):  # pragma: no cover - garde interne
        raise GenerationError("Le rendu PDF est invalide (signature absente)")
    return octets


class _RenduPdf:
    """Rendu d'un ``DocumentSpec`` avec fpdf2 (une instance par document).

    L'instance ``pdf`` est une **sous-classe** de ``FPDF`` dont ``header()`` et
    ``footer()`` délèguent à ce rendu : l'en-tête et le pied sont donc écrits
    aussi sur les pages créées automatiquement par les sauts de page.
    """

    def __init__(
        self,
        libs: _Libs,
        *,
        spec: DocumentSpec,
        style: DocumentStyle,
        images: FournisseurImages | None,
    ) -> None:
        geometrie = spec.geometrie()
        self.libs = libs
        self.spec = spec
        self.style = style
        self.images = images
        self.geometrie = geometrie
        self._flux: list[io.BytesIO] = []
        #: Famille Unicode embarquée (``None`` = polices de base + translittération).
        self._police_unifiee: str | None = None
        self._couverture = spec.couverture() is not None
        self._entete = _tokens_zone(style, spec, entete=True)
        self._pied = _tokens_zone(style, spec, entete=False)

        classe = _classe_pdf(libs, self)
        # fpdf2 attend le format **en portrait** puis applique lui-même la rotation
        # quand l'orientation est paysage : on lui donne donc les dimensions du
        # format, jamais celles déjà tournées.
        largeur, hauteur = FORMATS_MM[geometrie.format]
        self.pdf = classe(
            orientation="L" if geometrie.orientation == "paysage" else "P",
            unit="mm",
            format=(largeur, hauteur),
        )
        self.pdf.set_margins(
            geometrie.marges.gauche_mm,
            geometrie.marges.haut_mm,
            geometrie.marges.droite_mm,
        )
        self.pdf.set_auto_page_break(auto=True, margin=geometrie.marges.bas_mm)
        self.pdf.set_display_mode("fullwidth")
        self.pdf.alias_nb_pages()
        self._police_unifiee = self._enregistrer_police_optionnelle()

    @property
    def largeur_utile(self) -> float:
        """Largeur imprimable en millimètres."""
        return self.geometrie.largeur_utile_mm()

    # --- En-tête / pied de page (appelés par fpdf2) --------------------------

    def entete(self) -> None:
        """Écrit l'en-tête de page (jamais sur la couverture)."""
        if self.pdf.page_no() == 1 and self._couverture:
            return
        tokens = self._entete
        texte = (tokens.texte or self.spec.mise_en_page.mention_proposition or "").strip()
        if not texte:
            return
        self.pdf.set_font(self._famille(), "", tokens.taille_pt)
        self.pdf.set_text_color(*couleur_rgb(tokens.couleur))
        self.pdf.cell(
            0,
            _hauteur_ligne(tokens.comme_style_texte()),
            texte_pdf(texte),
            align=_ALIGNEMENTS[tokens.alignement],
            new_x=self.libs.XPos.LMARGIN,
            new_y=self.libs.YPos.NEXT,
        )

    def pied(self) -> None:
        """Écrit le pied de page avec numérotation (jamais sur la couverture)."""
        if self.pdf.page_no() == 1 and self._couverture:
            return
        tokens = self._pied
        texte = texte_pdf(tokens.texte or "")
        numero = (
            self.spec.mise_en_page.numero_de_page
            if self.spec.mise_en_page.numero_de_page is not None
            else self.style.pied.numero_page
        )
        if numero:
            compteur = f"Page {self.pdf.page_no()} / {{nb}}"
            texte = f"{texte}   {compteur}".strip() if texte else compteur
        if not texte:
            return
        self.pdf.set_y(-1 * max(10.0, self.geometrie.marges.bas_mm - 6.0))
        self.pdf.set_font(self._famille(), "", tokens.taille_pt)
        self.pdf.set_text_color(*couleur_rgb(tokens.couleur))
        if tokens.filet:
            y = self.pdf.get_y() - 1.5
            self.pdf.set_draw_color(*couleur_rgb(tokens.couleur))
            self.pdf.set_line_width(0.2)
            self.pdf.line(
                self.geometrie.marges.gauche_mm,
                y,
                self.geometrie.marges.gauche_mm + self.largeur_utile,
                y,
            )
        self.pdf.cell(
            0,
            _hauteur_ligne(tokens.comme_style_texte()),
            texte,
            align=_ALIGNEMENTS[tokens.alignement],
        )

    # --- Pages et blocs ------------------------------------------------------

    def ajouter_page(self) -> None:
        """Ajoute une page (en-tête et pied sont écrits par fpdf2)."""
        self.pdf.add_page()

    def rendre_couverture(self, couverture: Couverture) -> None:
        """Rend la page de couverture (titre, sous-titre, champs, mention)."""
        tokens = self.style.couverture
        align = _ALIGNEMENTS[tokens.alignement]
        self.pdf.add_page()
        self.pdf.ln(tokens.espace_haut_mm)
        if couverture.logo_document_id is not None or couverture.logo_chemin:
            self._image(
                Image(
                    document_id=couverture.logo_document_id,
                    chemin=couverture.logo_chemin,
                    largeur_mm=tokens.logo_largeur_mm,
                ),
                centrer=True,
            )
        self.pdf.set_font(self._famille(), "B", tokens.titre_taille_pt)
        self.pdf.set_text_color(*couleur_rgb(tokens.couleur_accent))
        self.pdf.multi_cell(
            0,
            _hauteur_ligne(StyleTexte(taille_pt=tokens.titre_taille_pt)),
            texte_pdf(couverture.titre or self.spec.metadata.titre),
            align=align,
            new_x=self.libs.XPos.LMARGIN,
            new_y=self.libs.YPos.NEXT,
        )
        self.pdf.ln(3)
        sous_titre = couverture.sous_titre or self.spec.metadata.sous_titre
        if sous_titre:
            self._ecrire(
                StyleTexte(
                    taille_pt=tokens.sous_titre_taille_pt,
                    alignement=tokens.alignement,
                    espace_apres_pt=6.0,
                ),
                sous_titre,
            )
        if tokens.bandeau:
            self.pdf.set_draw_color(*couleur_rgb(tokens.couleur_accent))
            self.pdf.set_line_width(0.6)
            y = self.pdf.get_y()
            x = self.geometrie.marges.gauche_mm + (
                (self.largeur_utile - 40.0) / 2 if tokens.alignement == "centre" else 0.0
            )
            self.pdf.line(x, y, x + 40.0, y)
            self.pdf.ln(6)
        organisation = couverture.organisation or self.spec.metadata.organisation
        if organisation:
            self._ecrire(
                StyleTexte(
                    taille_pt=self.style.corps.taille_pt + 1,
                    gras=True,
                    alignement=tokens.alignement,
                    espace_apres_pt=3.0,
                ),
                organisation,
            )
        for paire in couverture.champs:
            self._ecrire(
                StyleTexte(
                    taille_pt=self.style.corps.taille_pt,
                    alignement=tokens.alignement,
                    espace_apres_pt=1.5,
                ),
                f"{paire.libelle} : {paire.valeur}",
            )
        if couverture.date_ligne:
            self._ecrire(
                StyleTexte(
                    taille_pt=self.style.corps.taille_pt,
                    italique=True,
                    alignement=tokens.alignement,
                    espace_apres_pt=1.5,
                ),
                couverture.date_ligne,
            )
        if self.spec.mise_en_page.mention_proposition:
            self.pdf.ln(8)
            self._ecrire(
                StyleTexte(
                    taille_pt=8.5,
                    italique=True,
                    couleur="#6B7280",
                    alignement=tokens.alignement,
                    espace_apres_pt=0.0,
                ),
                self.spec.mise_en_page.mention_proposition,
            )

    def rendre_bloc(self, bloc: Bloc) -> None:
        """Traduit un bloc du spec en contenu PDF."""
        if isinstance(bloc, Titre):
            self._ecrire_marque(
                self.style.titre_niveau(bloc.niveau), bloc.texte, bloc.statut
            )
            return
        if isinstance(bloc, Paragraphe):
            tokens = self.style.citation if bloc.role == "note" else self.style.corps
            if bloc.role == "chapeau":
                tokens = tokens.model_copy(
                    update={
                        "taille_pt": tokens.taille_pt + 1.0,
                        "espace_apres_pt": tokens.espace_apres_pt + 3.0,
                    }
                )
            self._ecrire_marque(tokens, bloc.texte, bloc.statut)
            return
        if isinstance(bloc, Liste):
            self._liste(bloc)
            return
        if isinstance(bloc, Tableau):
            self._tableau(bloc)
            return
        if isinstance(bloc, Image):
            self._image(bloc, centrer=True)
            return
        if isinstance(bloc, Encadre):
            self._encadre(bloc)
            return
        if isinstance(bloc, Citation):
            self._ecrire_marque(self.style.citation, f"« {bloc.texte} »", bloc.statut)
            if bloc.attribution:
                self._ecrire(
                    self.style.citation.model_copy(
                        update={"alignement": "droite", "espace_apres_pt": 6.0}
                    ),
                    f"- {bloc.attribution}",
                )
            return
        if isinstance(bloc, Paires):
            self._paires(bloc)
            return
        if isinstance(bloc, SautDePage):
            self.ajouter_page()
            return
        raise GenerationError(f"Type de bloc non rendable : {type(bloc).__name__}")

    # --- Helpers d'écriture --------------------------------------------------

    def _ecrire(self, tokens: StyleTexte, texte: str) -> None:
        """Écrit un paragraphe avec ses tokens (police, taille, couleur, marges)."""
        if tokens.espace_avant_pt:
            self.pdf.ln(tokens.espace_avant_pt * _PT_VERS_MM)
        self.pdf.set_font(
            self._famille(tokens.police), _style_police(tokens), tokens.taille_pt
        )
        self.pdf.set_text_color(*couleur_rgb(tokens.couleur))
        self.pdf.multi_cell(
            0,
            _hauteur_ligne(tokens),
            texte_pdf(texte),
            align=_ALIGNEMENTS[tokens.alignement],
            new_x=self.libs.XPos.LMARGIN,
            new_y=self.libs.YPos.NEXT,
        )
        if tokens.espace_apres_pt:
            self.pdf.ln(tokens.espace_apres_pt * _PT_VERS_MM)

    def _ecrire_marque(self, tokens: StyleTexte, texte: str, statut: str) -> None:
        """Écrit un texte en ajoutant le marqueur d'une donnée non fournie."""
        ajout = "" if MARQUEUR_RE.search(texte) else _marqueur(statut)
        self._ecrire(tokens, f"{texte} {ajout}".strip())

    def _liste(self, bloc: Liste) -> None:
        """Liste à puces (pastille tracée) ou numérotée, indentée par profondeur."""
        tokens = self.style.liste
        indent = 3.0 + 4.0 * bloc.profondeur
        for index, item in enumerate(bloc.items, start=1):
            self._preparer_tokens(tokens)
            hauteur = _hauteur_ligne(tokens)
            self.pdf.set_x(self.geometrie.marges.gauche_mm + indent)
            if bloc.numerotee:
                self.pdf.cell(6.0, hauteur, texte_pdf(f"{index}."))
            else:
                # La pastille est **tracée** : aucune dépendance à une police Unicode.
                self.pdf.set_fill_color(*couleur_rgb(tokens.couleur))
                self.pdf.circle(
                    self.geometrie.marges.gauche_mm + indent + 1.2,
                    self.pdf.get_y() + hauteur / 2,
                    0.7,
                    style="F",
                )
                self.pdf.set_x(self.geometrie.marges.gauche_mm + indent + 4.0)
            largeur = (
                self.geometrie.marges.gauche_mm + self.largeur_utile - self.pdf.get_x()
            )
            ajout = "" if MARQUEUR_RE.search(item) else _marqueur(bloc.statut)
            self.pdf.multi_cell(
                largeur,
                hauteur,
                texte_pdf(f"{item} {ajout}".strip()),
                new_x=self.libs.XPos.LMARGIN,
                new_y=self.libs.YPos.NEXT,
            )
            if tokens.espace_apres_pt:
                self.pdf.ln(tokens.espace_apres_pt * _PT_VERS_MM * 0.5)

    def _tableau(self, bloc: Tableau) -> None:
        """Tableau : fusions horizontales/verticales, trames, en-tête répété.

        fpdf2 ne connaît pas les fusions de cellules : le tableau est donc
        dessiné cellule par cellule (rectangle + texte) à partir de la grille du
        spec. Une ligne de section pleine largeur, un en-tête à deux étages
        (année au-dessus des mois) ou une cellule tramée se rendent donc comme
        dans le DOCX, sans moteur externe.
        """
        tokens = self.style.tableau
        if bloc.titre_tableau:
            self._ecrire(
                self.style.corps.model_copy(
                    update={"gras": True, "espace_avant_pt": 5.0, "espace_apres_pt": 2.0}
                ),
                bloc.titre_tableau,
            )
        grille = bloc.grille()
        nb_colonnes = bloc.largeur_grille()
        nb_entete = bloc.nb_lignes_entete()
        largeurs = _largeurs(bloc.largeurs_mm, self.largeur_utile, nb_colonnes)
        corps = StyleTexte(
            police=self.style.corps.police, taille_pt=tokens.taille_pt, interligne=1.15
        )
        padding = max(0.7, tokens.marge_interne_pt * 0.55)
        hauteurs = self._hauteurs_tableau(grille, largeurs, corps, padding)
        for index_ligne, rangees in enumerate(grille):
            en_entete = index_ligne < nb_entete
            decalage = index_ligne - nb_entete
            total = bloc.total_ligne is not None and decalage == bloc.total_ligne
            alternance = bool(tokens.bandes_alternees and not total and decalage % 2 == 1)
            if self.pdf.get_y() + hauteurs[index_ligne] > self._limite_tableau():
                self.pdf.add_page()
                if nb_entete and bloc.repeter_entete and tokens.repeter_entete:
                    for index_entete in range(nb_entete):
                        self._ligne_tableau(
                            bloc,
                            grille[index_entete],
                            largeurs,
                            hauteurs,
                            corps,
                            padding,
                            en_entete=True,
                            total=False,
                            alternance=False,
                        )
            self._ligne_tableau(
                bloc,
                rangees,
                largeurs,
                hauteurs,
                corps,
                padding,
                en_entete=en_entete,
                total=total,
                alternance=alternance,
            )
        self.pdf.ln(3)

    def _hauteurs_tableau(
        self,
        grille: list[list[Any]],
        largeurs: list[float],
        corps: StyleTexte,
        padding: float,
    ) -> list[float]:
        """Hauteur de chaque ligne, mesurée sur le texte réel de ses cellules.

        Une cellule fusionnée verticalement agrandit la dernière ligne de son
        groupe pour que son texte tienne dans la hauteur fusionnée.
        """
        hauteur_ligne = _hauteur_ligne(corps)
        hauteurs: list[float] = []
        for rangees in grille:
            hauteur = hauteur_ligne + 2 * padding
            for placee in rangees:
                hauteur = max(hauteur, self._besoin_cellule(placee, largeurs, corps, padding))
            hauteurs.append(round(hauteur, 2))
        for rangees in grille:
            for placee in rangees:
                if placee.lignes < 2:
                    continue
                besoin = self._besoin_cellule(placee, largeurs, corps, padding)
                total = sum(hauteurs[placee.ligne : placee.ligne + placee.lignes])
                if besoin > total:
                    hauteurs[placee.ligne + placee.lignes - 1] += besoin - total
        return hauteurs

    def _besoin_cellule(
        self, placee: Any, largeurs: list[float], corps: StyleTexte, padding: float
    ) -> float:
        """Hauteur nécessaire à une cellule (texte mesuré à sa largeur fusionnée)."""
        largeur = sum(largeurs[placee.colonne : placee.colonne + placee.colonnes])
        return (
            self._hauteur_texte(corps, placee.cellule.texte, max(4.0, largeur - 2 * padding))
            + 2 * padding
        )

    def _limite_tableau(self) -> float:
        """Ordonnée au-delà de laquelle une ligne de tableau part sur une nouvelle page."""
        return self.pdf.h - self.geometrie.marges.bas_mm - 6.0

    def _ligne_tableau(
        self,
        bloc: Tableau,
        rangees: list[Any],
        largeurs: list[float],
        hauteurs: list[float],
        corps: StyleTexte,
        padding: float,
        *,
        en_entete: bool,
        total: bool,
        alternance: bool,
    ) -> None:
        """Dessine une ligne : rectangle par cellule (trame et filets) puis texte."""
        tokens = self.style.tableau
        x0 = self.geometrie.marges.gauche_mm
        y = self.pdf.get_y()
        hauteur_ligne = _hauteur_ligne(corps)
        garde = self.pdf.auto_page_break
        self.pdf.set_auto_page_break(False)
        try:
            for placee in rangees:
                largeur = sum(largeurs[placee.colonne : placee.colonne + placee.colonnes])
                x = x0 + sum(largeurs[: placee.colonne])
                hauteur = (
                    sum(hauteurs[placee.ligne : placee.ligne + placee.lignes])
                    if placee.lignes > 1
                    else hauteurs[placee.ligne]
                )
                fond = _fond_effectif(placee.cellule, en_entete, total, alternance, tokens)
                if fond:
                    self.pdf.set_fill_color(*couleur_rgb(fond))
                if fond or tokens.bordures:
                    self.pdf.rect(x, y, largeur, hauteur, style="DF" if fond else "D")
                couleur = placee.cellule.couleur_texte or (
                    tokens.entete_couleur if en_entete else self.style.corps.couleur
                )
                gras = (
                    placee.cellule.gras
                    if placee.cellule.gras is not None
                    else (bool(tokens.entete_gras) if en_entete else total)
                )
                self.pdf.set_font(
                    self._famille(self.style.corps.police),
                    "B" if gras else "",
                    placee.cellule.taille_pt or tokens.taille_pt,
                )
                self.pdf.set_text_color(*couleur_rgb(couleur))
                self.pdf.set_xy(x + padding, y + padding)
                self.pdf.multi_cell(
                    largeur - 2 * padding,
                    hauteur_ligne,
                    texte_pdf(placee.cellule.texte),
                    align=_ALIGNEMENTS[_alignement_effectif(placee, bloc)],
                )
        finally:
            self.pdf.set_auto_page_break(garde)
        self.pdf.set_y(y + hauteurs[rangees[0].ligne])

    def _encadre(self, bloc: Encadre) -> None:
        """Encadré : texte tramé dans un rectangle bordé (même esprit que le DOCX)."""
        tokens = self.style.encadre(bloc.genre)
        libelle = bloc.titre or tokens.libelle
        corps = StyleTexte(
            police=self.style.corps.police,
            taille_pt=self.style.corps.taille_pt,
            couleur=tokens.couleur_texte,
        )
        hauteur = self._hauteur_texte(corps, bloc.texte, self.largeur_utile - 6.0)
        if libelle:
            hauteur += _hauteur_ligne(corps)
        x = self.geometrie.marges.gauche_mm
        y = self.pdf.get_y()
        self.pdf.set_fill_color(*couleur_rgb(tokens.fond))
        self.pdf.set_draw_color(*couleur_rgb(tokens.bordure))
        self.pdf.set_line_width(0.4)
        self.pdf.rect(x, y, self.largeur_utile, hauteur + 3.0, style="DF")
        self.pdf.set_xy(x + 3.0, y + 1.5)
        if libelle:
            self._ecrire(
                corps.model_copy(
                    update={
                        "gras": True,
                        "couleur": tokens.couleur_titre,
                        "espace_apres_pt": 0.0,
                    }
                ),
                f"{libelle} -",
            )
        self._ecrire(corps.model_copy(update={"espace_apres_pt": 0.0}), bloc.texte)
        self.pdf.set_y(y + hauteur + 5.0)

    def _paires(self, bloc: Paires) -> None:
        """Bloc libellé/valeur : deux colonnes, libellés en gras."""
        tokens = StyleTexte(
            police=self.style.corps.police,
            taille_pt=self.style.tableau.taille_pt,
            couleur=self.style.corps.couleur,
            interligne=1.2,
        )
        hauteur = _hauteur_ligne(tokens)
        largeur_libelle = self.largeur_utile * 0.32
        for paire in bloc.paires:
            self.pdf.set_font(self._famille(tokens.police), "B", tokens.taille_pt)
            self.pdf.set_text_color(*couleur_rgb(tokens.couleur))
            self.pdf.cell(largeur_libelle, hauteur, texte_pdf(f"{paire.libelle} :"))
            self.pdf.set_font(self._famille(tokens.police), "", tokens.taille_pt)
            self.pdf.multi_cell(
                self.largeur_utile - largeur_libelle,
                hauteur,
                texte_pdf(paire.valeur),
                new_x=self.libs.XPos.LMARGIN,
                new_y=self.libs.YPos.NEXT,
            )
        self.pdf.ln(3)

    def _image(self, bloc: Image, *, centrer: bool) -> None:
        """Insère une image centrée, avec sa légende (erreur explicite si absente)."""
        contenu = resoudre_image(bloc, self.images)
        if contenu is None:
            raise ValidationError(
                "Image référencée sans fournisseur d'images",
                details={"document_id": str(bloc.document_id or ""), "chemin": bloc.chemin},
            )
        octets, nom = contenu
        flux = io.BytesIO(octets)
        self._flux.append(flux)
        largeur = min(bloc.largeur_mm, self.largeur_utile)
        x = (
            self.geometrie.marges.gauche_mm + (self.largeur_utile - largeur) / 2
            if centrer
            else self.pdf.get_x()
        )
        try:
            self.pdf.image(flux, x=x, w=largeur)
        except Exception as erreur:
            raise ValidationError(f"Image illisible : {nom}") from erreur
        self.pdf.ln(2)
        if bloc.legende:
            self._ecrire(
                StyleTexte(
                    taille_pt=8.5,
                    italique=True,
                    couleur="#6B7280",
                    alignement="centre",
                    espace_apres_pt=4.0,
                ),
                bloc.legende,
            )

    # --- Utilitaires internes ------------------------------------------------

    def _preparer_tokens(self, tokens: StyleTexte) -> None:
        """Pose police et couleur d'un rôle typographique sur le curseur courant."""
        self.pdf.set_font(
            self._famille(tokens.police), _style_police(tokens), tokens.taille_pt
        )
        self.pdf.set_text_color(*couleur_rgb(tokens.couleur))

    def _hauteur_texte(self, tokens: StyleTexte, texte: str, largeur: float) -> float:
        """Hauteur d'un texte multi-lignes, mesurée par fpdf2 (repli estimatif)."""
        hauteur = _hauteur_ligne(tokens)
        self._preparer_tokens(tokens)
        try:
            return float(
                self.pdf.multi_cell(
                    largeur, hauteur, texte_pdf(texte), dry_run=True, output="HEIGHT"
                )
            )
        except Exception:  # pragma: no cover - repli si l'API de mesure change
            caracteres_par_ligne = max(20.0, largeur / (tokens.taille_pt * 0.19))
            lignes = math.ceil(len(texte) / caracteres_par_ligne)
            return max(hauteur, lignes * hauteur)

    def _enregistrer_police_optionnelle(self) -> str | None:
        """Embarque une police Unicode si ``DOCUMENT_POLICE_TTF`` est configurée.

        Sans police Unicode, les polices de base du PDF sont utilisées : la
        translittération (``texte_pdf``) garantit qu'aucun caractère ne casse le
        rendu. Une configuration invalide est ignorée sans masquer le repli.
        """
        chemin = _chemin_police_optionnelle()
        if chemin is None:
            return None
        try:
            self.pdf.add_font(_FAMILLE_UNICODE, fname=chemin)
        except Exception:  # pragma: no cover - police illisible
            return None
        return _FAMILLE_UNICODE

    def _famille(self, police: str | None = None) -> str:
        """Famille effective : police embarquée si configurée, sinon police de base."""
        if self._police_unifiee:
            return self._police_unifiee
        return _famille(police or self.style.corps.police)


def _classe_pdf(libs: _Libs, rendu: _RenduPdf) -> Any:
    """Construit la sous-classe ``FPDF`` qui délègue en-tête et pied au rendu.

    fpdf2 appelle ``header()``/``footer()`` à chaque ``add_page()``, y compris
    pour les pages créées par un saut automatique : c'est le seul moyen d'avoir
    un en-tête et un pied corrects sur un document long. La sous-classe est
    construite dynamiquement (``type``) car ``FPDF`` n'existe qu'à l'exécution
    (librairie optionnelle).
    """

    return type(
        "PdfAvecZones",
        (libs.FPDF,),
        {
            "header": lambda self: rendu.entete(),
            "footer": lambda self: rendu.pied(),
        },
    )


def _chemin_police_optionnelle() -> str | None:
    """Chemin d'une police TTF configurée et réellement présente (sinon ``None``)."""
    try:
        from app.core.config import get_settings

        chemin = get_settings().document_police_ttf
    except Exception:  # pragma: no cover - configuration non chargée
        return None
    if not chemin:
        return None
    fichier = Path(chemin).expanduser()
    return str(fichier) if fichier.is_file() else None


def _tokens_zone(style: DocumentStyle, spec: DocumentSpec, *, entete: bool) -> StyleEntetePied:
    """Zone d'en-tête/pied effective (surcharge du document > style)."""
    zone = style.entete if entete else style.pied
    texte = spec.mise_en_page.entete_texte if entete else spec.mise_en_page.pied_texte
    if texte:
        return zone.model_copy(update={"texte": texte})
    if entete and not zone.texte and spec.mise_en_page.mention_proposition:
        return zone.model_copy(update={"texte": spec.mise_en_page.mention_proposition})
    return zone


def _alignement_effectif(placee: Any, bloc: Tableau) -> str:
    """Alignement d'une cellule : la cellule décide, sinon sa colonne de départ."""
    if placee.cellule.alignement:
        return str(placee.cellule.alignement)
    if bloc.alignements and placee.colonne < len(bloc.alignements):
        return str(bloc.alignements[placee.colonne])
    return "gauche"


def _fond_effectif(
    cellule: Any, en_entete: bool, total: bool, alternance: bool, tokens: Any
) -> str | None:
    """Trame effective d'une cellule (cellule > en-tête > total > bande alternée)."""
    if cellule.fond:
        return str(cellule.fond)
    if en_entete:
        return tokens.entete_fond or None
    if total or alternance:
        return tokens.bande_fond or None
    return None


def _largeurs(
    largeurs_mm: list[float] | None, largeur_utile: float, nb_colonnes: int
) -> list[float]:
    """Largeurs de colonnes effectives (déclarées, sinon répartition égale)."""
    if largeurs_mm and len(largeurs_mm) == nb_colonnes:
        total = sum(largeurs_mm)
        if total <= 0:
            return [round(largeur_utile / nb_colonnes, 2)] * nb_colonnes
        # On normalise sur la largeur imprimable : un tableau ne déborde jamais.
        facteur = largeur_utile / total
        return [round(largeur * facteur, 2) for largeur in largeurs_mm]
    return [round(largeur_utile / nb_colonnes, 2)] * nb_colonnes
