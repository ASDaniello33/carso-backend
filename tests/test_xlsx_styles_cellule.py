"""Styles de cellule du classeur professionnel (format='xlsx').

Demande d'exploitation : le générateur de document professionnel doit exposer
la **couleur de texte**, le **fond**, la **bordure**, la **taille de police**,
la **largeur de colonne** et les **fusions**. Le fond, la largeur et les
fusions existaient ; cet incrément ajoute ``data-couleur``, ``data-taille``,
``data-bordure`` et ``data-bordure-couleur``, et les honore aussi sur les
cellules d'en-tête (``<th>``), qui les ignoraient.

Règle tenue : l'agent nomme un **rôle** (``accent``, ``alerte``…), le thème
décide la couleur. Un rôle inconnu ou une taille hors bornes ne casse pas le
rendu : l'attribut est ignoré (ou borné) et remonte en avertissement — rien
n'est deviné en silence.

Mise en page d'une feuille issue d'un ``table.data`` : ligne 1 = titre fusionné
sur la largeur du tableau, ligne 2 = en-têtes, lignes suivantes = corps.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.documents.professionnel.themes import BORDURE, TEXTE, get_theme
from app.documents.professionnel.xlsx_renderer import rendre_xlsx

TABLEAU = """
<h1>Budget mission</h1>
<table class="data">
  <thead>
    <tr>
      <th data-largeur="30" data-fond="primaire" data-gras="1">Poste</th>
      <th class="num" data-couleur="accent" data-taille="13">Montant</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Formation</td>
      <td class="num" data-format="devise:MGA"
          data-bordure="moyenne" data-bordure-couleur="alerte">1 250 000</td>
    </tr>
    <tr class="total">
      <td>Total</td>
      <td class="num" data-format="devise:MGA" data-couleur="blanc"
          data-fond="primaire" data-gras="1">1 250 000</td>
    </tr>
    <tr>
      <td colspan="2" data-couleur="ok" data-taille="15"
          data-bordure="epaisse">Sous-total vérifié</td>
    </tr>
  </tbody>
</table>
"""


def _rendre(html: str, tmp_path: Path) -> tuple[Worksheet, list[str]]:
    """Rend le classeur et le relit avec openpyxl (le fichier fait foi)."""
    avis: list[str] = []
    chemin = tmp_path / "essai.xlsx"
    rendre_xlsx(html, chemin, titre="Budget", avertissements=avis)
    return load_workbook(io.BytesIO(chemin.read_bytes())).active, avis


def _couleur(cellule: object) -> str | None:
    """Couleur de police au format openpyxl (``AARRGGBB``)."""
    return cellule.font.color.rgb if cellule.font.color is not None else None


def test_couleur_de_texte_par_role(tmp_path: Path) -> None:
    feuille, avis = _rendre(TABLEAU, tmp_path)
    theme = get_theme("corporate")

    # En-tête : rôle accent déclaré, il l'emporte sur le blanc par défaut.
    assert _couleur(feuille["B2"]) == "00" + theme.accent.lstrip("#").upper()
    # En-tête sans rôle : blanc sur le fond primaire, comportement historique.
    assert _couleur(feuille["A2"]) == "00FFFFFF"
    # Corps : rôle blanc, rôle « ok », puis texte par défaut.
    assert _couleur(feuille["B4"]) == "00FFFFFF"
    assert _couleur(feuille["A5"]) == "00059669"
    assert _couleur(feuille["A3"]) == "00" + TEXTE.lstrip("#").upper()
    assert avis == []


def test_taille_de_police(tmp_path: Path) -> None:
    feuille, avis = _rendre(TABLEAU, tmp_path)
    assert feuille["B2"].font.sz == 13  # en-tête, taille déclarée
    assert feuille["A5"].font.sz == 15  # corps, taille déclarée
    assert feuille["A3"].font.sz == 11  # défaut : le tableau reste cohérent
    assert feuille["A2"].font.sz is None  # en-tête sans taille : défaut Excel
    assert avis == []


def test_bordure_explicite(tmp_path: Path) -> None:
    feuille, avis = _rendre(TABLEAU, tmp_path)
    bordure = feuille["B3"].border
    assert bordure.left.style == "medium"
    assert bordure.right.style == "medium"
    assert bordure.top.style == "medium"
    assert bordure.bottom.style == "medium"
    assert bordure.left.color.rgb == "00DC2626"  # rôle alerte
    assert feuille["A5"].border.top.style == "thick"
    assert avis == []


def test_bordure_sans_couleur_suit_le_theme(tmp_path: Path) -> None:
    html = (
        '<table class="data"><thead><tr><th>A</th></tr></thead>'
        '<tbody><tr><td data-bordure="fine">x</td></tr></tbody></table>'
    )
    feuille, _ = _rendre(html, tmp_path)
    assert feuille["A3"].border.left.style == "thin"
    assert feuille["A3"].border.left.color.rgb == "00" + BORDURE.lstrip("#").upper()


def test_largeur_et_fusion(tmp_path: Path) -> None:
    feuille, _ = _rendre(TABLEAU, tmp_path)
    # Largeur déclarée sur l'en-tête — elle était ignorée avant cet incrément.
    assert feuille.column_dimensions["A"].width == 30
    fusions = {str(plage) for plage in feuille.merged_cells.ranges}
    assert "A1:B1" in fusions  # titre du document
    assert "A5:B5" in fusions  # colspan="2"


def test_role_inconnu_averti_et_ignore(tmp_path: Path) -> None:
    html = (
        '<table class="data"><thead><tr><th>A</th><th>B</th></tr></thead><tbody>'
        '<tr><td data-couleur="violet">x</td><td data-taille="40">y</td></tr>'
        "</tbody></table>"
    )
    feuille, avis = _rendre(html, tmp_path)
    assert _couleur(feuille["A3"]) == "00" + TEXTE.lstrip("#").upper()  # inchangé
    assert feuille["B3"].font.sz == 20  # borné, pas 40
    texte = " ".join(avis)
    assert "data-couleur='violet'" in texte
    assert "data-taille='40'" in texte


def test_taille_non_numerique_avertie(tmp_path: Path) -> None:
    html = (
        '<table class="data"><thead><tr><th>A</th></tr></thead>'
        '<tbody><tr><td data-taille="grand">x</td></tr></tbody></table>'
    )
    feuille, avis = _rendre(html, tmp_path)
    assert feuille["A3"].font.sz == 11
    assert any("data-taille" in constat for constat in avis)


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [("aucune", None), ("fine", "thin"), ("moyenne", "medium"), ("epaisse", "thick")],
)
def test_epaisseurs_acceptees(
    tmp_path: Path, valeur: str, attendu: str | None
) -> None:
    html = (
        '<table class="data"><thead><tr><th>A</th></tr></thead>'
        f'<tbody><tr><td data-bordure="{valeur}">x</td></tr></tbody></table>'
    )
    feuille, avis = _rendre(html, tmp_path)
    cote = feuille["A3"].border.left
    assert (cote.style if cote is not None else None) == attendu
    assert not any("data-bordure" in constat for constat in avis)
