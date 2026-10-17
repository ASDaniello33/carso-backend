"""SVG **écrit par l'agent** dans un livrable professionnel (incrément 70).

L'agent doit pouvoir dessiner un schéma (organigramme, flèches, flux, frise) au
lieu de le décrire. Sept points de contrôle :

1. **assainissement** (``html_builder``) — ``foreignObject``, animation, référence
   distante (``href`` *et* serveur de peinture ``url(http…)``) et attributs
   d'événement sont retirés ; le dessin survit ;
2. **constats** (``verification``) — ce qui a été retiré est dit, l'absence de
   ``viewBox`` et un schéma vide sont signalés ; le CSS interne du SVG ne
   déclenche plus les avertissements du document (faux positifs corrigés) ;
3. **casse XML** — ``viewBox``/gradients/``clipPath`` restaurés pour resvg et
   ``xmlns`` ajouté : sans cela, mesuré, un SVG qui n'a qu'un ``viewBox``
   sortait distordu (220 × 160 au lieu de 400 × 160) ;
4. **taille logique** — ``width``/``height`` déclarés d'abord (comme en HTML),
   ``viewBox`` ensuite : PDF et DOCX donnent la même place au même schéma ;
5. **DOCX réel** — vrai PNG embarqué, taille en millimètres, texte alternatif
   porté sur l'image, texte du SVG **jamais** répandu en paragraphes (la
   corruption silencieuse mesurée avant ce travail) ;
6. **PDF réel** — le schéma est rendu et borné à la page (un schéma vertical
   mesuré sur 3 pages tient désormais sur une) ;
7. **dégradations annoncées** — sans resvg les SVG sont retirés et l'omission est
   annoncée ; un SVG que resvg refuse lève une erreur qui **nomme la figure**.

SQLite mémoire + stockage local temporaire (mêmes fixtures que
``test_documents_graphiques.py``).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup
from docx import Document as LireDocx
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.core.errors import ValidationError
from app.documents.professionnel import rendre_professionnel, verifier
from app.documents.professionnel.html_builder import (
    compter_svg,
    convertir_svg_en_png,
    preparer,
)
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

# ---------------------------------------------------------------------------
# Fixtures (reprises de test_documents_graphiques.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=5 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)


def _tools(service: DocumentService) -> dict[str, Any]:
    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=IDENTITE,
            policy=POLICY,
            service_factory=lambda: service,
        )
    }


# ---------------------------------------------------------------------------
# Jeux d'essai
# ---------------------------------------------------------------------------

#: Organigramme typique : des boîtes, des textes, une flèche.
SVG_ORGANIGRAMME = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160" '
    'width="400" height="160" role="img">'
    "<title>Organigramme de la mission</title>"
    '<rect x="150" y="10" width="100" height="34" rx="6" fill="#1E3A5F"/>'
    '<text x="200" y="32" text-anchor="middle">Chef de mission</text>'
    '<line x1="200" y1="44" x2="200" y2="70" stroke="#333" stroke-width="1.5"/>'
    '<rect x="30" y="70" width="100" height="30" fill="#EFF6FF" stroke="#2563EB"/>'
    '<text x="80" y="90" text-anchor="middle">Formateur</text>'
    "</svg>"
)

#: Le même, **sans** width/height : seul le viewBox donne la taille.
SVG_VIEWBOX_SEUL = SVG_ORGANIGRAMME.replace(' width="400" height="160"', "")

#: Ce qui n'est pas un dessin : tout doit disparaître.
SVG_HOSTILE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    "<title>Schéma hostile</title>"
    "<foreignObject><p>HTML embarqué</p></foreignObject>"
    '<image href="http://exemple.invalid/logo.png" width="10" height="10"/>'
    '<a xlink:href="javascript:alert(1)"><rect width="10" height="10"/></a>'
    '<rect onclick="alert(1)" width="20" height="20" fill="url(http://exemple.invalid/p.svg#g)"/>'
    "<animate attributeName=\"x\" from=\"0\" to=\"10\" dur=\"1s\"/>"
    '<path d="M0 0 L10 10" fill="#333"/>'
    "</svg>"
)


def figure(svg: str, legende: str = "Figure 1 – Organisation") -> str:
    return f'<h1>Schéma</h1><figure>{svg}<figcaption>{legende}</figcaption></figure>'


def _png(largeur: int = 8, hauteur: int = 4) -> bytes:
    """PNG minimal valide (rasteriser factice : aucun moteur requis)."""
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), "white").save(tampon, format="PNG")
    return tampon.getvalue()


def _texte_docx(octets: bytes) -> str:
    return "\n".join(p.text for p in LireDocx(io.BytesIO(octets)).paragraphs)


# ---------------------------------------------------------------------------
# 1. Assainissement
# ---------------------------------------------------------------------------


def test_assainissement_retire_tout_ce_qui_nest_pas_un_dessin():
    corps = preparer(figure(SVG_HOSTILE)).corps

    assert "<foreignobject" not in corps and "foreignObject" not in corps
    assert "HTML embarqué" not in corps
    assert "<animate" not in corps
    assert "exemple.invalid" not in corps  # href distant ET serveur de peinture
    assert "javascript:" not in corps
    assert "onclick" not in corps
    assert "<path" in corps and "<rect" in corps  # le dessin survit
    assert "Schéma hostile" in corps  # le titre aussi (texte alternatif)


def test_assainissement_conserve_les_references_internes():
    """Marqueurs et dégradés internes : ce sont des références `#id`, admises."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<defs><marker id="fleche"><path d="M0 0 L6 3 L0 6 Z" fill="#333"/></marker>'
        '<linearGradient id="fond"><stop offset="0" stop-color="#1E3A5F"/></linearGradient>'
        "</defs>"
        '<rect width="20" height="20" fill="url(#fond)"/>'
        '<line x1="0" y1="30" x2="30" y2="30" stroke="#333" marker-end="url(#fleche)"/>'
        "</svg>"
    )
    corps = preparer(f'<h1>T</h1>{svg}').corps

    assert "url(#fond)" in corps
    assert "url(#fleche)" in corps
    assert 'id="fleche"' in corps and 'id="fond"' in corps


