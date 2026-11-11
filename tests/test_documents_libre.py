"""Tests — ``generer_document`` en mode HTML libre (INC-137).

Ce que ces tests verrouillent, dans l'ordre des garanties annoncées :

1. **le HTML est la voie du document libre** : un corps avec les classes CARSO
   produit un DOCX au contenu réel (titres, paragraphe, tableau, encadré) ;
2. **la mise en forme est libre mais uniforme** : ``css`` reprend une classe, les
   tailles en ``pt`` sont alignées sur l'échelle du document et l'alignement est
   annoncé ;
3. **les images par lien sont embarquées** (le fichier ne dépend plus du réseau),
   et un lien qui échoue laisse un **repère visible** + un avertissement ;
4. **le serveur ne devient pas un proxy** : une URL locale ou ``file:`` est
   refusée avant tout appel réseau ;
5. **pas de tiret cadratin** : il est remplacé par « - » et le remplacement est
   signalé ;
6. les graphiques ancrés sont dessinés par le serveur, pas par l'agent.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from docx import Document as LireDocx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.documents.document_libre import (
    ECHELLE_TYPOGRAPHIQUE,
    normaliser_css,
    sans_tirets_cadratins,
)
from app.documents.images_distantes import (
    ImageDistante,
    ImageDistanteRefusee,
    url_autorisee,
)
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)

HTML = """
<div class="h1">Note de cadrage</div>
<div class="h2">Contexte</div>
<p class="p">Le programme FID couvre <span class="bold">cinq jours</span> de
formation à Toliara, avec un accompagnement terrain.</p>
<ul class="liste"><li>Formation certifiante</li><li>Suivi post-formation</li></ul>
<table class="tableau">
  <tr><th>Poste</th><th>Montant</th></tr>
  <tr><td>Formation</td><td>12 000</td></tr>
