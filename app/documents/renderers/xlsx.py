"""Renderer XLSX — ``DocumentSpec`` → classeur (ADR 0007, incrément 9).

Jusqu'ici un classeur ne pouvait naître que du chemin « génération simple »
(``build_xlsx`` : openpyxl brut, sans aucun style) et ``rendre(spec,
format="xlsx")`` n'existait pas : un agent qui demandait un ``document_spec`` au
format ``xlsx`` recevait « Format de rendu inconnu ». Ce module ferme les deux
trous.

Deux entrées, un seul chemin de style :

- :func:`rendre_xlsx` — un ``DocumentSpec`` devient un classeur : **une feuille
  par bloc tableau** (nom d'onglet = titre du tableau), le reste du document
  (titre, métadonnées, paragraphes, listes, libellé/valeur, sources) en tête de
  la première feuille. Le tableau reste exploitable comme tableau, et le
  document reste lisible dans un tableur.
- :func:`rendre_tableur` — un seul onglet (première ligne = en-tête) pour les
  classeurs d'une seule table : fiche de présence, liste de bénéficiaires.

Le style vient du **design system** (``StyleTableau`` : en-tête, trames
alternées, bordures, taille) et des règles partagées par les renderers
(``app.documents.renderers.commun``) : le classeur ne décide de rien.

Règles tenues :

- **aucune valeur inventée** : une cellule vide reste vide, un texte reste un
  texte (aucune conversion devinée en nombre ou en date) et un texte qui
  commence par ``=`` est écrit comme **texte** — jamais comme formule exécutable ;
- **aucune perte silencieuse** : une image est signalée à sa place
  (« image non reportée dans le classeur ») au lieu de disparaître ;
- **lecture immédiate** : en-tête gelé, filtre automatique, largeurs calculées
  depuis le contenu (ou des ``largeurs_mm`` déclarées), en-tête répété à
  l'impression, mise en page ajustée à la largeur.

Limites documentées : les listes deviennent des lignes préfixées ; ``saut_de_page``
n'a pas d'équivalent (ignoré) ; un classeur ne porte ni couverture ni pagination
de document.
"""

from __future__ import annotations

import io
import re
from typing import Any, NamedTuple

from app.core.errors import CarsoError, GenerationError
from app.documents import spec as sp
from app.documents.renderers.commun import (
    alignement_cellule,
    couleur_cellule,
    est_ligne_de_donnee_alternee,
    fond_cellule,
    gras_cellule,
)
from app.documents.styles import DocumentStyle, StyleTableau, StyleTexte
from app.documents.tableau_xlsx import MM_PAR_CARACTERE

__all__ = ["rendre_xlsx", "rendre_tableur", "ecrire_tableau", "nom_onglet"]

#: Longueur maximale du nom d'un onglet Excel.
MAX_CARACTERES_ONGLET = 31

#: Caractères interdits dans un nom d'onglet Excel.
_CARACTERES_INTERDITS = re.compile(r"[\[\]:*?/\\]")

#: Marge intérieure d'une cellule, en caractères (confort de lecture Excel).
_MARGE_CARACTERES = 2.0
_LARGEUR_MIN = 8.0
_LARGEUR_MAX = 60.0

#: Au-delà, une cellule est renvoyée à la ligne (sinon la colonne s'étire).
_SEUIL_RENVOI = 40

#: Lignes de séparation posées après un bloc de texte.
_LIGNES_AIR = 1

_ALIGNEMENTS_EXCEL = {"gauche": "left", "centre": "center", "droite": "right"}

_LIBELLES_SOURCE = {
    "document_source": "Document source",
    "base_de_donnees": "Base de données",
    "saisie_utilisateur": "Saisie utilisateur",
    "modele_reference": "Modèle de référence",
    "information_derivee": "Information dérivée",
    "hypothese": "Hypothèse",
}