def test_le_texte_alteratif_nest_pas_un_placeholder():
    """Le titre du SVG ne doit pas être pris pour un `[placeholder]` à corriger."""
    constats = verifier(figure(SVG_ORGANIGRAMME))
    assert not any("laceholder" in c for c in constats)


# ---------------------------------------------------------------------------
# 2. Constats destinés à l'agent
# ---------------------------------------------------------------------------


def test_constats_nomment_ce_qui_a_ete_retire():
    constats = " | ".join(verifier(figure(SVG_HOSTILE)))
    assert "foreignObject" in constats
    assert "exemple.invalid" in constats
    assert "onclick" in constats


def test_constat_sur_un_svg_sans_viewbox():
    constats = verifier("<h1>T</h1><svg><rect width='10' height='10'/></svg>")
    assert any("viewBox" in c for c in constats)


def test_constat_sur_un_schema_vide():
    """Un `<svg>` sans dessin produirait une figure blanche silencieuse."""
    constats = verifier('<h1>T</h1><svg viewBox="0 0 10 10"><title>Vide</title></svg>')
    assert any("sans dessin" in c for c in constats)


def test_constat_explicite_pour_un_docx():
    constats = verifier(figure(SVG_ORGANIGRAMME), format="docx")
    assert any("DOCX" in c for c in constats)


def test_le_css_interne_du_svg_ne_est_plus_un_avertissement_du_document():
    """`style="font-…"` et `fill` sont légitimes **dans** un SVG (faux positif)."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">'
        '<text style="font-family:Arial" x="0" y="20">Titre</text>'
        '<rect style="background:#fff" width="4" height="4"/>'
        "</svg>"
    )
    constats = verifier(f"<h1>T</h1>{svg}")
    assert not any("Styles inline" in c for c in constats)
    assert not any("flex/grid" in c for c in constats)


def test_le_css_du_document_est_toujours_signale():
    """Le faux positif corrigé ne doit pas avoir éteint le contrôle réel."""
    constats = verifier('<h1>T</h1><div style="color:red">X</div>')
    assert any("Styles inline" in c for c in constats)


# ---------------------------------------------------------------------------
# 3. Casse XML (BeautifulSoup met tout en minuscules, resvg est strict)
# ---------------------------------------------------------------------------


def test_casse_xml_restauree_avant_resvg():
    vus: list[str] = []

    def rasteriser(svg: str) -> bytes:
        vus.append(svg)
        return _png()

    corps = preparer(
        '<h1>T</h1><svg viewBox="0 0 40 40">'
        '<defs><linearGradient id="g"><stop offset="0"/></linearGradient>'
        '<clipPath id="c"><rect width="4" height="4"/></clipPath></defs>'
        '<rect width="10" height="10" fill="url(#g)"/></svg>'
    ).corps
    convertir_svg_en_png(corps, rasteriser)

    assert len(vus) == 1
    xml = vus[0]
    assert 'viewBox="0 0 40 40"' in xml  # sans cela, resvg ignore la mise à l'échelle
    assert "viewbox" not in xml
    assert "<linearGradient" in xml and "<clipPath" in xml
    assert 'xmlns="http://www.w3.org/2000/svg"' in xml


def test_xmlns_ajoute_quand_lagent_ne_lecrit_pas():
    vus: list[str] = []

    def rasteriser(svg: str) -> bytes:
        vus.append(svg)
        return _png()

    corps = preparer('<h1>T</h1><svg viewBox="0 0 10 10"><rect width="2" height="2"/></svg>').corps
    convertir_svg_en_png(corps, rasteriser)
    assert 'xmlns="http://www.w3.org/2000/svg"' in vus[0]


# ---------------------------------------------------------------------------
# 4. Taille logique : la même dans les deux formats
# ---------------------------------------------------------------------------


def _image_convertie(corps: str) -> Any:
    corps_png = convertir_svg_en_png(corps, lambda _svg: _png())
    return BeautifulSoup(corps_png, "html.parser").find("img")


def test_width_height_declares_priment_sur_le_viewbox():
    """Le viewBox est un système de coordonnées, pas une taille d'affichage."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600" '
           'width="200" height="200"><rect width="1" height="1"/></svg>')
    image = _image_convertie(f"<h1>T</h1>{svg}")
    assert (image["width"], image["height"]) == ("200", "200")


