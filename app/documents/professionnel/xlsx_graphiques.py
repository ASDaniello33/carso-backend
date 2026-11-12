"""Graphiques **Excel natifs** pour les classeurs du moteur professionnel.

Un classeur CARSO ne peut pas se contenter d'une image : un graphique collé en
PNG dans une feuille ne suit pas les données, ne se filtre pas et ne se corrige
pas. Ce module construit de vrais objets Excel (``bar``, ``line``, ``pie``,
``donut``) **liés à des cellules** : chaque graphique reçoit sa propre feuille
de données, et le graphique lit ces cellules par référence.

Constat de recette (11/11) : sur 1 354 classeurs produits par les agents,
**aucun** ne contenait de graphique, et l'outil répondait explicitement
« Paramètre 'graphiques' sans effet sur un classeur xlsx ». Le paramètre
``graphiques`` était donc inutilisable là où un tableur est justement le bon
format pour des statistiques.

Placement : une ancre ``<figure data-graphique="id">`` du corps HTML désigne
l'emplacement. Le graphique est posé sur la **feuille du tableau le plus proche
au-dessus de l'ancre**, à sa droite ; s'il n'y a aucun tableau au-dessus (ou si
la place manque), il ouvre une feuille à son nom. L'agent choisit donc
l'emplacement, le moteur décide de la mécanique.

Aucune donnée n'est inventée : les valeurs viennent de la spec fournie par
l'agent, qui doit elle-même s'appuyer sur des données réelles. Une spec
inutilisable produit un **avertissement** et aucun graphique, jamais un
graphique faux.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openpyxl.chart import BarChart, DoughnutChart, LineChart, PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .themes import Theme

__all__ = ["GraphiqueClasseur", "posers_graphiques", "TYPES_EXCEL"]

#: Types du moteur (``render_chart``) → classe de graphique Excel.
TYPES_EXCEL = {
    "bar": BarChart,
    "line": LineChart,
    "pie": PieChart,
    "donut": DoughnutChart,
}

#: Largeur (cm) et hauteur (cm) par défaut d'un graphique posé dans une feuille.
_LARGEUR_CM, _HAUTEUR_CM = 15.0, 8.5
#: Nombre de colonnes laissées entre le dernier tableau et le graphique.
_MARGE_COLONNES = 2
#: Hauteur (en lignes) sous laquelle on considère la zone libre occupée.
_LIGNES_LIBRES = 18
#: Suffixe de la feuille de données d'un graphique.
_SUFFIXE_DONNEES = " (données)"

#: Teintes de complément, alignées sur les rôles `ok` et `alerte` du renderer
#: (mêmes valeurs que `xlsx_renderer._couleur_texte`, source unique des rôles).
_VERT, _ROUGE = "#059669", "#DC2626"


@dataclass(slots=True)
class GraphiqueClasseur:
    """Un graphique demandé et la feuille où il doit être posé.

    ``feuille`` est le nom du tableau qui le porte (ancre placée juste après
    ce tableau) ou ``None`` quand l'ancre n'est précédée d'aucun tableau : le
    graphique ouvre alors sa propre feuille, à son nom.
    """

    identifiant: str
    spec: Mapping[str, Any]
    feuille: str | None


def _nom_feuille(nom: str, pris: set[str]) -> str:
    """Nom de feuille unique et borné (31 caractères, contrainte Excel)."""
    base = (nom or "Graphique").strip()[:31] or "Graphique"
    candidat, rang = base, 2
    while candidat.lower() in pris:
        suffixe = f" {rang}"
        candidat = f"{base[: 31 - len(suffixe)]}{suffixe}"
        rang += 1
    pris.add(candidat.lower())
    return candidat


def _series(spec: Mapping[str, Any]) -> dict[str, list[float]]:
    """Séries nommées d'une spec, sous la forme ``{nom: [valeurs]}``.

    Le moteur reçoit cette forme depuis ``_specs_graphiques``, mais un appel
    direct peut fournir la forme « outil » (liste de ``{nom, valeurs}``). Les
    deux sont lues : refuser la seconde ferait échouer un usage légitime.
    """
    brut = spec.get("series")
    if isinstance(brut, Mapping):
        return {
            str(nom): [float(valeur) for valeur in valeurs]
            for nom, valeurs in brut.items()
            if valeurs
        }
    series: dict[str, list[float]] = {}
    for entree in brut or []:
        if isinstance(entree, Mapping):
            nom = str(entree.get("nom") or "")
            valeurs = entree.get("valeurs") or []
        else:  # pragma: no cover - objet Pydantic d'un appel direct
            nom = str(getattr(entree, "nom", "") or "")
            valeurs = getattr(entree, "valeurs", []) or []
        if nom and valeurs:
            series[nom] = [float(valeur) for valeur in valeurs]
    return series


def _valeurs_parts(spec: Mapping[str, Any]) -> list[float]:
    """Valeurs d'un camembert/anneau (``values`` ou première série)."""
    brutes = spec.get("values")
    if brutes:
        return [float(valeur) for valeur in brutes]
    series = _series(spec)
    return next(iter(series.values()), [])


