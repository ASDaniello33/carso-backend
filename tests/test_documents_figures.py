"""Figures SVG dans **tous** les outils de documents (INC-123).

Ce que ces tests verrouillent, dans l'ordre de la demande :

1. **spec** — deux nouveaux blocs (``graphique``, ``illustration``) et une
   troisième source d'image (``svg``), avec des refus explicites : une variante
   sans ses données, des séries désalignées, deux sources d'image à la fois ;
2. **brique partagée** (``app.documents.figures``) — le SVG vient du serveur,
   la rasterisation produit un **vrai PNG**, et l'absence de ``resvg-py`` lève
   une erreur nommée (jamais une figure manquante en silence) ;
3. **quatre renderers** — un même ``DocumentSpec`` donne : SVG vectoriel en
   HTML, PNG dans le DOCX, image dans le PDF, image ancrée dans le XLSX ;
4. **``generer_document_html``** — le ``<svg>`` n'est **plus supprimé** (deux
   images dans le DOCX, deux images dans le PDF) et les ancres
   ``data-graphique`` / ``data-illustration`` remplissent la figure du serveur,
   avec refus d'une ancre inconnue ou d'une figure sans ancre ;
5. **sécurité** — un SVG d'agent porteur de ``<script>`` ou d'une référence
   distante ressort nettoyé avant d'atteindre un renderer.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image as LireImage
from pydantic import ValidationError as PydanticValidationError
from pypdf import PdfReader

from app.core.errors import GenerationError, ValidationError
from app.documents.figures import (
    png_depuis_svg,
    svg_depuis_bloc,
    svg_graphique,
    svg_illustration,
)
from app.documents.generation_html import convertir_html_bytes
from app.documents.renderers.docx import rendre_docx
from app.documents.renderers.html import rendre_html_texte
from app.documents.renderers.pdf import rendre_pdf
from app.documents.renderers.xlsx import rendre_xlsx
from app.documents.spec import (
    DocumentSpec,
    Graphique,
    Illustration,
    Image,
    Metadonnees,
    bloc_par_type,
)

SVG_AGENT = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 40" width="160" '
    'height="40"><rect width="160" height="40" fill="#EEF2FF"/>'
    '<text x="8" y="26" font-size="13">Schéma agent</text></svg>'
)

HTML_AVEC_SVG = f"""<html><body>
<h1>Note</h1>
<p>Un schéma dessiné par l'agent :</p>
{SVG_AGENT}
<p>Fin.</p>
</body></html>"""


def _spec() -> DocumentSpec:
    """Document de référence : un graphique, une illustration, un SVG d'agent."""
    return DocumentSpec(
        metadata=Metadonnees(titre="Rapport avec figures"),
        blocs=[
            {"type": "titre", "niveau": 1, "texte": "Résultats"},
            {
                "type": "graphique",
                "variante": "bar",
                "titre": "CA par trimestre",
                "labels": ["T1", "T2", "T3"],
                "series": [{"nom": "2025", "valeurs": [10, 25, 18]}],
                "legende": "Figure 1 – CA par trimestre",
            },
            {
                "type": "illustration",
                "variante": "etapes",
                "titre": "Notre démarche",
                "items": [
                    {"titre": "Écoute", "texte": "Analyse du besoin", "icone": "cible"},
                    {"titre": "Conception", "texte": "Ingénierie", "icone": "ampoule"},
                    {"titre": "Évaluation", "texte": "Mesure", "icone": "check"},
                ],
                "legende": "Figure 2 – Démarche",
            },
            {"type": "image", "svg": SVG_AGENT, "legende": "Figure 3 – Schéma"},
        ],
    )


# --- 1. Contrats du spec ------------------------------------------------------


def test_bloc_graphique_exige_les_donnees_de_sa_variante() -> None:
    """Un graphique sans chiffres est refusé à la construction, pas au rendu."""
    with pytest.raises(PydanticValidationError, match="series"):
        bloc_par_type({"type": "graphique", "variante": "bar", "labels": ["A"]})
    with pytest.raises(PydanticValidationError, match="valeurs"):
        bloc_par_type({"type": "graphique", "variante": "pie", "labels": ["A"]})