def test_viewbox_sert_de_taille_quand_rien_nest_declare():
    image = _image_convertie(figure(SVG_VIEWBOX_SEUL))
    assert (image["width"], image["height"]) == ("400", "160")


def test_image_porte_le_titre_du_svg_comme_texte_alternatif():
    image = _image_convertie(figure(SVG_ORGANIGRAMME))
    assert image["alt"] == "Organigramme de la mission"
    assert image["src"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# 5. DOCX réel
# ---------------------------------------------------------------------------


def test_docx_embarque_le_schema_a_sa_taille_logique():
    rendu = rendre_professionnel(figure(SVG_ORGANIGRAMME), "Rapport", format="docx")

    archive = zipfile.ZipFile(io.BytesIO(rendu.octets))
    medias = [n for n in archive.namelist() if "media" in n]
    assert len(medias) == 1
    assert archive.read(medias[0])[:4] == b"\x89PNG"  # un vrai PNG, pas un repli

    document = LireDocx(io.BytesIO(rendu.octets))
    forme = document.inline_shapes[0]
    # 400 x 160 px logiques (convention CSS 96 dpi) — et non 174 mm de large.
    assert forme.width.mm == pytest.approx(400 / 96 * 25.4, abs=0.3)
    assert forme.height.mm == pytest.approx(160 / 96 * 25.4, abs=0.3)


def test_docx_ne_repand_pas_le_texte_du_svg_et_garde_la_legende():
    """La corruption mesurée : l'organigramme devenait une suite de mots."""
    rendu = rendre_professionnel(figure(SVG_ORGANIGRAMME), "Rapport", format="docx")
    texte = _texte_docx(rendu.octets)

    assert "Chef de mission" not in texte  # le texte du dessin n'est pas du texte
    assert "Formateur" not in texte
    assert "Figure 1 – Organisation" in texte  # la légende, elle, reste
    assert "Image non intégrée" not in texte


def test_docx_image_porte_la_description_accessible():
    rendu = rendre_professionnel(figure(SVG_ORGANIGRAMME), "Rapport", format="docx")
    document = LireDocx(io.BytesIO(rendu.octets))
    assert document.inline_shapes[0]._inline.docPr.get("descr") == (
        "Organigramme de la mission"
    )


def test_schema_et_graphique_cohabitent_dans_un_seul_document():
    """Une seule voie d'adaptation pour les SVG du moteur **et** de l'agent."""
    html = figure(SVG_ORGANIGRAMME) + '<figure data-graphique="ca"></figure>'
    rendu = rendre_professionnel(
        html,
        "Rapport",
        format="docx",
        graphiques={
            "ca": {
                "type": "bar",
                "labels": ["T1", "T2"],
                "series": {"2026": [12, 18]},
                "legende": "Figure 2 – CA",
            }
        },
    )
    document = LireDocx(io.BytesIO(rendu.octets))
    assert len(document.inline_shapes) == 2
    texte = _texte_docx(rendu.octets)
    assert "Figure 1 – Organisation" in texte and "Figure 2 – CA" in texte


# ---------------------------------------------------------------------------
# 6. PDF réel
# ---------------------------------------------------------------------------


def test_pdf_rend_le_schema_sans_avertissement():
    rendu = rendre_professionnel(figure(SVG_ORGANIGRAMME), "Rapport", format="pdf")
    assert rendu.octets[:5] == b"%PDF-"
    assert rendu.pages == 1
    assert not any("SVG" in a for a in rendu.avertissements)


def test_pdf_borne_un_schema_vertical_a_une_page():
    """Mesuré avant ce travail : 400 x 900 débordait sur 3 pages."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 900">'
           '<rect width="400" height="900" fill="#1E3A5F"/></svg>')
    rendu = rendre_professionnel(figure(svg), "Rapport", format="pdf")
    assert rendu.pages == 1


def test_pdf_borne_un_schema_tres_large_a_une_page():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4000 200">'
           '<rect width="4000" height="200" fill="#1E3A5F"/></svg>')
    rendu = rendre_professionnel(figure(svg), "Rapport", format="pdf")
    assert rendu.pages == 1


# ---------------------------------------------------------------------------
# 7. Dégradations annoncées, jamais silencieuses
# ---------------------------------------------------------------------------


def test_docx_sans_resvg_retire_les_svg_et_lannonce(monkeypatch):
    """Sans convertisseur, le DOCX reste livrable — sans texte de SVG répandu."""
    from app.documents.professionnel import rendu as module_rendu

    monkeypatch.setattr(module_rendu, "rasterisation_disponible", lambda: False)
    rendu = rendre_professionnel(figure(SVG_ORGANIGRAMME), "Rapport", format="docx")

    assert rendu.octets[:2] == b"PK"  # DOCX livrable
    assert any("rasterisation" in a.lower() for a in rendu.avertissements)
    assert any("1 SVG" in a for a in rendu.avertissements)  # l'omission est comptée

    document = LireDocx(io.BytesIO(rendu.octets))
    assert not document.inline_shapes
    assert "Chef de mission" not in _texte_docx(rendu.octets)


def test_svg_refuse_par_resvg_leve_une_erreur_qui_nomme_la_figure():
    """Aucun repli muet : le message nomme la figure et propose la sortie PDF."""

    def refuse(_svg: str) -> bytes:
        raise ValueError("the root node was opened but never closed")

    with pytest.raises(ValidationError) as erreur:
        convertir_svg_en_png(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            "<title>Schéma cassé</title><path d=\"M0 0\"/></svg>",
            refuse,
        )
    assert "Schéma cassé" in str(erreur.value)
    assert "pdf" in str(erreur.value.details.get("conseil", "")).lower()


def test_sans_resvg_le_pdf_reste_intact(monkeypatch):
    """La limite est bornée au DOCX : le PDF n'a pas besoin de rasterisation."""
    from app.documents.professionnel import rendu as module_rendu

    monkeypatch.setattr(module_rendu, "rasterisation_disponible", lambda: False)
    rendu = rendre_professionnel(figure(SVG_ORGANIGRAMME), "Rapport", format="pdf")
    assert rendu.octets[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# 8. Non-régression
# ---------------------------------------------------------------------------


def test_document_sans_svg_reste_intact():
    html = "<h1>Titre</h1><p>Texte simple.</p>"
    corps = preparer(html).corps
    assert "<svg" not in corps and compter_svg(corps) == 0

    rendu = rendre_professionnel(html, "Titre", format="docx")
    assert rendu.octets[:2] == b"PK"
    assert not LireDocx(io.BytesIO(rendu.octets)).inline_shapes


def test_convertir_svg_en_png_sans_svg_ne_change_rien():
    corps = preparer("<h1>T</h1><p>Texte.</p>").corps
    assert convertir_svg_en_png(corps, lambda _svg: _png()) == corps


def test_tool_depose_un_document_avec_schema(service: DocumentService):
    """Bout-en-bout : l'agent écrit un schéma, le document naît en proposition."""
    sortie = _tools(service)["generer_document_professionnel"].handler(
        {
            "contenu_html": figure(SVG_ORGANIGRAMME),
            "titre": "Rapport avec schéma",
            "format": "docx",
            "type_document": "rapport",
            "propose_par": IDENTITE.agent_id,
        }
    )
    assert sortie["statut"] == "proposed"  # règle 6 [C] : jamais approuvé par un agent
    assert sortie["documents"]["format"] == "docx"
    assert "graphiques" not in sortie  # aucun graphique déclaré : rien à rapporter