class _Libs(NamedTuple):
    """Points d'entrée openpyxl utilisés par le renderer (imports groupés)."""

    Workbook: Any
    Font: Any
    PatternFill: Any
    Border: Any
    Side: Any
    Alignment: Any
    PageSetupProperties: Any
    get_column_letter: Any


def _libs() -> _Libs:
    """Importe openpyxl (librairie optionnelle, extra ``documents``).

    Raises:
        GenerationError: openpyxl absent.
    """
    try:
        from app.documents.extraction.base import require_library

        openpyxl = require_library("openpyxl")
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.properties import PageSetupProperties
    except CarsoError as erreur:
        raise GenerationError(str(erreur)) from erreur
    return _Libs(
        Workbook=openpyxl.Workbook,
        Font=Font,
        PatternFill=PatternFill,
        Border=Border,
        Side=Side,
        Alignment=Alignment,
        PageSetupProperties=PageSetupProperties,
        get_column_letter=get_column_letter,
    )


def _argb(couleur: str) -> str:
    """``#1F4E79`` → ``FF1F4E79`` (openpyxl attend un canal alpha)."""
    return f"FF{couleur.lstrip('#').upper()}"


def _texte(valeur: Any) -> str:
    """Texte affichable d'une valeur de spec (``None`` → chaîne vide)."""
    if valeur is None:
        return ""
    return str(valeur)


def nom_onglet(brut: str | None, *, defaut: str, pris: set[str]) -> str:
    """Nom d'onglet valide, unique et lisible (contrainte Excel de 31 caractères).

    Args:
        brut: nom souhaité (titre du tableau, titre du document…).
        defaut: nom de repli quand ``brut`` est vide.
        pris: noms déjà utilisés dans ce classeur (modifié en place).

    Returns:
        Un nom d'onglet utilisable, suffixé si nécessaire pour rester unique.
    """
    base = _CARACTERES_INTERDITS.sub(" ", _texte(brut)).strip() or defaut
    candidat = base[:MAX_CARACTERES_ONGLET]
    suffixe = 2
    while candidat.lower() in pris:
        marque = f" ({suffixe})"
        candidat = f"{base[: MAX_CARACTERES_ONGLET - len(marque)]}{marque}"
        suffixe += 1
    pris.add(candidat.lower())
    return candidat


class _Redacteur:
    """Écriture d'un onglet : un seul chemin pour poser une cellule stylée."""

    def __init__(self, onglet: Any, libs: _Libs, style: DocumentStyle) -> None:
        self.onglet = onglet
        self.libs = libs
        self.style = style
        self.largeurs: dict[int, float] = {}

    # --- Écriture ------------------------------------------------------------

    def ecrire(
        self,
        ligne: int,
        colonne: int,
        texte: Any,
        *,
        tokens: StyleTexte | None = None,
        gras: bool | None = None,
        couleur: str | None = None,
        fond: str | None = None,
        taille_pt: float | None = None,
        alignement: str | None = None,
        bordures: str | None = None,
        fusion_colonnes: int = 1,
        fusion_lignes: int = 1,
    ) -> None:
        """Pose une cellule (texte seul — jamais une formule) et son habillage."""
        valeur = _texte(texte)
        cellule = self.onglet.cell(row=ligne, column=colonne, value=valeur)
        if valeur.startswith("="):
            # Un texte qui commence par « = » serait lu comme une formule : le
            # tableur exécuterait un contenu de document.
            cellule.data_type = "s"

        modele = tokens or self.style.corps
        renoncer = len(valeur) > _SEUIL_RENVOI or "\n" in valeur
        cellule.font = self.libs.Font(
            name=modele.police,
            size=taille_pt or modele.taille_pt,
            bold=modele.gras if gras is None else gras,
            italic=modele.italique,
            color=_argb(couleur or modele.couleur),
        )
        cellule.alignment = self.libs.Alignment(
            horizontal=_ALIGNEMENTS_EXCEL.get(alignement or modele.alignement, "left"),
            vertical="top",
            wrap_text=renoncer,
        )
        if fond:
            cellule.fill = self.libs.PatternFill(
                fill_type="solid", start_color=_argb(fond), end_color=_argb(fond)
            )
        if bordures:
            cote = self.libs.Side(style="thin", color=_argb(bordures))
            cellule.border = self.libs.Border(left=cote, right=cote, top=cote, bottom=cote)

        if fusion_colonnes > 1 or fusion_lignes > 1:
            self.onglet.merge_cells(
                start_row=ligne,
                start_column=colonne,
                end_row=ligne + fusion_lignes - 1,
                end_column=colonne + fusion_colonnes - 1,
            )
        self._mesurer(colonne, valeur, fusion_colonnes)

    def largeur_imposee(self, colonne: int, millimetres: float) -> None:
        """Largeur **déclarée** d'une colonne (``largeurs_mm`` du tableau)."""
        self.largeurs[colonne] = max(millimetres / MM_PAR_CARACTERE, _LARGEUR_MIN)

    def ajuster_largeurs(self) -> None:
        """Applique les largeurs mesurées aux colonnes de l'onglet."""
        for colonne, largeur in self.largeurs.items():
            self.onglet.column_dimensions[self.libs.get_column_letter(colonne)].width = min(
                largeur + _MARGE_CARACTERES, _LARGEUR_MAX
            )

    def _mesurer(self, colonne: int, valeur: str, fusion_colonnes: int) -> None:
        """Retient la largeur nécessaire (une cellule fusionnée n'impose rien)."""
        if fusion_colonnes > 1 or colonne in self.largeurs:
            return
        plus_longue = max((len(ligne) for ligne in valeur.split("\n")), default=0)
        self.largeurs[colonne] = max(min(float(plus_longue), _LARGEUR_MAX), _LARGEUR_MIN)


