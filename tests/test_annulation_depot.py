"""Annulation d'un dépôt — pièce jointe retirée avant envoi (demande 04/10).

Un fichier joint au chat est d'abord un document CARSO : si l'utilisateur le
retire avant d'envoyer son message, la ligne **et** le fichier doivent partir
ensemble. Sinon le même nom reste pris et le fichier ne peut plus être redéposé
(« Un document de ce nom et de ce type existe déjà », constaté en navigateur).

Trois verrous sont testés ici : le dépôt vierge s'annule, un document engagé
dans le cycle est refusé, et un autre déposant n'y touche pas.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.services import DocumentService
from app.core.errors import ConflictError, NotFoundError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.document import AuditEvent, Document
from app.domain.enums import ActionAudit, StatutDocument, StatutMission, TypeDocument
from app.domain.execution import Mission
from app.domain.organization import Organisation
from tests.document_fixtures import build_pdf


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
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=2 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="deposant")


@pytest.fixture
def mission(session: Session) -> Mission:
    organisation = Organisation(nom="Client Formation")
    session.add(organisation)
    session.flush()
    active = Mission(
        organisation_id=organisation.id,
        reference="M-2026-01",
        titre="Mission bureautique",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(active)
    session.flush()
    return active


def _entree(
    *,
    mission_id: object,
    nom: str = "Fiche technique.pdf",
    created_by: str | None = "deposant",
    proposed_by_agent: str | None = None,
) -> DocumentUploadInput:
    return DocumentUploadInput(
        type_document=TypeDocument.FICHE_TECHNIQUE.value,
        nom=nom,
        stream=io.BytesIO(build_pdf("LISTE DES BENEFICIAIRES")),
        created_by=created_by,
        proposed_by_agent=proposed_by_agent,
        mission_id=mission_id,  # type: ignore[arg-type]
    )


def test_annuler_depot_retire_le_fichier_et_la_ligne(
    service: DocumentService, session: Session, storage: LocalDocumentStorage, mission: Mission
) -> None:
    """Le dépôt vierge s'efface entièrement — et le même nom redevient libre."""
    document = service.enregistrer_document(_entree(mission_id=mission.id))
    session.flush()
    chemin = storage.root / document.storage_path
    assert document.statut == StatutDocument.DRAFT.value
    assert chemin.is_file()

    retires = service.annuler_depot(document.id, "deposant")
    session.flush()

    assert retires >= 1
    assert not chemin.exists()
    assert session.get(Document, document.id) is None

    # Le blocage signalé : sans l'annulation, ce second dépôt répondait 409.
    deuxieme = service.enregistrer_document(_entree(mission_id=mission.id))
    session.flush()
    assert deuxieme.id != document.id

    evenements = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.DOCUMENT_DEPOT_ANNULE.value
            )
        )
    )
    assert len(evenements) == 1
    assert evenements[0].entity_id == str(document.id)


def test_annuler_depot_refuse_un_document_engage(
    service: DocumentService, session: Session, mission: Mission
) -> None:
    """Un document déjà dans le cycle (proposé par un agent) ne s'annule pas."""
    document = service.enregistrer_document(
        _entree(
            mission_id=mission.id,
            nom="Note de synthese.pdf",
            created_by=None,
            proposed_by_agent="agent_assistant_formateur",
        )
    )
    session.flush()
    assert document.statut == StatutDocument.PROPOSED.value

    with pytest.raises(ConflictError):
        service.annuler_depot(document.id, "deposant")

    session.flush()
    assert session.get(Document, document.id) is not None


def test_annuler_depot_refuse_un_autre_deposant(
    service: DocumentService, session: Session, storage: LocalDocumentStorage, mission: Mission
) -> None:
    """Seul le déposant annule son dépôt — et l'existence n'est pas révélée."""
    document = service.enregistrer_document(
        _entree(mission_id=mission.id, nom="CV.pdf", created_by="quelqu-un-dautre")
    )
    session.flush()
    chemin = storage.root / document.storage_path

    with pytest.raises(NotFoundError):
        service.annuler_depot(document.id, "deposant")

    session.flush()
    assert chemin.is_file()
    assert session.get(Document, document.id) is not None