def _feuille_donnees(
    wb: Workbook,
    t: Theme,
    spec: Mapping[str, Any],
    libelles: list[str],
    series: dict[str, list[float]],
    valeurs: list[float],
    *,
    nom: str,
) -> SheetDonnees | None:
    """Écrit la feuille de données du graphique et retourne ses références.

    Le graphique lit des cellules : c'est ce qui le rend éditable dans Excel.
    Les libellés occupent la colonne A, chaque série une colonne suivante.
    """
    from openpyxl.styles import Font

    ws = wb.create_sheet(title=nom)
    entete = ws.cell(row=1, column=1, value="Catégorie")
    entete.font = Font(bold=True, color="FF" + t.primary.lstrip("#"))
    libelles_retenus = [str(libelle) for libelle in libelles]
    for rang, libelle in enumerate(libelles_retenus, start=2):
        ws.cell(row=rang, column=1, value=libelle)
    if series:
        for index, (nom_serie, valeurs_serie) in enumerate(series.items(), start=2):
            cellule = ws.cell(row=1, column=index, value=nom_serie)
            cellule.font = Font(bold=True, color="FF" + t.primary.lstrip("#"))
            for rang, valeur in enumerate(valeurs_serie, start=2):
                ws.cell(row=rang, column=index, value=valeur)
        nb_colonnes = 1 + len(series)
    else:
        entete_valeur = ws.cell(row=1, column=2, value=str(spec.get("title") or "Valeur"))
        entete_valeur.font = Font(bold=True, color="FF" + t.primary.lstrip("#"))
        for rang, valeur in enumerate(valeurs, start=2):
            ws.cell(row=rang, column=2, value=valeur)
        nb_colonnes = 2
    ws.column_dimensions["A"].width = 28
    for index in range(2, nb_colonnes + 1):
        ws.column_dimensions[get_column_letter(index)].width = 16
    return SheetDonnees(
        feuille=ws.title,
        nb_lignes=max(1, len(libelles_retenus)),
        nb_series=max(1, nb_colonnes - 1),
    )


@dataclass(slots=True)
class SheetDonnees:
    """Références de la plage écrite pour un graphique."""

    feuille: str
    nb_lignes: int
    nb_series: int


def _probleme(spec: Mapping[str, Any]) -> str | None:
    """Raison de ne pas dessiner ce graphique, ou ``None`` s'il est valide."""
    type_graphique = str(spec.get("type") or "").strip().lower()
    if type_graphique not in TYPES_EXCEL:
        return (
            f"type de graphique inconnu {spec.get('type')!r} : types acceptés "
            f"{', '.join(sorted(TYPES_EXCEL))}."
        )
    libelles = spec.get("labels") or []
    if not libelles:
        return "aucun label : un graphique Excel a besoin de ses catégories."
    if type_graphique in ("bar", "line"):
        series = _series(spec)
        if not series:
            return (
                "aucune série de valeurs : renseigner 'series' "
                "({nom: [valeurs]}) pour un graphique à axes."
            )
        longueurs = {len(valeurs) for valeurs in series.values()}
        if longueurs != {len(libelles)}:
            return (
                f"données désalignées : {len(libelles)} label(s) et des séries de "
                f"longueur(s) {sorted(longueurs)}."
            )
        return None
    valeurs = _valeurs_parts(spec)
    if not valeurs:
        return "aucune valeur : renseigner 'values' pour un camembert ou un anneau."
    if len(valeurs) != len(libelles):
        return (
            f"données désalignées : {len(libelles)} label(s) et "
            f"{len(valeurs)} valeur(s)."
        )
    if any(valeur < 0 for valeur in valeurs):
        return "un camembert n'accepte pas de valeur négative."
    if sum(valeurs) <= 0:
        return "la somme des valeurs est nulle : rien à représenter."
    return None


def _adapter_chart(chart: Any, spec: Mapping[str, Any], t: Theme) -> None:
    """Titres, légende et étiquettes de données, dans le vocabulaire CARSO."""
    chart.title = str(spec.get("title") or spec.get("id") or "Graphique")
    chart.style = 2
    type_graphique = str(spec.get("type") or "").strip().lower()
    # Un camembert se lit par ses parts : on affiche les pourcentages, comme
    # dans le rendu SVG. À axes, l'échelle et la grille suffisent.
    if type_graphique in ("pie", "donut"):
        chart.dataLabels = DataLabelList()
        chart.dataLabels.showPercent = True
        chart.legend.position = "r"
    else:
        chart.legend.position = "b"
        if str(spec.get("y_label") or "").strip():
            chart.y_axis.title = str(spec["y_label"]).strip()
    if str(spec.get("x_label") or "").strip():
        chart.x_axis.title = str(spec["x_label"]).strip()
    # Couleurs du thème : le classeur reste au nom de CARSO, sans que l'agent
    # choisisse une couleur arbitraire. Le thème n'expose que quatre teintes,
    # les séries au-delà reprennent le cycle plutôt qu'une couleur inventée.
    couleurs = (t.primary, t.accent, _VERT, _ROUGE)
    for index, serie in enumerate(chart.series):
        teinte = couleurs[index % len(couleurs)]
        if type_graphique in ("bar", "line"):
            serie.graphicalProperties.solidFill = teinte.lstrip("#")
            serie.graphicalProperties.line.solidFill = teinte.lstrip("#")