def _nouvel_onglet(
    classeur: Any, titre: str, style: DocumentStyle, libs: _Libs, *, premier: bool = False
) -> _Redacteur:
    """Crée un onglet titré (le premier réutilise la feuille d'un classeur neuf)."""
    if premier and len(classeur.worksheets) == 1:
        onglet = classeur.worksheets[0]
        onglet.title = titre
    else:
        onglet = classeur.create_sheet(title=titre)
    onglet.sheet_properties.tabColor = _argb(style.tableau.entete_fond)
    return _Redacteur(onglet, libs, style)


def _configurer_impression(onglet: Any, libs: _Libs) -> None:
    """Mise en page d'un onglet : paysage, ajusté à une page en largeur."""
    onglet.page_setup.orientation = "landscape"
    onglet.page_setup.fitToWidth = 1
    onglet.page_setup.fitToHeight = 0
    onglet.sheet_properties.pageSetUpPr = libs.PageSetupProperties(fitToPage=True)


# --- Feuille « document » -----------------------------------------------------


def _ecrire_metadonnees(redacteur: _Redacteur, spec: sp.DocumentSpec) -> int:
    """Titre + métadonnées renseignées.

    Returns:
        La première ligne libre après les métadonnées.
    """
    style = redacteur.style
    ligne = 1
    redacteur.ecrire(ligne, 1, spec.metadata.titre, tokens=style.titre, fusion_colonnes=2)
    ligne += 1

    references: list[tuple[str, Any]] = [
        ("Sous-titre", spec.metadata.sous_titre),
        ("Référence", spec.metadata.reference),
        ("Organisation", spec.metadata.organisation),
        ("Destinataire", spec.metadata.destinataire),
        ("Auteur", spec.metadata.auteur),
        (
            "Date du document",
            spec.metadata.date_document.isoformat() if spec.metadata.date_document else None,
        ),
        ("Objet", spec.metadata.objet),
    ]
    # Un champ vide n'est pas écrit : ni valeur inventée, ni libellé sans contenu.
    for libelle, valeur in references:
        if valeur is None or not str(valeur).strip():
            continue
        redacteur.ecrire(ligne, 1, libelle, gras=True)
        redacteur.ecrire(ligne, 2, valeur)
        ligne += 1
    return ligne + _LIGNES_AIR


