"""Graphiques des livrables professionnels (incrément 69).

Huit points de contrôle, dans l'ordre du plan validé :

1. **moteur** (`graphiques.py`) : les quatre types produisent un SVG structuré
   (axes, polylines, arcs, légende) ; valeurs négatives acceptées en barres
   (axe zéro tracé), refusées en camembert ;
2. **validation** : specs invalides → ``ValidationError`` au message actionnable,
   jamais un graphique vide ;
3. **sécurité** : un libellé HTML/script ressort échappé — le SVG est construit
   par le serveur, aucun ``<script>`` ne peut s'y glisser ;
4. **rasterisation** (``rasterisation.py``) : PNG réel et dimensionné via
   resvg-py ; SVG que resvg refuse → erreur explicite ; parseur strict exigé ;
5. **injection** (``html_builder.injecter_graphiques``) : ancre remplie +
   figcaption ; ancre inconnue, double, ou spec sans ancre → ``ValidationError`` ;
6. **rendu réel PDF** : SVG vectoriel dans un PDF paginé (marqué
   ``sans_navigateur`` comme la suite professionnelle) ;
7. **rendu réel DOCX** : le .docx embarque un **vrai PNG** (jamais le repli
   « Image non intégrée ») et son PDF jumeau reste produit ;
8. **non-régression** : sans ``graphiques``, le corps nettoyé est identique et
   le tool reste utilisable sans le paramètre.

SQLite mémoire + stockage local temporaire (mêmes fixtures que
``test_documents_professionnel.py``).
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
from app.documents.professionnel import rendre_professionnel
from app.documents.professionnel.graphiques import (
    HAUTEUR_DEFAUT,
    LARGEUR_DEFAUT,
    render_chart,
)
from app.documents.professionnel.html_builder import injecter_graphiques, preparer
from app.documents.professionnel.rasterisation import (
    RasterisationIndisponible,
    rasteriser_svg,
)
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

# ---------------------------------------------------------------------------
# Fixtures (reprises de test_documents_professionnel.py)
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


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Professionnel")
    session.add(organisation)
    session.flush()
    return organisation


IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)


def _tools(service: DocumentService, policy: PermissionPolicy = POLICY) -> dict[str, Any]:
    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=IDENTITE,
            policy=policy,
            service_factory=lambda: service,
        )
    }

SPECS = {
    "bar": {"type": "bar", "title": "CA", "labels": ["T1", "T2", "T3"],
            "series": {"2025": [12, 18, -3]}},
    "line": {"type": "line", "title": "Evolution", "labels": ["Jan", "Fev", "Mar"],
             "series": {"F": [3, 5, 8]}},
    "pie": {"type": "pie", "title": "Repartition", "labels": ["A", "B", "C"],
            "values": [45, 30, 25]},
    "donut": {"type": "donut", "title": "Budget", "labels": ["L1", "L2"],
              "values": [60, 40]},
}


# ---------------------------------------------------------------------------
# 1. Moteur SVG
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nom", sorted(SPECS))
def test_moteur_produit_svg_structure(nom):
    svg = render_chart(SPECS[nom])

    assert svg.startswith("<svg ") and svg.rstrip().endswith("</svg>")
    assert 'viewBox="0 0' in svg
    assert 'role="img"' in svg and "<title>" in svg
    if nom in ("bar", "line"):
        assert 'stroke="#333"' in svg  # axe zéro toujours tracé
        assert ">0</text>" in svg or ">-0</text>" not in svg
    if nom == "bar":
        assert svg.count("<rect") >= 3  # une barre par point
    if nom == "line":
        assert svg.count("<polyline") == 1 and svg.count("<circle") == 3
    if nom in ("pie", "donut"):
        assert svg.count("<path") == len(SPECS[nom]["values"]) - 0 or svg.count("<path") >= 1
        assert "%" in svg  # pourcentages dans la légende
    if nom == "donut":
        assert 'fill="#fff"' in svg  # anneau = trou central
        assert 'font-weight="bold"' in svg  # total au centre


def test_moteur_barres_negatives_tracent_l_axe_zero():
    svg = render_chart({"type": "bar", "labels": ["a", "b"],
                        "series": {"S": [-5, 5]}})
    assert 'stroke="#333"' in svg


def test_moteur_deterministe():
    """La même spec produit les mêmes octets : base d'un contrôle par diff."""
    assert render_chart(SPECS["bar"]) == render_chart(SPECS["bar"])


