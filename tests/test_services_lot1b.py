"""Lot C1 — tests du service participations (SQLite mémoire, sans commit).

Inscription unique par couple, compléments, traçabilité AuditEvent ; le pointage
de présence vit dans ``test_presence_service`` (il a quitté la participation le
24/09 : une présence se pointe pour une date).
"""

import re
from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import (
    ParticipationInscriptionInput,
    ParticipationUpdateInput,
)
from app.application.services import ParticipationService
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import StatutMission, StatutSessionFormation
from app.domain.execution import Beneficiaire, Mission, SessionFormation
from app.domain.organization import Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _audit_actions(session: Session) -> list[str]:
    return list(session.scalars(select(AuditEvent.action)))


def _seed_session(session: Session) -> SessionFormation:
    org = Organisation(nom="Client Part")
    session.add(org)
    session.flush()
    mission = Mission(
        organisation_id=org.id,
        reference="M-PART-01",
        titre="Mission part",
        date_debut=date(2026, 9, 1),
        date_fin=date(2026, 9, 5),
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    formation = SessionFormation(
        mission_id=mission.id,
        theme="Atelier",
        date_debut=date(2026, 9, 1),
        date_fin=date(2026, 9, 5),
        statut=StatutSessionFormation.PLANIFIE.value,
    )
    session.add(formation)
    session.flush()
    return formation


def _seed_beneficiaire(session: Session) -> Beneficiaire:
    ben = Beneficiaire(nom="Tazi", prenom="Sara")
    session.add(ben)
    session.flush()
    return ben


def test_inscrire_et_lister(session: Session) -> None:
    formation = _seed_session(session)
    ben = _seed_beneficiaire(session)
    service = ParticipationService(session)
    part = service.inscrire(formation.id, ParticipationInscriptionInput(beneficiaire_id=ben.id))
    assert part.id is not None
    assert len(service.lister_pour_session(formation.id)) == 1
    assert _audit_actions(session) == ["participation.inscrite"]


def test_double_inscription_409(session: Session) -> None:
    formation = _seed_session(session)
    ben = _seed_beneficiaire(session)
    service = ParticipationService(session)
    service.inscrire(formation.id, ParticipationInscriptionInput(beneficiaire_id=ben.id))
    with pytest.raises(ConflictError):
        service.inscrire(formation.id, ParticipationInscriptionInput(beneficiaire_id=ben.id))


def test_inscrire_entites_absentes_404(session: Session) -> None:
    service = ParticipationService(session)
    ben = _seed_beneficiaire(session)
    with pytest.raises(NotFoundError):
        service.inscrire(uuid4(), ParticipationInscriptionInput(beneficiaire_id=ben.id))
    formation = _seed_session(session)
    with pytest.raises(NotFoundError):
        service.inscrire(formation.id, ParticipationInscriptionInput(beneficiaire_id=uuid4()))


def test_l_inscription_ne_porte_plus_de_presence(session: Session) -> None:
    """Le pointage a quitté la participation : le lire ici serait un mensonge."""
    formation = _seed_session(session)
    ben = _seed_beneficiaire(session)
    service = ParticipationService(session)
    part = service.inscrire(formation.id, ParticipationInscriptionInput(beneficiaire_id=ben.id))

    assert not hasattr(part, "presence")
    assert not hasattr(part, "heure_arrivee")


def test_modifier_evaluation(session: Session) -> None:
    formation = _seed_session(session)
    ben = _seed_beneficiaire(session)
    service = ParticipationService(session)
    part = service.inscrire(formation.id, ParticipationInscriptionInput(beneficiaire_id=ben.id))
    service.modifier(
        part.id,
        ParticipationUpdateInput(evaluation="Réussi", observations="Assidue"),
    )
    assert service.obtenir(part.id).evaluation == "Réussi"
    with pytest.raises(ValidationError):
        service.modifier(part.id, ParticipationUpdateInput())
    actions = list(session.scalars(select(AuditEvent.action)))
    assert actions == ["participation.inscrite", "participation.modifiee"]