def _ecrire_bloc(redacteur: _Redacteur, bloc: Any, ligne: int) -> int:
    """Écrit un bloc non tabulaire en feuille document.

    Returns:
        La première ligne libre après le bloc.
    """
    style = redacteur.style
    if isinstance(bloc, sp.Titre):
        redacteur.ecrire(
            ligne, 1, bloc.texte, tokens=style.titre_niveau(bloc.niveau), fusion_colonnes=2
        )
    elif isinstance(bloc, sp.Paragraphe):
        tokens = style.citation if bloc.role == "note" else style.corps
        redacteur.ecrire(ligne, 1, bloc.texte, tokens=tokens, fusion_colonnes=2)
    elif isinstance(bloc, sp.Liste):
        for position, item in enumerate(bloc.items, start=1):
            puce = f"{position}. " if bloc.numerotee else "• "
            redacteur.ecrire(ligne, 1, f"{puce}{item}", tokens=style.liste, fusion_colonnes=2)
            ligne += 1
    elif isinstance(bloc, sp.Paires):
        for paire in bloc.paires:
            redacteur.ecrire(ligne, 1, paire.libelle, gras=True)
            redacteur.ecrire(ligne, 2, paire.valeur)
            ligne += 1
    elif isinstance(bloc, sp.Encadre):
        tokens = style.encadre(bloc.genre)
        redacteur.ecrire(
            ligne,
            1,
            bloc.titre or tokens.libelle or bloc.genre,
            gras=True,
            couleur=tokens.couleur_titre,
            fond=tokens.fond,
            fusion_colonnes=2,
        )
        redacteur.ecrire(ligne + 1, 1, bloc.texte, fond=tokens.fond, fusion_colonnes=2)
        ligne += 2
    elif isinstance(bloc, sp.Citation):
        texte = f"« {bloc.texte} »"
        if bloc.attribution:
            texte = f"{texte} — {bloc.attribution}"
        redacteur.ecrire(ligne, 1, texte, tokens=style.citation, fusion_colonnes=2)
    elif isinstance(bloc, sp.Image):
        mention = "image non reportée dans le classeur"
        if bloc.legende:
            mention = f"{mention} : {bloc.legende}"
        redacteur.ecrire(ligne, 1, f"({mention})", tokens=style.citation, fusion_colonnes=2)
    # `saut_de_page` n'a pas d'équivalent dans un tableur : ignoré.
    return ligne + 1 + _LIGNES_AIR


def _ecrire_sources(redacteur: _Redacteur, spec: sp.DocumentSpec, ligne: int) -> int:
    """Sources déclarées (genre, référence, précision), si le document en porte.

    Returns:
        La première ligne libre après les sources.
    """
    if not spec.sources:
        return ligne
    redacteur.ecrire(ligne, 1, "Sources", gras=True, fusion_colonnes=2)
    ligne += 1
    for source in spec.sources:
        detail = " — ".join(
            partie for partie in (source.reference, source.precision) if partie
        )
        redacteur.ecrire(ligne, 1, _LIBELLES_SOURCE.get(source.genre, source.genre), gras=True)
        redacteur.ecrire(ligne, 2, detail)
        ligne += 1
    return ligne + _LIGNES_AIR


# --- Feuilles de tableaux -----------------------------------------------------


