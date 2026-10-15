"""Service présences — la règle du pointage daté (droite confirmée 24/09).

Ce que ces tests tiennent, et pourquoi :

- **la plage** : une session se pointe entre ses dates, ou à défaut entre celles
  de sa mission — jamais ailleurs, jamais « aujourd'hui » par défaut ;
- **l'unité** : pointe une personne pour une date, corrige sans dupliquer ;
- **la trace** : chaque pointage écrit ``presence.enregistree`` avec sa date ;
- **l'idempotence** : re-pointer la même valeur n'écrit rien de plus.

SQLite mémoire via les hooks de compilation de ``conftest`` ; aucun commit par le
service (la transaction appartient à l'appelant).
"""

from __future__ import annotations

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
    BeneficiaireInput,
    ParticipationInscriptionInput,
    PresenceLotEntree,
    PresencePointageInput,
)
from app.application.services import (
    BeneficiaireService,
    ParticipationService,
    PresenceService,
)
from app.core.errors import NotFoundError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, StatutMission, StatutSessionFormation
from app.domain.execution import Mission, SessionFormation
from app.domain.organization import Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


JOUR_1 = date(2026, 9, 1)
JOUR_3 = date(2026, 9, 3)
JOUR_5 = date(2026, 9, 5)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _mission(
    session: Session,
    *,
    debut: date | None = JOUR_1,
    fin: date | None = JOUR_5,
) -> Mission:
    organisation = Organisation(nom="Client Présences")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-PRES-01",
        titre="Mission présences",
        date_debut=debut,
        date_fin=fin,
        statut=StatutMission.EN_COURS.value,
    )
    session.add(mission)
    session.flush()
    return mission


def _session_formation(
    session: Session,
    mission: Mission,
    *,
    debut: date | None = None,
    fin: date | None = None,
) -> SessionFormation:
    formation = SessionFormation(
        mission_id=mission.id,
        theme="Atelier",
        date_debut=debut,
        date_fin=fin,
        statut=StatutSessionFormation.PLANIFIEE.value,
    )
    session.add(formation)
    session.flush()
    return formation


def _inscrire(session: Session, session_id) -> object:
    personne = BeneficiaireService(session).creer(
        BeneficiaireInput(nom="Sow", prenom="Fatou")
    )
    return ParticipationService(session).inscrire(
        session_id, ParticipationInscriptionInput(beneficiaire_id=personne.id)
    )


# --- La plage de pointage --------------------------------------------------------


def test_la_plage_vient_de_la_session(session: Session) -> None:
    mission = _mission(session)
    formation = _session_formation(session, mission, debut=JOUR_1, fin=JOUR_5)

    assert PresenceService(session).plage(formation.id) == (JOUR_1, JOUR_5)


def test_la_plage_retombe_sur_la_mission_si_la_session_n_a_pas_de_dates(
    session: Session,
) -> None:
    mission = _mission(session)
    formation = _session_formation(session, mission)

    assert PresenceService(session).plage(formation.id) == (JOUR_1, JOUR_5)


def test_la_plage_garde_la_borne_connue(session: Session) -> None:
    """Une seule date renseignée ne vaut pas « aucune borne »."""
    mission = _mission(session, debut=None, fin=None)
    formation = _session_formation(session, mission, debut=JOUR_3, fin=None)

    assert PresenceService(session).plage(formation.id) == (JOUR_3, None)


def test_la_plage_d_une_session_inconnue_leve_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        PresenceService(session).plage(uuid4())


# --- Pointage unitaire -----------------------------------------------------------


def test_pointer_une_personne_pour_une_date(session: Session) -> None:
    mission = _mission(session)
    formation = _session_formation(session, mission)
    participation = _inscrire(session, formation.id)

    presence = PresenceService(session, actor_id="test").pointer_participation(
        participation.id,
        PresencePointageInput(date=JOUR_3, presence="retard", heure_arrivee=None),
    )
    session.flush()

    assert (presence.date, presence.presence) == (JOUR_3, "retard")
    service = PresenceService(session)
    assert service.compter_pour_participation(participation.id) == 1
    assert [(p.date, p.presence) for p in service.lister_pour_participation(participation.id)] == [
        (JOUR_3, "retard")
    ]


def test_pointer_une_participation_inconnue_leve_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        PresenceService(session).pointer_participation(
            uuid4(), PresencePointageInput(date=JOUR_1, presence="present")
        )


def test_la_correction_ne_cree_pas_un_second_pointage(session: Session) -> None:
    mission = _mission(session)
    formation = _session_formation(session, mission)
    participation = _inscrire(session, formation.id)
    service = PresenceService(session, actor_id="test")

    premier = service.pointer_participation(
        participation.id, PresencePointageInput(date=JOUR_1, presence="absent")
    )
    corrige = service.pointer_participation(
        participation.id, PresencePointageInput(date=JOUR_1, presence="present")
    )

    assert corrige.id == premier.id
    assert corrige.presence == "present"
    assert service.compter_pour_participation(participation.id) == 1


def test_la_valeur_de_presence_est_controlee(session: Session) -> None:
    mission = _mission(session)
    formation = _session_formation(session, mission)
    participation = _inscrire(session, formation.id)

    with pytest.raises(ValidationError) as erreur:
        PresenceService(session).pointer_participation(
            participation.id, PresencePointageInput(date=JOUR_1, presence="peut-etre")
        )
    assert "present" in erreur.value.details["valeurs_valides"]


# --- Trace et idempotence --------------------------------------------------------


def test_chaque_pointage_est_trace_avec_sa_date(session: Session) -> None:
    mission = _mission(session)
    formation = _session_formation(session, mission)
    participation = _inscrire(session, formation.id)

    PresenceService(session, actor_id="test").pointer(
        formation.id,
        JOUR_3,
        [PresenceLotEntree(participation_id=participation.id, presence="present")],
    )
    session.flush()

    evenements = list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.action == ActionAudit.PRESENCE_ENREGISTREE.value)
        )
    )
    assert len(evenements) == 1
    assert evenements[0].after["date"] == JOUR_3.isoformat()
    assert evenements[0].after["presence"] == "present"


def test_repointer_la_meme_valeur_n_ajoute_aucune_trace(session: Session) -> None:
    """Un enregistrement identique n'est pas une décision : il n'écrit rien."""
    mission = _mission(session)
    formation = _session_formation(session, mission)
    participation = _inscrire(session, formation.id)
    service = PresenceService(session, actor_id="test")
    ligne = PresenceLotEntree(participation_id=participation.id, presence="present")

    service.pointer(formation.id, JOUR_1, [ligne])
    service.pointer(formation.id, JOUR_1, [ligne])
    session.flush()

    pointages = list(
        session.scalars(
            select(AuditEvent.id).where(
                AuditEvent.action == ActionAudit.PRESENCE_ENREGISTREE.value
            )
        )
    )
    assert len(pointages) == 1


def test_lire_la_fiche_d_une_session_inconnue_leve_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        PresenceService(session).lister_pour_session(uuid4())