def test_bloc_graphique_refuse_des_series_desalignees() -> None:
    with pytest.raises(PydanticValidationError, match="valeur"):
        Graphique(
            variante="bar",
            labels=["A", "B"],
            series=[{"nom": "2025", "valeurs": [1]}],
        )


def test_bloc_image_accepte_une_seule_source() -> None:
    """document_id, chemin et svg sont exclusifs : deux sources, erreur dite."""
    with pytest.raises(PydanticValidationError, match="une seule source"):
        Image(svg=SVG_AGENT, chemin="modele/logo.png")
    # Une seule source reste valide.
    assert Image(svg=SVG_AGENT).svg == SVG_AGENT


def test_bloc_illustration_porte_ses_items() -> None:
    bloc = bloc_par_type(
        {"type": "illustration", "variante": "cycle", "items": [{"titre": "A"}]}
    )
    assert isinstance(bloc, Illustration)
    assert bloc.items[0].titre == "A"


# --- 2. Brique partagée -------------------------------------------------------


def test_graphique_deterministe_et_echappe() -> None:
    """Même spec, mêmes octets ; un libellé HTML ne sort jamais en balise."""
    spec = {
        "type": "bar",
        "labels": ["<script>alert(1)</script>", "B"],
        "series": {"2025": [1, 2]},
        "title": "T",
    }
    premier = svg_graphique(spec)
    assert premier == svg_graphique(spec)
    assert "<script>" not in premier
    assert "&lt;script&gt;" in premier


def test_illustration_svg_puis_png() -> None:
    bloc = Illustration(
        variante="icones",
        items=[{"titre": "Qualité", "icone": "cible"}, {"titre": "Proximité", "icone": "globe"}],
    )
    svg = svg_illustration({"type": "icones", "items": [
        {"titre": "Qualité", "icone": "cible"},
        {"titre": "Proximité", "icone": "globe"},
    ]})
    assert "<svg" in svg
    octets, largeur, hauteur = png_depuis_svg(svg)
    assert octets[:4] == b"\x89PNG"
    # Échelle 2× : la taille déclarée est bien doublée.
    assert largeur == 1280
    assert hauteur > 0
    assert svg_depuis_bloc(bloc) == svg


def test_sans_resvg_l_erreur_nomme_la_dependance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans resvg, une figure lève une erreur actionnable — jamais un livrable muet."""
    import app.documents.professionnel.rasterisation as rasterisation

    monkeypatch.setattr(rasterisation, "rasterisation_disponible", lambda: False)
    with pytest.raises(GenerationError, match="resvg-py"):
        png_depuis_svg(SVG_AGENT)


def test_svg_d_agent_assaini_avant_rendu() -> None:
    """Un SVG d'agent est nettoyé : script et référence distante ne passent pas."""
    malveillant = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        "<script>alert(1)</script>"
        '<image href="http://exemple.test/x.png" width="10" height="10"/>'
        '<animate attributeName="x" dur="1s"/></svg>'
    )
    nettoye = svg_depuis_bloc(Image(svg=malveillant))
    assert "<script" not in nettoye
    assert "animate" not in nettoye.lower()
    assert "http://exemple.test" not in nettoye


# --- 3. Les quatre renderers --------------------------------------------------


def test_html_garde_le_svg_vectoriel() -> None:
    html = rendre_html_texte(_spec())
    assert html.count("<svg") == 3
    assert "Figure 1 – CA par trimestre" in html


def test_docx_embarque_les_trois_figures_en_png() -> None:
    octets = rendre_docx(_spec())
    with zipfile.ZipFile(io.BytesIO(octets)) as archive:
        medias = [n for n in archive.namelist() if n.startswith("word/media/")]
        contenu = b"".join(
            archive.read(n) for n in archive.namelist() if n.endswith(".xml")
        ).decode("utf-8", "ignore")
    assert len(medias) == 3
    # Le texte du SVG ne doit pas se répandre en paragraphes (corruption connue).
    assert "Schéma agent" not in contenu
    assert "Figure 3 – Schéma" in contenu


