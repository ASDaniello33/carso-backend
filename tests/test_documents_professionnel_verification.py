"""Palettes et contrôles qualité du générateur professionnel (repris du module).

Le moteur vendu sous ``app.documents.professionnel`` est celui du module
``generation-document`` : ses deux garde-fous les plus utiles sont ici testés
depuis CARSO, pour qu'une modification du thème ou des règles d'avertissement ne
passe pas inaperçue.

- **palettes** : trois thèmes, couleurs hexadécimales, repli ``corporate`` ;
- **avertissements** : un document propre n'en produit aucun, chaque défaut connu
  (``<style>``, flex/grid, style en ligne, Markdown, placeholder, absence de
  ``<h1>``, formules creuses, tableau trop large sans ``<thead>``, image distante
  en DOCX) en produit un ;
- **CSS** : la feuille inlinée (``theme_css.py``) reste alignée sur
  ``assets/theme.css`` du module source — garde-fou de dérive, déclaré ignoré
  quand le module source n'est pas présent à côté du backend.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.documents.professionnel import THEMES, Theme, get_theme, verifier
from app.documents.professionnel.theme_css import THEME_CSS

HEX = re.compile(r"^#[0-9A-F]{6}$")

#: Feuille du module source. La copie CARSO est inlinée (``theme_css.py``) : ce
#: test est le garde-fou de dérive, tant que le module source vit dans le dépôt.
SOURCE_THEME = (
    Path(__file__).resolve().parents[2]
    / "GenerationDocument"
    / "src"
    / "generation_document"
    / "assets"
    / "theme.css"
)
sans_module_source = pytest.mark.skipif(
    not SOURCE_THEME.exists(),
    reason=f"module source generation-document absent : {SOURCE_THEME}",
)


def _normaliser(css: str) -> str:
    """CSS comparable : commentaires retirés, espaces réduits à un seul."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\s+", " ", css).strip()

HTML_COMPLET = """
<section class="cover">
  <div class="cover-label">Rapport d'analyse</div>
  <h1 class="cover-title">Bilan commercial 2026</h1>
  <p class="cover-subtitle">Performance des ventes et recommandations</p>
  <div class="cover-meta">Préparé pour <strong>Client SA</strong> · Octobre 2026</div>
</section>
<nav class="toc"><h2>Sommaire</h2>
  <a href="#synthese">Synthèse</a>
  <a href="#ventes">Ventes par produit</a>
</nav>
<div class="page-break"></div>
<h1 id="synthese">1. Synthèse</h1>
<p class="lead">Une année de croissance portée par les licences Pro.</p>
<table class="kpi"><tr>
  <td><span class="kpi-value">+24 %</span><span class="kpi-label">Croissance CA</span></td>
  <td><span class="kpi-value">1 250</span><span class="kpi-label">Clients actifs</span></td>
</tr></table>
<div class="callout success"><strong>Recommandation :</strong> renforcer l'équipe commerciale.</div>
<h1 id="ventes">2. Ventes par produit</h1>
<table class="data">
  <thead><tr><th>Produit</th><th class="num">Quantité</th><th class="num">Montant</th></tr></thead>
  <tbody>
    <tr><td>Licence Pro</td><td class="num">12</td><td class="num">4 800 €</td></tr>
    <tr><td>Support</td><td class="num">5</td><td class="num">1 500 €</td></tr>
  </tbody>
  <tfoot><tr><td>Total</td><td></td><td class="num">6 300 €</td></tr></tfoot>
</table>
<ul><li>Point un</li><li>Point deux</li></ul>
"""


def _contient(avertissements: list[str], morceau: str) -> bool:
    return any(morceau in avertissement for avertissement in avertissements)


# --- Palettes -----------------------------------------------------------------


def test_trois_themes_disponibles() -> None:
    assert set(THEMES) == {"corporate", "moderne", "nature"}


