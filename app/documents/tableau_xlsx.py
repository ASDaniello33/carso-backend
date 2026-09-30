"""Import d'un tableau depuis un classeur XLSX vers un ``Tableau`` du spec.

Un appel à proposition CARSO arrive souvent avec ses annexes déjà saisies dans un
tableur (planning, matrice d'activités, budget). Redemander à l'agent de
ressaisir cette structure en JSON serait lent **et** infidèle : ce module
**lit** la feuille et restitue ce qui s'y trouve réellement — texte, fusions,
trames, graisse, alignements.

Rien n'est deviné :

- une cellule vide reste vide ;
- une fusion Excel devient une fusion ``Cellule`` (les cellules absorbées ne
  sont pas recréées, sauf si toute la ligne est absorbée : la ligne disparaît
  alors, la fusion verticale la portant déjà) ;
- les formules sont lues par leur valeur mise en cache (``data_only``) ;
- la ligne d'en-tête est **déclarée** par l'appelant (``ligne_entete``), jamais
  devinée par une heuristique ;
- un classeur protégé, chiffré ou corrompu produit une ``ValidationError``
  explicite plutôt qu'un tableau partiel silencieux.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.extraction.base import require_library
from app.documents.spec import Cellule, Tableau

__all__ = ["MM_PAR_CARACTERE", "import_tableau_xlsx", "tableau_depuis_feuille"]

#: Au-delà, le tableau n'est plus lisible sur une page : on refuse explicitement.
MAX_COLONNES = 30

#: Nombre de lignes maximal importées (mémoire et lisibilité).
MAX_LIGNES = 400

#: Largeur Excel (caractères) → millimètres, pour la mise en page du document.
#: Le renderer XLSX s'en sert dans l'autre sens (``largeurs_mm`` → caractères) :
#: une seule constante, donc une seule conversion entre les deux sens.
MM_PAR_CARACTERE = 1.85


def import_tableau_xlsx(
    chemin: Path,
    *,
    feuille: str | None = None,
    ligne_entete: int = 1,
    colonne_min: int = 1,
    colonne_max: int | None = None,
    ligne_min: int = 1,
    ligne_max: int | None = None,
    titre_tableau: str | None = None,
) -> Tableau:
    """Lit une plage d'une feuille XLSX et la convertit en ``Tableau``.

    Args:
        chemin: fichier XLSX. Le chemin vient de l'appelant (stockage contrôlé),
            jamais directement de l'agent.
        feuille: nom de feuille ; la première si ``None``.
        ligne_entete: ligne Excel (1-based) de l'en-tête ; ``0`` = aucune.
        colonne_min: première colonne (1-based, incluse).
        colonne_max: dernière colonne (1-based, incluse) ; le bord de la feuille
            si ``None``.
        ligne_min: première ligne (1-based, incluse).
        ligne_max: dernière ligne (1-based, incluse) ; le bas de la feuille si
            ``None``.
        titre_tableau: titre posé au-dessus du tableau dans le document.

    Raises:
        ValidationError: feuille inconnue, plage vide ou classeur illisible.
    """
    openpyxl = require_library("openpyxl")
    try:
        classeur = openpyxl.load_workbook(filename=str(chemin), data_only=True)
    except Exception as exc:  # openpyxl lève des erreurs variées si corrompu
        raise ValidationError(
            "Classeur XLSX illisible (fichier corrompu ou protégé)"
        ) from exc

    try:
        if feuille is None:
            resultat = classeur.worksheets[0]
        elif feuille in classeur.sheetnames:
            resultat = classeur[feuille]
        else:
            raise ValidationError(
                f"Feuille inconnue : {feuille!r}",
                details={"feuilles_disponibles": list(classeur.sheetnames)},
            )
        return tableau_depuis_feuille(
            resultat,
            titre_tableau=titre_tableau,
            ligne_entete=ligne_entete,
            colonne_min=colonne_min,
            colonne_max=colonne_max,
            ligne_min=ligne_min,
            ligne_max=ligne_max,
        )
    finally:
        classeur.close()


def tableau_depuis_feuille(
    feuille: Any,
    *,
    titre_tableau: str | None = None,
    ligne_entete: int = 1,
    colonne_min: int = 1,
    colonne_max: int | None = None,
    ligne_min: int = 1,
    ligne_max: int | None = None,
) -> Tableau:
    """Convertit une feuille openpyxl en ``Tableau`` (texte, fusions, trames)."""
    colonne_max = colonne_max or feuille.max_column
    ligne_max = ligne_max or feuille.max_row
    largeur = colonne_max - colonne_min + 1
    if largeur > MAX_COLONNES:
        raise ValidationError(
            f"Tableau trop large : {largeur} colonnes (maximum {MAX_COLONNES})."
        )
    hauteur = ligne_max - ligne_min + 1
    if hauteur > MAX_LIGNES:
        raise ValidationError(
            f"Tableau trop long : {hauteur} lignes (maximum {MAX_LIGNES})."
        )

    absorbee, fusions = _plages_fusions(feuille, colonne_min, colonne_max, ligne_min, ligne_max)
    index_entete = ligne_entete if ligne_min <= ligne_entete <= ligne_max else 0

    entete = (
        _cellules_ligne(feuille, index_entete, colonne_min, colonne_max, absorbee, fusions)
        if index_entete
        else None
    )
    lignes: list[list[Cellule]] = []
    for index in range(ligne_min, ligne_max + 1):
        if index == index_entete:
            continue
        cellules = _cellules_ligne(feuille, index, colonne_min, colonne_max, absorbee, fusions)
        if cellules is not None:
            lignes.append(cellules)

    if not lignes:
        raise ValidationError(
            "Aucune ligne de données exploitable dans la plage demandée.",
            details={
                "ligne_min": ligne_min,
                "ligne_max": ligne_max,
                "ligne_entete": index_entete,
            },
        )

    largeurs = _largeurs_mm(feuille, colonne_min, colonne_max)
    return Tableau(
        titre_tableau=titre_tableau,
        entetes=[entete] if entete else None,
        lignes=lignes,
        largeurs_mm=largeurs if len(largeurs) == largeur else None,
    )


def _cellules_ligne(
    feuille: Any,
    index_ligne: int,
    colonne_min: int,
    colonne_max: int,
    absorbee: set[tuple[int, int]],
    fusions: dict[tuple[int, int], tuple[int, int]],
) -> list[Cellule] | None:
    """Cellules d'une ligne, ou ``None`` si la ligne n'apporte rien.

    Une ligne entièrement absorbée par une fusion, ou dont tous les textes sont
    vides, ne porte aucune information : elle n'est pas reportée (lignes de
    séparation et reste de feuille Excel disparaissent donc du document).
    """
    cellules: list[Cellule] = []
    vide = True
    for index_colonne in range(colonne_min, colonne_max + 1):
        if (index_ligne, index_colonne) in absorbee:
            continue
        excel = feuille.cell(row=index_ligne, column=index_colonne)
        texte = _texte(excel.value)
        if texte:
            vide = False
        lignes_fusion, colonnes_fusion = fusions.get((index_ligne, index_colonne), (1, 1))
        cellules.append(
            Cellule(
                texte=texte,
                fusion_colonnes=max(1, colonnes_fusion),
                fusion_lignes=max(1, lignes_fusion),
                **_style_cellule(excel),
            )
        )
    return None if vide else cellules


def _plages_fusions(
    feuille: Any, colonne_min: int, colonne_max: int, ligne_min: int, ligne_max: int
) -> tuple[set[tuple[int, int]], dict[tuple[int, int], tuple[int, int]]]:
    """Fusions de la plage : positions absorbées, et dimensions par cellule d'ancrage."""
    absorbee: set[tuple[int, int]] = set()
    fusions: dict[tuple[int, int], tuple[int, int]] = {}
    for plage in feuille.merged_cells.ranges:
        if plage.max_col < colonne_min or plage.min_col > colonne_max:
            continue
        if plage.max_row < ligne_min or plage.min_row > ligne_max:
            continue
        ancre = (plage.min_row, plage.min_col)
        fusions[ancre] = (plage.max_row - plage.min_row + 1, plage.max_col - plage.min_col + 1)
        for index_ligne in range(plage.min_row, plage.max_row + 1):
            for index_colonne in range(plage.min_col, plage.max_col + 1):
                if (index_ligne, index_colonne) != ancre:
                    absorbee.add((index_ligne, index_colonne))
    return absorbee, fusions