def ecrire_tableau(
    redacteur: _Redacteur,
    bloc: sp.Tableau,
    *,
    ligne_depart: int = 1,
    colonne_depart: int = 1,
) -> int:
    """Écrit un bloc tableau (en-tête, bandes alternées, fusions, bordures).

    Args:
        redacteur: onglet cible.
        bloc: tableau du spec (en-têtes optionnels, fusions, ligne de total).
        ligne_depart: première ligne d'écriture (1-based).
        colonne_depart: première colonne d'écriture (1-based).

    Returns:
        La première ligne libre après le tableau.
    """
    style = redacteur.style
    tokens: StyleTableau = style.tableau
    grille = bloc.grille()
    nb_entete = bloc.nb_lignes_entete()
    ligne_tableau = ligne_depart

    if bloc.titre_tableau:
        redacteur.ecrire(
            ligne_tableau,
            colonne_depart,
            bloc.titre_tableau,
            tokens=style.titre_niveau(4),
            fusion_colonnes=max(bloc.largeur_grille(), 1),
        )
        ligne_tableau += 1

    premiere = ligne_tableau
    derniere = ligne_tableau - 1
    for index_ligne, rangees in enumerate(grille):
        ligne = ligne_tableau + index_ligne
        derniere = ligne
        en_entete = index_ligne < nb_entete
        index_donnee = index_ligne - nb_entete
        total = bloc.total_ligne is not None and index_donnee == bloc.total_ligne
        alternance = bool(
            tokens.bandes_alternees
            and index_donnee >= 0
            and est_ligne_de_donnee_alternee(index_donnee)
            and not total
        )
        for placee in rangees:
            redacteur.ecrire(
                ligne,
                colonne_depart + placee.colonne,
                placee.cellule.texte,
                gras=gras_cellule(
                    placee.cellule, en_entete=en_entete, total=total, tokens=tokens
                ),
                couleur=couleur_cellule(
                    placee.cellule, en_entete=en_entete, style=style, tokens=tokens
                ),
                fond=fond_cellule(
                    placee.cellule,
                    en_entete=en_entete,
                    total=total,
                    alternance=alternance,
                    tokens=tokens,
                ),
                taille_pt=placee.cellule.taille_pt or tokens.taille_pt,
                alignement=alignement_cellule(placee.cellule, bloc.alignements, placee.colonne),
                bordures=tokens.bordure_couleur if tokens.bordures else None,
                fusion_colonnes=placee.colonnes,
                fusion_lignes=placee.lignes,
            )

    for position, millimetres in enumerate(bloc.largeurs_mm or []):
        redacteur.largeur_imposee(colonne_depart + position, millimetres)

    derniere_colonne = redacteur.libs.get_column_letter(
        colonne_depart + bloc.largeur_grille() - 1
    )
    premiere_colonne = redacteur.libs.get_column_letter(colonne_depart)
    # Lisibilité : en-tête gelé, filtre sur tout le tableau, en-tête répété à
    # l'impression — un tableau reste exploitable tel quel.
    if nb_entete > 0:
        redacteur.onglet.freeze_panes = redacteur.onglet.cell(
            row=premiere + nb_entete, column=colonne_depart
        )
        if tokens.repeter_entete and bloc.repeter_entete:
            redacteur.onglet.print_title_rows = f"{premiere}:{premiere + nb_entete - 1}"
    if derniere >= premiere:
        redacteur.onglet.auto_filter.ref = (
            f"{premiere_colonne}{premiere}:{derniere_colonne}{derniere}"
        )
    return derniere + 1 + _LIGNES_AIR


# --- Entrées publiques --------------------------------------------------------


def _onglet_document(
    classeur: Any, spec: sp.DocumentSpec, style: DocumentStyle, libs: _Libs, pris: set[str]
) -> None:
    """Première feuille : le document lui-même (métadonnées, texte, sources)."""
    titre = nom_onglet(spec.metadata.titre, defaut="Document", pris=pris)
    redacteur = _nouvel_onglet(classeur, titre, style, libs, premier=True)
    ligne = _ecrire_metadonnees(redacteur, spec)
    for bloc in spec.blocs:
        if isinstance(bloc, sp.Tableau):
            continue
        ligne = _ecrire_bloc(redacteur, bloc, ligne)
    _ecrire_sources(redacteur, spec, ligne)
    redacteur.ajuster_largeurs()
    _configurer_impression(redacteur.onglet, libs)