def test_pdf_insere_les_figures_comme_images() -> None:
    lecture = PdfReader(io.BytesIO(rendre_pdf(_spec())))
    images = sum(len(page.images) for page in lecture.pages)
    assert images == 3
    texte = "\n".join(page.extract_text() or "" for page in lecture.pages)
    assert "Figure 1 - CA par trimestre" in texte or "Figure 1 – CA par trimestre" in texte


def test_xlsx_ancre_les_figures_dans_la_feuille() -> None:
    import openpyxl

    classeur = openpyxl.load_workbook(io.BytesIO(rendre_xlsx(_spec())))
    feuille = classeur.worksheets[0]
    assert len(feuille._images) == 3  # noqa: SLF001 - inspection du classeur produit


def test_image_raster_sans_svg_reste_signalee_dans_le_classeur() -> None:
    """Comportement conservé : une image de stockage n'est pas inventée en PNG."""
    spec = DocumentSpec(
        metadata=Metadonnees(titre="Classeur"),
        blocs=[{"type": "image", "chemin": "modele/photo.png", "legende": "Photo"}],
    )
    import openpyxl

    classeur = openpyxl.load_workbook(io.BytesIO(rendre_xlsx(spec)))
    feuille = classeur.worksheets[0]
    valeurs = [cellule.value for ligne in feuille.iter_rows() for cellule in ligne]
    assert any("image non reportée" in str(valeur) for valeur in valeurs)


# --- 4. generer_document_html : SVG conservé + ancres -------------------------


def test_html_vers_docx_conserve_le_svg() -> None:
    """Le défaut mesuré avant l'INC-123 : un `<svg>` disparaissait du DOCX."""
    octets = convertir_html_bytes(HTML_AVEC_SVG, format="docx")
    with zipfile.ZipFile(io.BytesIO(octets)) as archive:
        medias = [n for n in archive.namelist() if n.startswith("word/media/")]
        contenu = b"".join(
            archive.read(n) for n in archive.namelist() if n.endswith(".xml")
        ).decode("utf-8", "ignore")
    assert len(medias) == 1
    assert "Schéma agent" not in contenu  # pas répandu en paragraphes


def test_html_vers_pdf_conserve_le_svg() -> None:
    lecture = PdfReader(io.BytesIO(convertir_html_bytes(HTML_AVEC_SVG, format="pdf")))
    assert sum(len(page.images) for page in lecture.pages) == 1


def test_html_ancres_de_figures_du_serveur() -> None:
    html = (
        "<html><body><h1>Note</h1><p>Voir :</p>"
        '<figure data-graphique="ca"></figure>'
        '<figure data-illustration="dem"></figure>'
        "</body></html>"
    )
    octets = convertir_html_bytes(
        html,
        format="docx",
        graphiques={
            "ca": {
                "type": "bar",
                "labels": ["T1", "T2"],
                "series": {"2025": [1, 2]},
                "title": "CA",
                "legende": "Figure 1 – CA",
            }
        },
        illustrations={
            "dem": {
                "type": "etapes",
                "titre": "Démarche",
                "items": [{"titre": "A"}, {"titre": "B"}],
                "legende": "Figure 2 – Démarche",
            }
        },
    )
    with zipfile.ZipFile(io.BytesIO(octets)) as archive:
        medias = [n for n in archive.namelist() if n.startswith("word/media/")]
    assert len(medias) == 2


def test_html_ancre_inconnue_refusee() -> None:
    html = '<html><body><figure data-graphique="absente"></figure></body></html>'
    with pytest.raises(ValidationError, match="inconnu"):
        convertir_html_bytes(html, format="docx", graphiques={"ca": {
            "type": "bar", "labels": ["A"], "series": {"2025": [1]}
        }})


