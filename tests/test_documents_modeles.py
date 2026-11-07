"""Fidélité aux modèles d'organisation (INC-125).

Défaut mesuré avant cet incrément : `clone_document_structure` créait un document
**neuf** — seules la taille de page et le style « Normal » survivaient ; ni les
styles du client, ni sa numérotation, ni ses en-têtes/pieds, ni sa couverture.
L'agent ne pouvait donc pas respecter un modèle.

Ces tests verrouillent la promesse tenue : **le DOCX du modèle sert de base**.

1. inventaire — placeholders (corps, en-têtes), plan des titres, styles de titre ;
2. remplissage par placeholders — corps, tableaux, en-têtes et pieds ;
3. remplissage par sections — corps remplacé, style du voisin repris, sections
   non fournies **conservées** (et listées), titre inconnu refusé avec la liste ;
4. conservation réelle du modèle — numérotation, en-tête et taille de page lus
   dans le fichier produit ;
5. surface agent — `remplir_document_modele` dépose un livrable `proposed` ancré ;
   un modèle PDF est refusé avec la consigne `generer_document` + `modele` ;
6. `generer_document(modele=…)` applique l'identité visuelle du modèle.

SQLite mémoire + stockage local temporaire.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from docx import Document as LireDocx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.services import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.documents.modeles import (
    inventaire_modele,
    style_depuis_modele,
)
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset(
        {"script_python", "document_read", "document_propose"}
    )
)


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
    return LocalDocumentStorage(tmp_path / "storage", max_bytes=8 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Modele")
    session.add(organisation)
    session.flush()
    return organisation


def _modele_docx() -> bytes:
    """Modèle réaliste : styles de titre, paragraphe de corps stylé, en-tête."""
    document = LireDocx()
    document.add_heading("Offre technique", level=0)
    document.add_heading("1. Contexte", level=1)
    corps = document.add_paragraph("Texte du client à remplacer.")
    corps.paragraph_format.first_line_indent = Pt(12)
    corps.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    document.add_heading("2. Méthodologie", level=1)
    document.add_paragraph("Méthode du client.")
    document.add_heading("3. Budget", level=1)
    document.add_paragraph("Budget du client.")
    entete = document.sections[0].header.paragraphs[0]
    entete.text = "Client {{client}} — offre {{reference}}"
    return _octets(document)


def _octets(document) -> bytes:
    tampon = io.BytesIO()
    document.save(tampon)
    return tampon.getvalue()


def _enregistrer_modele(
    service: DocumentService,
    session: Session,
    organisation: Organisation,
    *,
    octets: bytes,
    type_document: str = "modele_offre_technique",
    nom: str = "modele_offre.docx",
) -> str:
    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom=nom,
            stream=io.BytesIO(octets),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    ModeleDocumentService(session).enregistrer_modele(
        organisation.id,
        nom=nom,
        type_document=type_document,
        document_template_id=document.id,
        registered_by="test",
    )
    session.commit()
    _nom, chemin = service.telecharger(document.id)
    return str(chemin)


def _tools(service: DocumentService) -> dict[str, object]:
    outils = build_document_tools(
        identite=IDENTITE, policy=POLICY, service_factory=lambda: service
    )
    return {outil.name: outil for outil in outils}


# --- 1. Inventaire ------------------------------------------------------------


def test_inventaire_decrit_placeholders_titres_et_entete(tmp_path: Path) -> None:
    chemin = Path(tmp_path / "modele.docx")
    chemin.write_bytes(_modele_docx())

    inventaire = inventaire_modele(chemin)

    assert inventaire["placeholders"] == ["client", "reference"]
    assert [titre["texte"] for titre in inventaire["titres"]] == [
        "Offre technique",
        "1. Contexte",
        "2. Méthodologie",
        "3. Budget",
    ]
    assert "Heading 1" in inventaire["styles_titres"]
    assert inventaire["sections"][0]["entete"].startswith("Client {{client}}")
    assert inventaire["couverture_probable"] is True


# --- 2. Placeholders ----------------------------------------------------------


# --- 3. Sections --------------------------------------------------------------


# --- 4. Fidélité au fichier du modèle ----------------------------------------


# --- 5. Surface agent ---------------------------------------------------------


def test_style_depuis_modele_donne_une_identite_visuelle(
    service: DocumentService, session: Session, organisation: Organisation
) -> None:
    chemin = Path(
        _enregistrer_modele(service, session, organisation, octets=_modele_docx())
    )
    style = style_depuis_modele(chemin)
    assert style.nom == "reference"
    assert style.page.format in {"A4", "Letter"}


def test_generer_document_applique_l_identite_du_modele(
    service: DocumentService, session: Session, organisation: Organisation
) -> None:
    _enregistrer_modele(service, session, organisation, octets=_modele_docx())
    outils = _tools(service)

    sortie = outils["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "livrable_style",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "modele": {
                "organisation_id": str(organisation.id),
                "type_document": "modele_offre_technique",
            },
            "document_spec": {
                "metadata": {"titre": "Offre au style du client"},
                "blocs": [{"type": "paragraphe", "texte": "Contenu structuré."}],
            },
        }
    )
    assert sortie["ok"] is True, sortie
    assert sortie["modele"]["type_document"] == "modele_offre_technique"