def rendre_xlsx(spec: sp.DocumentSpec, *, images: Any = None) -> bytes:
    """Rend un ``DocumentSpec`` en classeur XLSX (une feuille par tableau).

    Args:
        spec: document structuré à rendre.
        images: ignoré — un classeur n'intègre pas les images du document ; leur
            emplacement est signalé dans la feuille document.

    Returns:
        Les octets du classeur.

    Raises:
        GenerationError: openpyxl absent ou échec de construction.
    """
    del images
    libs = _libs()
    style = spec.style_effectif()
    try:
        classeur = libs.Workbook()
        pris: set[str] = set()
        _onglet_document(classeur, spec, style, libs, pris)

        numero = 0
        for bloc in spec.blocs:
            if not isinstance(bloc, sp.Tableau):
                continue
            numero += 1
            entete = nom_onglet(bloc.titre_tableau, defaut=f"Tableau {numero}", pris=pris)
            redacteur = _nouvel_onglet(classeur, entete, style, libs)
            ecrire_tableau(redacteur, bloc)
            redacteur.ajuster_largeurs()
            _configurer_impression(redacteur.onglet, libs)

        classeur.properties.title = spec.metadata.titre
        classeur.properties.creator = spec.metadata.auteur or "CARSO"
        classeur.properties.subject = spec.metadata.objet or ""
        classeur.active = 0
        buffer = io.BytesIO()
        classeur.save(buffer)
    except GenerationError:
        raise
    except Exception as erreur:  # pragma: no cover - défensif (bibliothèque tierce)
        raise GenerationError(f"Échec du rendu XLSX : {erreur}") from erreur
    return buffer.getvalue()


def rendre_tableur(
    *,
    titre_feuille: str,
    lignes: list[list[Any]],
    style: DocumentStyle,
) -> bytes:
    """Rend un classeur d'une seule table (première ligne = en-tête).

    C'est le chemin des fiches opérationnelles (présence, listes de
    bénéficiaires) : un onglet, un tableau stylé, sans feuille document.

    Args:
        titre_feuille: nom de l'onglet.
        lignes: lignes brutes ; la première ligne est l'en-tête.
        style: design system appliqué (en-tête, bandes, bordures).

    Returns:
        Les octets du classeur.

    Raises:
        GenerationError: openpyxl absent ou échec de construction.
    """
    libs = _libs()
    tokens = style.tableau
    try:
        classeur = libs.Workbook()
        nom = nom_onglet(titre_feuille, defaut="Feuille", pris=set())
        redacteur = _nouvel_onglet(classeur, nom, style, libs, premier=True)

        if lignes:
            largeur = max(len(ligne) for ligne in lignes)
            for index_ligne, ligne in enumerate(lignes):
                en_entete = index_ligne == 0
                alternance = bool(
                    tokens.bandes_alternees
                    and index_ligne >= 1
                    and est_ligne_de_donnee_alternee(index_ligne - 1)
                )
                for index_colonne, valeur in enumerate(ligne):
                    redacteur.ecrire(
                        index_ligne + 1,
                        index_colonne + 1,
                        valeur,
                        gras=tokens.entete_gras if en_entete else None,
                        couleur=tokens.entete_couleur if en_entete else None,
                        fond=tokens.entete_fond
                        if en_entete
                        else (tokens.bande_fond if alternance else None),
                        taille_pt=tokens.taille_pt,
                        bordures=tokens.bordure_couleur if tokens.bordures else None,
                    )
            redacteur.onglet.freeze_panes = redacteur.onglet.cell(row=2, column=1)
            redacteur.onglet.auto_filter.ref = (
                f"A1:{redacteur.libs.get_column_letter(largeur)}{len(lignes)}"
            )
            redacteur.onglet.print_title_rows = "1:1"
        redacteur.ajuster_largeurs()
        _configurer_impression(redacteur.onglet, libs)

        classeur.properties.creator = "CARSO"
        buffer = io.BytesIO()
        classeur.save(buffer)
    except GenerationError:
        raise
    except Exception as erreur:  # pragma: no cover - défensif (bibliothèque tierce)
        raise GenerationError(f"Échec du rendu XLSX : {erreur}") from erreur
    return buffer.getvalue()