def test_html_graphique_declare_sans_ancre_refuse() -> None:
    """Un graphique déclaré mais pas placé est une erreur, pas un oubli muet."""
    with pytest.raises(ValidationError, match="sans ancre"):
        convertir_html_bytes(
            HTML_AVEC_SVG,
            format="docx",
            graphiques={"ca": {
                "type": "bar", "labels": ["A"], "series": {"2025": [1]}
            }},
        )


def test_image_embarquee_lisible_dans_le_pdf() -> None:
    """Une image data-URI du HTML devient un bloc image du PDF (pas perdue)."""
    tampon = io.BytesIO()
    LireImage.new("RGB", (40, 20), "#3366cc").save(tampon, format="PNG")
    import base64

    source = base64.b64encode(tampon.getvalue()).decode("ascii")
    html = (
        "<html><body><h1>Note</h1>"
        f'<figure><img src="data:image/png;base64,{source}" width="120">'
        "<figcaption>Figure 1 – Bandeau</figcaption></figure></body></html>"
    )
    lecture = PdfReader(io.BytesIO(convertir_html_bytes(html, format="pdf")))
    assert sum(len(page.images) for page in lecture.pages) == 1
    texte = "\n".join(page.extract_text() or "" for page in lecture.pages)
    assert "Figure 1" in texte


# --- 6. Plafond de rasterisation (abort Rust évité) ---------------------------
#
# Contexte : un SVG d'agent déclarant sa taille entre **guillemets simples**
# échappait à la garde de `taille_svg`, qui ne lisait que les guillemets doubles.
# resvg rendait alors réellement 88 000 × 102 400 px, soit 9 011 200 000 pixels
# → 36 044 800 000 octets de pixmap ; l'échec d'allocation tuait le processus
# backend (abort Rust, aucune exception Python attrapable).


def test_taille_svg_lit_les_deux_types_de_guillemets() -> None:
    from app.documents.professionnel.rasterisation import taille_svg

    double = '<svg width="9000" height="4000"><rect/></svg>'
    simple = "<svg width='9000' height='4000'><rect/></svg>"
    scientifique = '<svg width="9e3" height="4000px"><rect/></svg>'

    assert taille_svg(double) == (9000.0, 4000.0)
    assert taille_svg(simple) == (9000.0, 4000.0)
    assert taille_svg(scientifique) == (9000.0, 4000.0)


def test_taille_svg_lit_le_viewbox_quel_que_soit_le_guillemet() -> None:
    from app.documents.professionnel.rasterisation import taille_svg

    assert taille_svg("<svg viewBox='0 0 1200 630'><rect/></svg>") == (1200.0, 630.0)
    assert taille_svg('<svg viewBox="0 0 1200 630"><rect/></svg>') == (1200.0, 630.0)


def test_taille_svg_ne_lit_pas_les_elements_enfants() -> None:
    """La taille est celle de la racine, pas du premier ``<rect>`` rencontré."""
    from app.documents.professionnel.rasterisation import taille_svg

    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="999999" height="999999"/></svg>'

    assert taille_svg(svg) == (640.0, 400.0)


def test_rasterisation_refuse_un_svg_demesure_sans_appeler_resvg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le refus tombe **avant** resvg : aucune allocation géante n'est tentée."""
    import app.documents.professionnel.rasterisation as rasterisation

    def interdit():
        raise AssertionError("resvg ne doit pas être appelé sur un SVG démesuré")

    monkeypatch.setattr(rasterisation, "_moteur", interdit)

    with pytest.raises(rasterisation.RasterisationIndisponible, match="trop grand"):
        rasterisation.rasteriser_svg(
            "<svg width='44000' height='51200'>"
            "<rect width='100%' height='100%' fill='red'/></svg>",
            echelle=2.0,
        )


def test_rasterisation_impose_la_taille_de_sortie() -> None:
    """La taille rendue est celle demandée (× échelle), pas celle du fichier."""
    from app.documents.professionnel.rasterisation import rasteriser_svg

    octets = rasteriser_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="150" height="100">'
        '<rect width="150" height="100" fill="green"/></svg>',
        echelle=2.0,
    )

    with LireImage.open(io.BytesIO(octets)) as rendu:
        assert rendu.size == (300, 200)