@pytest.mark.parametrize("nom", list(THEMES))
def test_couleurs_hexadecimales(nom: str) -> None:
    theme = THEMES[nom]
    for couleur in (theme.primary, theme.accent, theme.soft, theme.callout):
        assert HEX.match(couleur), couleur


def test_get_theme_insensible_casse_et_espaces() -> None:
    assert get_theme("  MODERNE ").nom == "moderne"


@pytest.mark.parametrize("valeur", [None, "", "inconnu"])
def test_get_theme_repli_corporate(valeur: str | None) -> None:
    assert get_theme(valeur).nom == "corporate"


def test_get_theme_accepte_un_objet_theme() -> None:
    perso = Theme("perso", "#000000", "#111111", "#222222", "#333333")
    assert get_theme(perso) is perso


@sans_module_source
def test_theme_css_aligne_sur_le_module_source() -> None:
    """La copie inlinée ne dérive pas de la feuille du module source.

    Le module d'origine lisait ``assets/theme.css`` au moment du rendu ; CARSO
    l'inline dans ``theme_css.py`` (aucun fichier de données embarqué). Tant que
    le module source est présent dans le dépôt, ce test compare les deux feuilles
    à espaces et commentaires près — une retouche d'un seul côté ne passe pas
    inaperçue.
    """
    source = SOURCE_THEME.read_text(encoding="utf-8")

    assert _normaliser(THEME_CSS) == _normaliser(source)


# --- Avertissements -----------------------------------------------------------


def test_document_propre_sans_avertissement() -> None:
    assert verifier(HTML_COMPLET) == []


def test_contenu_vide() -> None:
    assert verifier("   ") == ["Contenu HTML vide."]


def test_balises_style_et_script() -> None:
    avertissements = verifier("<style>p{}</style><script>alert(1)</script><h1>T</h1>")

    assert _contient(avertissements, "<style>")
    assert _contient(avertissements, "<script>")


@pytest.mark.parametrize(
    "css",
    ["display:flex", "display: grid", "position:absolute", "float: left"],
)
def test_mise_en_page_interdite(css: str) -> None:
    avertissements = verifier(f'<h1>T</h1><div style="{css}">x</div>')

    assert _contient(avertissements, "flex/grid")


def test_style_inline_couleur() -> None:
    assert _contient(verifier('<h1>T</h1><p style="color:red">x</p>'), "Styles inline")


def test_markdown_detecte() -> None:
    assert _contient(verifier("<h1>T</h1><p>Un **gras** markdown</p>"), "Markdown")


@pytest.mark.parametrize("texte", ["[Nom du client]", "XXXX", "Lorem ipsum dolor"])
def test_placeholders(texte: str) -> None:
    assert _contient(verifier(f"<h1>T</h1><p>{texte}</p>"), "Placeholder")


def test_absence_de_h1() -> None:
    assert _contient(verifier("<p>Texte</p>"), "Aucun titre <h1>")


def test_formules_creuses_et_restes_assistant() -> None:
    avertissements = verifier(
        "<h1>T</h1><p>Bien sûr ! Il est important de noter que ceci est vrai.</p>"
    )

    assert _contient(avertissements, "Formules creuses")
    assert _contient(avertissements, "Restes de conversation")


def test_tableau_trop_large_en_portrait() -> None:
    entetes = "".join(f"<th>C{i}</th>" for i in range(8))
    html = f'<h1>T</h1><table class="data"><thead><tr>{entetes}</tr></thead></table>'

    assert _contient(verifier(html), "8 colonnes")
    assert not _contient(verifier(html, orientation="paysage"), "colonnes")


def test_tableau_sans_thead() -> None:
    html = '<h1>T</h1><table class="data"><tr><td>a</td></tr></table>'

    assert _contient(verifier(html), "sans <thead>")


def test_image_distante_en_docx_uniquement() -> None:
    html = '<h1>T</h1><img src="https://exemple.com/a.png" alt="a">'

    assert _contient(verifier(html, "docx"), "Image distante")
    assert not _contient(verifier(html, "pdf"), "Image distante")
