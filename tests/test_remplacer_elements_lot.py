"""Remplacement de texte par lot dans une copie de modèle (INC-129).

Un modèle contient plusieurs titres et lignes à réécrire. Appeler l'outil une
fois par élément produisait autant de versions que d'appels et noyait
l'historique. Le lot corrige cela : deux listes alignées, **une seule version**.

Ces tests couvrent le service (une version, styles conservés, ordre) et le
schéma de l'outil (formes exclusives, listes alignées).
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

import pytest
from docx import Document as LireDocx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.services.document_service import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.application.services.moteur_documentaire_service import MoteurDocumentaireService
from app.core.errors import ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Appel, Lot, Offre, Organisation
from tests.test_moteur_documentaire import modele_docx


@pytest.fixture
def db_session():
    moteur = create_engine("sqlite://")
    Base.metadata.create_all(moteur)
    fabrique = sessionmaker(bind=moteur)
    with fabrique() as active:
        yield active
    moteur.dispose()


@pytest.fixture
def stockage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "stockage", max_bytes=8 * 1024 * 1024)


@pytest.fixture
def contexte(db_session, stockage):
    """Organisation, modèle au registre, appel + lot + offre, copie prête."""
    organisation = Organisation(nom="Client Lot")
    db_session.add(organisation)
    db_session.flush()
    documents = DocumentService(db_session, storage=stockage, actor_id="test")
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="modele_client.docx",
            stream=io.BytesIO(modele_docx()),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    modele = ModeleDocumentService(db_session).enregistrer_modele(
        organisation.id,
        nom="modele_client.docx",
        type_document="modele_offre_technique",
        document_template_id=document.id,
        registered_by="test",
    )
    appel = Appel(
        organisation_id=organisation.id,
        reference="AMI-LOT",
        titre="Appel lot",
        type="appel_a_proposition",
    )
    db_session.add(appel)
    db_session.flush()
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot 1")
    db_session.add(lot)
    db_session.flush()
    offre = Offre(
        organisation_id=organisation.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type="offre_technique",
        reference="OFFRE-LOT",
        titre="Offre lot",
        version=1,
    )
    db_session.add(offre)
    db_session.flush()
    service = MoteurDocumentaireService(
        db_session, document_service=documents, actor_id="agent_generateur_offre"
    )
    copie, _modele, elements = service.copier_modele_dans_objet(
        "offre", offre.id, modele_id=modele.id
    )
    return {
        "db": db_session,
        "documents": documents,
        "service": service,
        "offre": offre,
        "copie": copie,
        "elements": elements,
    }


def test_le_lot_ne_produit_qu_une_version(contexte) -> None:
    service = contexte["service"]
    offre = contexte["offre"]
    copie = contexte["copie"]
    par_texte = {element.texte: element.element_id for element in contexte["elements"]}
    client = next(cle for cle in par_texte if cle.startswith("Client :"))
    titre = "Offre technique"

    resultat = service.remplacer_elements(
        "offre",
        offre.id,
        [
            {"element_id": par_texte[client], "nouveau_element": "Client : FID Madagascar"},
            {"element_id": par_texte[titre], "nouveau_element": "Offre technique — FID 59"},
        ],
    )

    assert resultat["version"] == copie.version + 1  # une seule version pour deux éléments
    assert resultat["nombre"] == 2
    assert {item["ancien_texte"] for item in resultat["remplacements"]} == {client, titre}
    _nom, chemin = contexte["documents"].telecharger(
        UUID(resultat["document_id"])
    )
    copie_docx = LireDocx(str(chemin))
    textes = [paragraphe.text for paragraphe in copie_docx.paragraphs]
    assert "Offre technique — FID 59" in textes
    assert "Client : FID Madagascar" in textes
    # Mise en forme du modèle conservée pour les deux.
    client_paragraphe = next(p for p in copie_docx.paragraphs if p.text.startswith("Client :"))
    assert client_paragraphe.runs[0].bold is True
    assert client_paragraphe.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY


def test_le_lot_accepte_les_segments_de_style(contexte) -> None:
    service = contexte["service"]
    par_texte = {element.texte: element.element_id for element in contexte["elements"]}
    cible = next(cle for cle in par_texte if cle.startswith("Client :"))

    resultat = service.remplacer_elements(
        "offre",
        contexte["offre"].id,
        [
            {
                "element_id": par_texte[cible],
                "nouveau_element": [
                    {"texte": "Client : ", "gras": True},
                    {"texte": "FID", "italique": True},
                ],
            }
        ],
    )

    assert resultat["remplacements"][0]["nouveau_texte"] == "Client : FID"
    # Forme unitaire conservée pour un seul élément.
    assert resultat["element_id"] == par_texte[cible]


def test_un_element_vise_deux_fois_est_refuse(contexte) -> None:
    element = contexte["elements"][3].element_id

    with pytest.raises(ValidationError, match="deux fois"):
        contexte["service"].remplacer_elements(
            "offre",
            contexte["offre"].id,
            [
                {"element_id": element, "nouveau_element": "A"},
                {"element_id": element, "nouveau_element": "B"},
            ],
        )


def test_un_identifiant_inconnu_dans_le_lot_est_refuse(contexte) -> None:
    with pytest.raises(ValidationError, match="introuvable"):
        contexte["service"].remplacer_elements(
            "offre",
            contexte["offre"].id,
            [
                {"element_id": "block_003", "nouveau_element": "Client : X"},
                {"element_id": "block_999", "nouveau_element": "Fantôme"},
            ],
        )


# --- schéma de l'outil --------------------------------------------------------


def _outil_remplacement(documents):
    from app.tools.documents import build_document_tools
    from app.tools.permissions import AgentIdentity, PermissionPolicy

    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=AgentIdentity(agent_id="agent_generateur_offre"),
            policy=PermissionPolicy(allowed_capabilities=frozenset({"document_propose"})),
            service_factory=lambda: documents,
        )
    }["remplacer_element_model"]


def test_l_outil_accepte_les_deux_listes(contexte) -> None:
    outil = _outil_remplacement(contexte["documents"])
    par_texte = {element.texte: element.element_id for element in contexte["elements"]}
    client = next(cle for cle in par_texte if cle.startswith("Client :"))

    sortie = outil.invoke(
        {
            "ancre": "offre",
            "entity_id": str(contexte["offre"].id),
            "elements": [par_texte[client], par_texte["Offre technique"]],
            "nouveaux_elements": ["Client : FID", "Offre technique — FID"],
        }
    )

    assert sortie["ok"] is True, sortie
    assert sortie["nombre"] == 2
    assert sortie["version"] == contexte["copie"].version + 1


def test_l_outil_refuse_des_listes_desalignees(contexte) -> None:
    outil = _outil_remplacement(contexte["documents"])

    sortie = outil.invoke(
        {
            "ancre": "offre",
            "entity_id": str(contexte["offre"].id),
            "elements": ["block_003", "block_004"],
            "nouveaux_elements": ["un seul contenu"],
        }
    )

    assert sortie["ok"] is False
    assert "désalignées" in sortie["error"]["message"]


def test_l_outil_refuse_les_deux_formes_melangees(contexte) -> None:
    outil = _outil_remplacement(contexte["documents"])

    sortie = outil.invoke(
        {
            "ancre": "offre",
            "entity_id": str(contexte["offre"].id),
            "element_id": "block_003",
            "nouveau_element": "Client : X",
            "elements": ["block_004"],
            "nouveaux_elements": ["1. Contexte"],
        }
    )

    assert sortie["ok"] is False
    assert "une seule forme" in sortie["error"]["message"]


def test_l_outil_exige_les_deux_listes_ensemble(contexte) -> None:
    outil = _outil_remplacement(contexte["documents"])

    sortie = outil.invoke(
        {
            "ancre": "offre",
            "entity_id": str(contexte["offre"].id),
            "elements": ["block_003"],
        }
    )

    assert sortie["ok"] is False
    assert "no" in sortie["error"]["message"].lower() or "liste" in sortie["error"]["message"]