def _style_cellule(excel: Any) -> dict[str, Any]:
    """Style exploitable d'une cellule Excel (trame, graisse, couleur, alignement)."""
    style: dict[str, Any] = {}
    if getattr(excel.font, "bold", None):
        style["gras"] = True
    fond = _couleur(getattr(excel.fill, "start_color", None), type_fond=excel.fill)
    if fond:
        style["fond"] = fond
    couleur = _couleur(getattr(excel.font, "color", None))
    if couleur:
        style["couleur_texte"] = couleur
    horizontal = getattr(excel.alignment, "horizontal", None)
    if horizontal in _ALIGNEMENTS_EXCEL:
        style["alignement"] = _ALIGNEMENTS_EXCEL[horizontal]
    return style


_ALIGNEMENTS_EXCEL = {"center": "centre", "right": "droite", "left": "gauche"}


def _largeurs_mm(feuille: Any, colonne_min: int, colonne_max: int) -> list[float]:
    """Largeurs de colonnes déclarées (Excel, en caractères) converties en mm."""
    largeurs: list[float] = []
    for index_colonne in range(colonne_min, colonne_max + 1):
        lettre = feuille.cell(row=1, column=index_colonne).column_letter
        dimension = feuille.column_dimensions.get(lettre)
        valeur = getattr(dimension, "width", None) if dimension is not None else None
        largeurs.append(round(float(valeur) * MM_PAR_CARACTERE, 2) if valeur else 0.0)
    return largeurs


def _couleur(couleur: Any, *, type_fond: Any = None) -> str | None:
    """Couleur openpyxl → ``#RRGGBB``, ou ``None`` (absente, « auto », par défaut)."""
    if type_fond is not None and getattr(type_fond, "patternType", None) in (None, "none"):
        return None
    rgb = getattr(couleur, "rgb", None)
    if not isinstance(rgb, str) or len(rgb) < 6:
        return None
    valeur = rgb[-6:].upper()
    if valeur in ("000000", "FFFFFF", "00000000"):
        return None
    return f"#{valeur}"


def _texte(valeur: Any) -> str:
    """Valeur de cellule → texte affichable (dates au format français)."""
    if valeur is None:
        return ""
    if isinstance(valeur, datetime):
        return valeur.strftime("%d/%m/%Y")
    if isinstance(valeur, date):
        return valeur.strftime("%d/%m/%Y")
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    return str(valeur).strip()