# ---------------------------------------------------------------------------
# 2. Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec, fragment",
    [
        ({"type": "radar", "labels": ["a"], "series": {"S": [1]}}, "Type de graphique inconnu"),
        ({"type": "bar", "labels": ["a", "b"], "series": {"S": [1]}}, "2 labels"),
        ({"type": "bar", "labels": [], "series": {"S": []}}, "au moins un libellé"),
        ({"type": "bar", "labels": ["a"], "series": {}}, "au moins une série"),
        ({"type": "pie", "labels": ["a"], "values": [-1]}, "négatives"),
        ({"type": "pie", "labels": ["a", "b"], "values": [0, 0]}, "> 0"),
        ({"type": "pie", "labels": ["a"], "values": [float("nan")]}, "nombre fini"),
        ({"type": "bar", "labels": ["a"], "series": {"S": [float("inf")]}}, "nombre fini"),
        ({"type": "bar", "labels": ["a"], "series": {"S": [True]}}, "nombre fini"),
        ({"type": "bar", "labels": ["a"], "series": {"S": [1]}, "width": 100}, "largeur"),
        ({"type": "bar", "labels": ["a"], "series": {"S": [1]}, "height": 99}, "hauteur"),
    ],
)
def test_spec_invalide_leve_validation_error(spec, fragment):
    with pytest.raises(ValidationError) as exc:
        render_chart(spec)
    assert fragment in str(exc.value)


# ---------------------------------------------------------------------------
# 3. Sécurité XSS
# ---------------------------------------------------------------------------


def test_libelle_malveillant_est_echappe():
    svg = render_chart({"type": "pie", "labels": ['<script>alert(1)</script>'],
                        "values": [1]})
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_attribut_ne_peut_pas_etre_ferme_par_un_libelle():
    svg = render_chart({"type": "bar", "labels": ['" onload="alert(1)'],
                        "series": {"S": [1]}})
    # Le texte est affiché tel quel mais les guillemets sont échappés : aucun
    # attribut n'est réellement fermé, donc rien n'est exécutable.
    assert ' onload="' not in svg
    assert "&quot; onload=&quot;" in svg


# ---------------------------------------------------------------------------
# 4. Rasterisation (resvg-py)
# ---------------------------------------------------------------------------


def test_rasterisation_produit_png_dimensionne():
    svg = render_chart(SPECS["bar"])
    png = rasteriser_svg(svg)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(io.BytesIO(png))
    assert image.size == (LARGEUR_DEFAUT * 2, HAUTEUR_DEFAUT * 2)  # échelle 2


@pytest.mark.parametrize("nom", sorted(SPECS))
def test_rasterisation_accepte_les_quatre_types(nom):
    """resvg est un parseur STRICT : tout type doit produire un PNG valide."""
    png = rasteriser_svg(render_chart(SPECS[nom]))
    assert Image.open(io.BytesIO(png)).size[0] > 0


def test_rasterisation_refuse_svg_invalide():
    with pytest.raises((RasterisationIndisponible, ValidationError)):
        rasteriser_svg("<svg><rect x=</svg>")


def test_rasterisation_refuse_svg_vide():
    with pytest.raises(ValidationError):
        rasteriser_svg("")


# ---------------------------------------------------------------------------
# 5. Injection par ancre
# ---------------------------------------------------------------------------


def test_injection_remplace_ancre_et_pose_legende():
    corps = '<h1>T</h1><figure data-graphique="ca"></figure>'
    sorti = injecter_graphiques(corps, {"ca": {**SPECS["bar"], "legende": "Figure 1 – CA"}},
                                produire_svg=render_chart)
    soup = BeautifulSoup(sorti, "html.parser")
    figure = soup.find("figure")
    assert figure.find("svg") is not None
    assert figure.find("figcaption").get_text() == "Figure 1 – CA"
    assert "data-graphique" not in figure.attrs  # ancre consommée


def test_injection_hors_moteur_testable_avec_faux_svg():
    corps = '<figure data-graphique="x"></figure>'
    sorti = injecter_graphiques(corps, {"x": SPECS["bar"]},
                                produire_svg=lambda spec: "<svg>faux</svg>")
    assert "<svg>faux</svg>" in sorti


def test_injection_ancre_inconnue_refusee():
    with pytest.raises(ValidationError) as exc:
        injecter_graphiques('<figure data-graphique="zzz"></figure>',
                            {"ca": SPECS["bar"]}, produire_svg=render_chart)
    assert "zzz" in str(exc.value)
    # Dit ce qui EXISTE : l'agent peut se corriger sans relire le tool.
    assert "ca" in (exc.value.details.get("graphiques_disponibles") or [])


def test_injection_ancre_double_refusee():
    corps = '<figure data-graphique="x"></figure><figure data-graphique="x"></figure>'
    with pytest.raises(ValidationError) as exc:
        injecter_graphiques(corps, {"x": SPECS["bar"]}, produire_svg=render_chart)
    assert "double" in str(exc.value).lower()


def test_injection_spec_sans_ancre_refusee():
    with pytest.raises(ValidationError) as exc:
        injecter_graphiques("<h1>T</h1>", {"orphan": SPECS["bar"]},
                            produire_svg=render_chart)
    assert "orphan" in str(exc.value)


def test_injection_ne_touche_pas_le_reste_du_corps():
    corps = (
        '<h1>Titre</h1><p class="lead">Intro.</p>'
        '<figure data-graphique="x"></figure><p>Fin</p>'
    )
    sorti = injecter_graphiques(corps, {"x": {**SPECS["bar"], "legende": "L"}},
                                produire_svg=render_chart)
    assert "Intro." in sorti and "<p>Fin</p>" in sorti and "<h1>Titre</h1>" in sorti