def _plage_libre(ws: Worksheet) -> tuple[int, int]:
    """Colonne (1-based) et ligne où poser un graphique sur cette feuille.

    Le graphique se place à droite des données : après la dernière colonne
    utilisée, plus une marge. Il ne recouvre donc jamais un tableau existant.
    """
    derniere_colonne = max((cellule.column for cellule in ws[1] if cellule.value), default=0)
    colonne = max(1, derniere_colonne) + _MARGE_COLONNES
    return colonne, 2


def posers_graphiques(
    wb: Workbook,
    t: Theme,
    graphiques: list[GraphiqueClasseur],
    avertissements: list[str],
) -> int:
    """Pose chaque graphique dans son classeur et retourne le nombre posé.

    Args:
        wb: classeur en construction.
        t: thème CARSO (couleurs).
        graphiques: graphiques à poser, avec la feuille qui les porte.
        avertissements: liste où sont consignés les refus et déplacements.

    Returns:
        Le nombre de graphiques réellement posés.
    """
    poses = 0
    pris = {nom.lower() for nom in wb.sheetnames}
    for graphique in graphiques:
        spec = graphique.spec
        probleme = _probleme(spec)
        if probleme:
            avertissements.append(
                f"Graphique {graphique.identifiant!r} non posé : {probleme}"
            )
            continue
        # La feuille de données est TOUJOURS à part : le graphique ne peut pas
        # lire les cellules fusionnées d'un tableau stylé (une fusion casse la
        # référence séquentielle des labels), et une feuille dédiée reste
        # éditable sans risquer d'abîmer la mise en forme du tableau.
        libelles = [str(libelle) for libelle in spec.get("labels") or []]
        series = _series(spec) if str(spec.get("type")).lower() in ("bar", "line") else {}
        valeurs = [] if series else _valeurs_parts(spec)
        nom_donnees = _nom_feuille(
            f"{graphique.identifiant}{_SUFFIXE_DONNEES}", pris
        )
        donnees = _feuille_donnees(
            wb, t, spec, libelles, series, valeurs, nom=nom_donnees
        )
        if donnees is None:  # pragma: no cover - _feuille_donnees crée toujours
            continue
        ws_donnees = wb[donnees.feuille]

        classe = TYPES_EXCEL[str(spec.get("type")).strip().lower()]
        chart = classe()
        if str(spec.get("type")).strip().lower() == "bar":
            chart.type = "col"
            chart.grouping = "clustered"
        lignes = donnees.nb_lignes
        if series:
            ref_categories = Reference(
                ws_donnees, min_col=1, min_row=2, max_row=1 + lignes
            )
            ref_valeurs = Reference(
                ws_donnees,
                min_col=2,
                max_col=1 + donnees.nb_series,
                min_row=1,
                max_row=1 + lignes,
            )
            chart.add_data(ref_valeurs, titles_from_data=True)
            chart.set_categories(ref_categories)
        else:
            ref_categories = Reference(
                ws_donnees, min_col=1, min_row=2, max_row=1 + lignes
            )
            ref_valeurs = Reference(
                ws_donnees, min_col=2, min_row=1, max_row=1 + lignes
            )
            chart.add_data(ref_valeurs, titles_from_data=True)
            chart.set_categories(ref_categories)

        _adapter_chart(chart, spec, t)
        chart.width = _LARGEUR_CM
        chart.height = _HAUTEUR_CM

        if graphique.feuille and graphique.feuille in wb.sheetnames:
            cible = wb[graphique.feuille]
            colonne, ligne = _plage_libre(cible)
        else:
            # Aucun tableau au-dessus de l'ancre : le graphique ouvre sa propre
            # feuille plutôt que d'être perdu ou posé au hasard.
            cible = wb.create_sheet(title=_nom_feuille(graphique.identifiant, pris))
            colonne, ligne = 1, 1
            avertissements.append(
                f"Graphique {graphique.identifiant!r} : aucun tableau au-dessus "
                "de son ancre, il ouvre sa propre feuille."
            )
        cible.add_chart(chart, f"{get_column_letter(colonne)}{ligne}")
        poses += 1
    return poses