</table>
<div class="encadre">Le budget est une proposition à valider.</div>
<img src="https://exemple.test/photo.png" alt="Photo">
"""


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def service(session: Session, tmp_path: Path) -> DocumentService:
    storage = LocalDocumentStorage(tmp_path / "carso", max_bytes=16 * 1024 * 1024)
    return DocumentService(
        session,
        storage=storage,
        actor_id="test",
        perimetre=Perimetre(role="administrateur", utilisateur_id=uuid4(), est_complet=True),
    )


def _outils(service: DocumentService) -> dict[str, Any]:
    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=IDENTITE, policy=POLICY, service_factory=lambda: service
        )
    }


def _docx(service: DocumentService, sortie: dict[str, Any]) -> Any:
    _nom, chemin = service.telecharger(UUID(str(sortie["document_id"])))
    return LireDocx(io.BytesIO(Path(chemin).read_bytes()))


def _faux_image(*_args: Any, **_kwargs: Any) -> ImageDistante:
    """Image distante simulée : un PNG 1×1 valide, sans réseau."""
    return ImageDistante(
        octets=(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        ),
        mime_type="image/png",
    )


def _image_refusee(*_args: Any, **_kwargs: Any) -> ImageDistante:
    raise ImageDistanteRefusee("Image distante inaccessible (HTTP 404) : lien mort")


# --- 1. Le document libre en DOCX ---------------------------------------------


def test_html_produit_un_docx_avec_les_classes(
    service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.documents.images_distantes.recuperer_image", _faux_image
    )

    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note-cadrage",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "titre": "Note de cadrage",
            "html": HTML,
        }
    )

    assert sortie["ok"] is True
    assert sortie["modele"] == "html"
    assert sortie["format"] == "docx"
    document = _docx(service, sortie)
    texte = "\n".join(paragraphe.text for paragraphe in document.paragraphs)
    assert "Note de cadrage" in texte
    assert "cinq jours" in texte
    assert "Formation certifiante" in texte
    # Le tableau du HTML est devenu un vrai tableau Word.
    assert document.tables and document.tables[0].rows[1].cells[0].text.strip() == "Formation"
    # L'image distante a été embarquée : une image dans le DOCX.
    assert len(document.inline_shapes) == 1


def test_css_de_l_agent_reprend_une_classe(
    service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.documents.images_distantes.recuperer_image", _faux_image
    )

    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note-couleur",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "titre": "Note",
            "html": HTML,
            "css": ".h2 { color: #7a1f1f; } .h1 { font-size: 20pt; }",
        }
    )

    assert sortie["ok"] is True
    document = _docx(service, sortie)
    titre = next(p for p in document.paragraphs if p.text.strip() == "Note de cadrage")
    # 20 pt : la taille demandée est appliquée (elle est déjà dans l'échelle).
    assert round(titre.runs[0].font.size.pt) == 20
    # La couleur du CSS de l'agent l'emporte sur celle de la feuille CARSO :
    # c'est la promesse « classe reprise, pas réécrite ».
    sous_titre = next(p for p in document.paragraphs if p.text.strip() == "Contexte")
    assert str(sous_titre.runs[0].font.color.rgb) == "7A1F1F"


def test_taille_hors_echelle_alignee_et_annoncee(
    service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.documents.images_distantes.recuperer_image", _faux_image
    )

    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note-taille",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "titre": "Note",
            "html": HTML,
            "css": ".p { font-size: 15px; }",
        }
    )

    assert sortie["ok"] is True
    assert any("échelle" in phrase for phrase in sortie["avertissements"])


def test_format_xlsx_refuse_en_mode_html(service: DocumentService) -> None:
    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "xlsx",
            "nom_fichier": "classeur",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "html": "<p class=\"p\">Texte</p>",
        }
    )

    assert sortie["ok"] is False
    assert "DOCX ou un PDF" in sortie["error"]["message"]


def test_corps_html_vide_refuse(service: DocumentService) -> None:
    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "vide",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "html": "   ",
        }
    )

    assert sortie["ok"] is False


# --- 2. Images par lien -------------------------------------------------------


def test_image_distante_embarquee(
    service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.documents.images_distantes.recuperer_image", _faux_image
    )

    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "image-en-ligne",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "html": '<p class="p">Photo :</p><img src="https://exemple.test/p.png">',
        }
    )

    assert sortie["ok"] is True
    # Le fichier produit ne contient plus d'URL : l'image est embarquée.
    document = _docx(service, sortie)
    assert len(document.inline_shapes) == 1


def test_image_injoignable_laisse_un_repere(
    service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.documents.images_distantes.recuperer_image", _image_refusee
    )

    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "lien-mort",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "html": '<img src="https://exemple.test/absent.png">',
        }
    )

    assert sortie["ok"] is True
    texte = "\n".join(p.text for p in _docx(service, sortie).paragraphs)
    assert "Image non chargée" in texte
    assert any("HTTP 404" in phrase for phrase in sortie["avertissements"])


def test_url_interne_refusee() -> None:
    """Pas de SSRF : le serveur ne va pas chercher le réseau interne."""
    for url in (
        "http://127.0.0.1/x.png",
        "http://localhost/x.png",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "ftp://exemple.test/x.png",
    ):
        autorisee, _raison = url_autorisee(url)
        assert autorisee is False, url


# --- 3. Typographie et ponctuation -------------------------------------------


def test_tirets_cadratins_remplaces() -> None:
    propre, nombre = sans_tirets_cadratins(
        '<p class="p">Voici le plan — en deux parties – et la suite.</p>'
    )

    assert "—" not in propre and "–" not in propre
    assert "Voici le plan - en deux parties - et la suite." in propre
    assert nombre == 2


def test_les_attributs_ne_sont_pas_touches() -> None:
    source = '<a href="https://exemple.test/a—b" class="x—y">lien — ici</a>'

    propre, nombre = sans_tirets_cadratins(source)

    assert 'href="https://exemple.test/a—b"' in propre
    assert 'class="x—y"' in propre
    assert "lien - ici" in propre
    assert nombre == 1


@pytest.mark.parametrize("taille", ECHELLE_TYPOGRAPHIQUE)
def test_les_tailles_de_l_echelle_sont_conservees(taille: float) -> None:
    css, avertissements = normaliser_css(f".h1 {{ font-size: {taille:g}pt; }}")

    assert f"font-size: {taille:g}pt" in css
    assert avertissements == []


def test_les_tailles_relatives_sont_laissees() -> None:
    css, avertissements = normaliser_css(".h2 { font-size: 1.4em; }")

    assert "1.4em" in css
    assert avertissements == []


# --- 4. Figures ---------------------------------------------------------------


def test_graphique_ancre_dessine_par_le_serveur(
    service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.documents.images_distantes.recuperer_image", _faux_image
    )

    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note-graphique",
            "type_document": "autre",
            "propose_par": "agent_generateur_offre",
            "html": (
                '<div class="h2">Répartition</div>'
                '<figure data-graphique="parts"></figure>'
            ),
            "graphiques": [
                {
                    "id": "parts",
                    "type": "pie",
                    "titre": "Budget par poste",
                    "labels": ["Formation", "Logistique"],
                    "valeurs": [60, 40],
                }
            ],
        }
    )

    assert sortie["ok"] is True, sortie.get("error")
    assert sortie["graphiques"] == 1
    assert len(_docx(service, sortie).inline_shapes) == 1