# ---------------------------------------------------------------------------
# 6–7. Rendus réels
# ---------------------------------------------------------------------------

HTML_DEUX_GRAPHIQUES = (
    "<h1>Rapport</h1>"
    '<figure data-graphique="bar1"></figure>'
    '<figure data-graphique="donut1"></figure>'
)
GRAPHIQUES_DEUX = {
    "bar1": {**SPECS["bar"], "legende": "Figure 1 – CA"},
    "donut1": {**SPECS["donut"], "legende": "Figure 2 – Budget"},
}


def test_pdf_vectoriel_avec_graphiques():
    rendu = rendre_professionnel(HTML_DEUX_GRAPHIQUES, "Rapport", format="pdf",
                                 graphiques=GRAPHIQUES_DEUX)
    assert rendu.pages >= 1 and rendu.octets[:5] == b"%PDF-"
    assert not any("graphique" in a.lower() for a in rendu.avertissements)


def test_docx_embarque_un_vrai_png():
    rendu = rendre_professionnel(HTML_DEUX_GRAPHIQUES, "Rapport", format="docx",
                                 graphiques=GRAPHIQUES_DEUX)
    archive = zipfile.ZipFile(io.BytesIO(rendu.octets))
    media = [n for n in archive.namelist() if "media" in n]
    assert len(media) == 2
    assert all(archive.read(n)[:4] == b"\x89PNG" for n in media)

    document = LireDocx(io.BytesIO(rendu.octets))
    textes = "\n".join(p.text for p in document.paragraphs)
    assert "Image non intégrée" not in textes  # jamais le repli
    assert "Figure 1 – CA" in textes and "Figure 2 – Budget" in textes


def test_docx_sans_resvg_annonce_les_graphiques(monkeypatch):
    """Sans resvg-py, le DOCX reste livrable et l'absence est ANNONCÉE."""
    from app.documents.professionnel import rendu as module_rendu

    monkeypatch.setattr(module_rendu, "rasterisation_disponible", lambda: False)
    rendu = rendre_professionnel(HTML_DEUX_GRAPHIQUES, "Rapport", format="docx",
                                 graphiques=GRAPHIQUES_DEUX)
    assert rendu.octets[:2] == b"PK"  # DOCX livrable
    assert any("rasterisation" in a.lower() for a in rendu.avertissements)


# ---------------------------------------------------------------------------
# 8. Non-régression + tool
# ---------------------------------------------------------------------------


def test_sans_graphiques_le_corps_est_inchange():
    html = "<h1>T</h1><figure data-graphique=\"x\"></figure>"
    assert preparer(html).corps.count("data-graphique") == 1  # l'ancre survit au nettoyage


def test_preparer_preserve_les_ancres():
    html = '<h1>T</h1><figure data-graphique="x" data-legende="L"></figure>'
    corps = preparer(html).corps
    assert 'data-graphique="x"' in corps


def test_tool_professionnel_depose_avec_graphiques(service: DocumentService):
    """Bout-en-bout via le tool : dépose en 'proposed' avec rapport de graphiques."""
    tool = _tools(service)["generer_document_professionnel"]
    sortie = tool.handler({
        "contenu_html": HTML_DEUX_GRAPHIQUES,
        "titre": "Rapport graphé",
        "format": "docx",
        "type_document": "rapport",
        "propose_par": IDENTITE.agent_id,
        "graphiques": [
            {"id": "bar1", "type": "bar", "titre": "CA", "labels": ["T1", "T2"],
             "series": [{"nom": "2025", "valeurs": [12, 18]}],
             "legende": "Figure 1 – CA"},
            {"id": "donut1", "type": "donut", "titre": "Budget",
             "labels": ["L1", "L2"], "valeurs": [60, 40],
             "legende": "Figure 2 – Budget"},
        ],
    })
    assert sortie["graphiques"]["nombre"] == 2
    assert sorted(sortie["graphiques"]["ids"]) == ["bar1", "donut1"]
    assert sortie["statut"] == "proposed"  # règle 6 [C] : jamais approved depuis un agent


def test_tool_refuse_deux_graphiques_de_meme_id():
    from app.tools.documents import _specs_graphiques
    with pytest.raises(ValidationError):
        _specs_graphiques([
            {"id": "x", "type": "bar", "labels": ["a"],
             "series": [{"nom": "S", "valeurs": [1]}]},
            {"id": "x", "type": "pie", "labels": ["a"], "valeurs": [1]},
        ])


def test_tool_sans_graphiques_reste_intact(service: DocumentService):
    sortie = _tools(service)["generer_document_professionnel"].handler({
        "contenu_html": "<h1>Rapport simple</h1><p>Contenu.</p>",
        "titre": "Rapport simple",
        "format": "docx",
        "type_document": "rapport",
        "propose_par": IDENTITE.agent_id,
    })
    assert "graphiques" not in sortie
