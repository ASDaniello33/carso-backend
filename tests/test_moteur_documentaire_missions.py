"""Chaîne modèle ancrée sur une mission ou une session (INC-129).

L'assistant formateur travaille sur des missions et des sessions, pas sur des
offres. Le moteur ne dépend pourtant pas de l'objet : ce qui change est la
**destination** de la copie (dossier documentaire et périmètre d'accès) et le
type de dépôt. Ces tests vérifient que la même chaîne s'applique sans dupliquer
de code, et que les ancres hors périmètre sont refusées explicitement.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document as LireDocx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.services.document_service import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.application.services.moteur_documentaire_service import MoteurDocumentaireService
from app.core.errors import NotFoundError, ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.enums import StatutMission
from app.domain.execution import Mission, SessionFormation
from app.domain.organization import Organisation
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
def organisation(db_session) -> Organisation:
    organisation = Organisation(nom="Client Missions")
    db_session.add(organisation)
    db_session.flush()
    return organisation


@pytest.fixture
def mission(db_session, organisation) -> Mission:
    mission = Mission(
        organisation_id=organisation.id,
        reference="MISSION-TEST-01",
        titre="Formation des formateurs",
        statut=StatutMission.EN_PLANIFICATION.value,
    )
    db_session.add(mission)
    db_session.flush()
    return mission


@pytest.fixture
def session(db_session, mission) -> SessionFormation:
    session = SessionFormation(mission_id=mission.id, theme="Session de test")
    db_session.add(session)
    db_session.flush()
    return session


def _modele(db_session, stockage, organisation, octets: bytes | None = None):
    documents = DocumentService(db_session, storage=stockage, actor_id="test")
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="modele_client.docx",
            stream=io.BytesIO(octets or modele_docx()),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    modele = ModeleDocumentService(db_session).enregistrer_modele(
        organisation.id,
        nom="modele_client.docx",
        type_document="fiche_technique",
        document_template_id=document.id,
        registered_by="test",
    )
    db_session.flush()
    return documents, modele


def _service(db_session, documents) -> MoteurDocumentaireService:
    return MoteurDocumentaireService(
        db_session, document_service=documents, actor_id="agent_assistant_formateur"
    )


def test_copier_le_modele_dans_une_mission(db_session, stockage, organisation, mission) -> None:
    documents, modele = _modele(db_session, stockage, organisation)
    service = _service(db_session, documents)

    document, modele_utilise, elements = service.copier_modele_dans_objet(
        "mission", mission.id, modele_id=modele.id, type_document="fiche_technique"
    )

    assert modele_utilise.id == modele.id
    assert document.mission_id == mission.id
    assert document.offre_id is None and document.session_id is None
    assert document.type_document == "fiche_technique"
    assert document.doc_metadata["ancre"] == "mission"
    assert elements[0].element_id == "block_001"
    _nom, chemin = documents.telecharger(document.id)
    assert chemin.read_bytes() == documents.telecharger(modele.document_template_id)[1].read_bytes()

    # La même chaîne s'édite : structure, texte, HTML.
    structure = service.structure_document("mission", mission.id)
    assert structure["ancre"] == "mission"
    assert structure["reference"] == "MISSION-TEST-01"
    resultat = service.remplacer_element(
        "mission", mission.id, "block_003", "Client : FID Madagascar"
    )
    assert resultat["version"] == document.version + 1
    rempli = service.remplir(
        "mission",
        mission.id,
        [
            {
                "element_id": "block_005",
                "contenu_html": "<h2>1. Contexte</h2><p>Mission confirmée.</p>",
            }
        ],
    )
    assert rempli["blocs_inseres"] == 2
    _nom, chemin = documents.telecharger(__import__("uuid").UUID(rempli["document_id"]))
    copie = LireDocx(str(chemin))
    assert "Mission confirmée." in [p.text for p in copie.paragraphs]
    assert copie.sections[0].header.paragraphs[0].text == "CARSO — Direction des programmes"


def test_copier_le_modele_dans_une_session(
    db_session, stockage, organisation, mission, session
) -> None:
    documents, modele = _modele(db_session, stockage, organisation)
    service = _service(db_session, documents)

    document, _modele_utilise, elements = service.copier_modele_dans_objet(
        "session", session.id, modele_id=modele.id, type_document="rapport"
    )

    assert document.session_id == session.id
    assert document.type_document == "rapport"
    assert document.nom.startswith("modele-MISSION-TEST-01-session-")
    assert elements
    structure = service.structure_document("session", session.id)
    assert structure["ancre"] == "session"
    assert structure["nombre_elements"] >= 6


def test_le_modele_actif_du_type_suffit_pour_une_mission(
    db_session, stockage, organisation, mission
) -> None:
    """Sans `modele_id`, le modèle du type demandé est pris (aucune invention)."""
    documents, modele = _modele(db_session, stockage, organisation)
    service = _service(db_session, documents)

    document, modele_utilise, _elements = service.copier_modele_dans_objet(
        "mission", mission.id, type_document="fiche_technique"
    )

    assert modele_utilise.id == modele.id
    assert document.mission_id == mission.id


def test_plusieurs_modeles_sans_choix_demande_lequel(
    db_session, stockage, organisation, mission
) -> None:
    documents, modele = _modele(db_session, stockage, organisation)
    ModeleDocumentService(db_session).enregistrer_modele(
        organisation.id,
        nom="modele_rapport.docx",
        type_document="rapport",
        document_template_id=documents.derniere_version("modele_client.docx", "template").id,
        registered_by="test",
    )
    db_session.flush()

    with pytest.raises(ValidationError, match="Préciser|préciser"):
        _service(db_session, documents).copier_modele_dans_objet("mission", mission.id)


def test_ancre_hors_perimetre_refusee(db_session, stockage, organisation) -> None:
    documents, modele = _modele(db_session, stockage, organisation)

    with pytest.raises(ValidationError, match="non prise en charge"):
        _service(db_session, documents).copier_modele_dans_objet("equipe", uuid4())


def test_objet_inconnu_repond_introuvable(db_session, stockage, organisation) -> None:
    documents, modele = _modele(db_session, stockage, organisation)

    with pytest.raises(NotFoundError, match="Mission"):
        _service(db_session, documents).copier_modele_dans_objet("mission", uuid4())


def test_sans_modele_pour_l_organisation_oriente_vers_la_generation(
    db_session, stockage, organisation, mission
) -> None:
    documents = DocumentService(db_session, storage=stockage, actor_id="test")

    with pytest.raises(NotFoundError, match="generer_document_professionnel"):
        _service(db_session, documents).copier_modele_dans_objet("mission", mission.id)
