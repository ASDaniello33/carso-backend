"""Disponibilité des affectations (ADR 0012) : calculée, jamais stockée.

Règle validée le 26/10 : une personne approuvée sur une mission dont les
dates chevauchent une autre mission est **indisponible** pour celle-ci. Le
contrôle porte sur les **affectations approuvées** uniquement et sur les
**dates de mission** (bornes incluses : deux missions qui se touchent le
même jour se chevauchent). Une mission annulée ne bloque personne ; une
mission sans dates n'est pas évaluable (documenté, jamais inventé).

Tests purs sur SQLite mémoire, mêmes conventions que ``test_services.py``.
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

from app.application.dto import AffectationProposee, MissionDirecteInput
from app.application.services import MissionService
from app.application.services.affectation_service import AffectationService
from app.application.trace import Decision
from app.core.errors import ConflictError
from app.domain.base import Base
from app.domain.enums import (
    RoleMission,
    SourceAffectation,
    StatutAffectation,
    StatutMission,
)
from app.domain.execution import AffectationEquipe, Equipe
from app.domain.organization import Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_server_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[arg-type]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _equipe(session: Session, *, nom: str = "Rasoa") -> Equipe:
    equipe = Equipe(nom=nom, prenom="Mino", fonction="Formatrice")
    session.add(equipe)
    session.flush()
    return equipe


def _mission(
    session: Session,
    *,
    reference: str,
    date_debut: date | None = date(2026, 9, 1),
    date_fin: date | None = date(2026, 9, 5),
) -> object:
    org = Organisation(nom=f"Client {reference}")
    session.add(org)
    session.flush()
    return MissionService(session).creer_directe(
        MissionDirecteInput(
            organisation_id=org.id,
            reference=reference,
            titre=f"Mission {reference}",
            date_debut=date_debut,
            date_fin=date_fin,
        )
    )


def _proposer(service: AffectationService, mission, equipe: Equipe):
    return service.proposer(
        AffectationProposee(
            mission_id=mission.id,
            equipe_id=equipe.id,
            role_dans_mission=RoleMission.FORMATEUR.value,
            source=SourceAffectation.MANUAL.value,
            proposed_by="test",
        )
    )


# --- Chevauchement ----------------------------------------------------------


def test_chevauchement_d_un_seul_jour_refuse(session: Session) -> None:
    """M1 du 1 au 5, M2 du 3 au 7 : Rasoa est en mission les 3, 4 et 5."""
    m1 = _mission(session, reference="M-DISP-1")
    m2 = _mission(session, reference="M-DISP-2", date_debut=date(2026, 9, 3), date_fin=date(2026, 9, 7))
    equipe = _equipe(session)
    service = AffectationService(session)

    affectation1 = _proposer(service, m1, equipe)
    service.approuver(affectation1.id, Decision(decided_by="chef"))

    with pytest.raises(ConflictError, match="pas disponible") as exc_info:
        _proposer(service, m2, equipe)

    conflits = exc_info.value.details["missions_en_conflit"]
    assert conflits[0]["reference"] == "M-DISP-1"


def test_mission_adjacente_autorisee(session: Session) -> None:
    """M1 finit le 5, M3 commence le 6 : aucun jour commun."""
    m1 = _mission(session, reference="M-DISP-ADJ-1")
    m3 = _mission(session, reference="M-DISP-ADJ-3", date_debut=date(2026, 9, 6), date_fin=date(2026, 9, 12))
    equipe = _equipe(session)
    service = AffectationService(session)

    affectation1 = _proposer(service, m1, equipe)
    service.approuver(affectation1.id, Decision(decided_by="chef"))

    affectation3 = _proposer(service, m3, equipe)
    assert affectation3.statut == StatutAffectation.PROPOSEE.value


def test_bornes_incluses_le_meme_jour_compte(session: Session) -> None:
    """M1 finit le 5, M2 commence le 5 : le 5 est commun, chevauchement."""
    m1 = _mission(session, reference="M-DISP-B-1")
    m2 = _mission(session, reference="M-DISP-B-2", date_debut=date(2026, 9, 5), date_fin=date(2026, 9, 8))
    equipe = _equipe(session)
    service = AffectationService(session)

    affectation1 = _proposer(service, m1, equipe)
    service.approuver(affectation1.id, Decision(decided_by="chef"))

    with pytest.raises(ConflictError, match="pas disponible"):
        _proposer(service, m2, equipe)


# --- Sources de blocage -----------------------------------------------------


def test_une_proposition_non_approuvee_ne_bloque_pas(session: Session) -> None:
    """Seule une affectation **approuvée** bloque : une proposition en attente
    n'entre jamais en vigueur (règle 6 [C])."""
    m1 = _mission(session, reference="M-DISP-PROP-1")
    m2 = _mission(session, reference="M-DISP-PROP-2", date_debut=date(2026, 9, 2), date_fin=date(2026, 9, 6))
    equipe = _equipe(session)
    service = AffectationService(session)

    _proposer(service, m1, equipe)  # jamais approuvée

    affectation2 = _proposer(service, m2, equipe)
    assert affectation2.statut == StatutAffectation.PROPOSEE.value


def test_une_mission_annulee_ne_bloque_pas(session: Session) -> None:
    m1 = _mission(session, reference="M-DISP-ANN-1")
    m2 = _mission(session, reference="M-DISP-ANN-2", date_debut=date(2026, 9, 2), date_fin=date(2026, 9, 6))
    equipe = _equipe(session)
    service = AffectationService(session)

    affectation1 = _proposer(service, m1, equipe)
    service.approuver(affectation1.id, Decision(decided_by="chef"))
    MissionService(session).annuler(m1.id, motif="Annulée par le bailleur")

    affectation2 = _proposer(service, m2, equipe)
    assert affectation2.statut == StatutAffectation.PROPOSEE.value


def test_mission_sans_dates_pas_de_controle(session: Session) -> None:
    """Une mission sans dates n'est pas évaluable : le contrôle ne s'applique
    pas (documenté) — aucun droit n'est inventé, aucune erreur non plus."""
    m1 = _mission(session, reference="M-DISP-SANS-DATE-1", date_debut=None, date_fin=None)
    m2 = _mission(session, reference="M-DISP-SANS-DATE-2", date_debut=None, date_fin=None)
    equipe = _equipe(session)
    service = AffectationService(session)

    affectation1 = _proposer(service, m1, equipe)
    service.approuver(affectation1.id, Decision(decided_by="chef"))

    affectation2 = _proposer(service, m2, equipe)
    assert affectation2.statut == StatutAffectation.PROPOSEE.value


def test_une_autre_personne_n_est_pas_bloquee(session: Session) -> None:
    """Le contrôle est **par personne** : le chevauchement de Rasoa ne
    concerne pas Tsiory."""
    m1 = _mission(session, reference="M-DISP-A-1")
    m2 = _mission(session, reference="M-DISP-A-2", date_debut=date(2026, 9, 3), date_fin=date(2026, 9, 7))
    rasoa = _equipe(session, nom="Rasoa")
    tsiory = _equipe(session, nom="Tsiory")
    service = AffectationService(session)

    affectation1 = _proposer(service, m1, rasoa)
    service.approuver(affectation1.id, Decision(decided_by="chef"))

    affectation2 = _proposer(service, m2, tsiory)
    assert affectation2.statut == StatutAffectation.PROPOSEE.value


# --- Re-contrôle à l'approbation ---------------------------------------------


def test_recontrole_a_l_approbation(session: Session) -> None:
    """Entre la proposition et la décision, une autre affectation a pu être
    approuvée sur les mêmes dates : l'approbation refuse aussi."""
    m1 = _mission(session, reference="M-DISP-RE-1")
    m2 = _mission(session, reference="M-DISP-RE-2", date_debut=date(2026, 9, 3), date_fin=date(2026, 9, 7))
    equipe = _equipe(session)
    service = AffectationService(session)

    proposition1 = _proposer(service, m1, equipe)
    proposition2 = _proposer(service, m2, equipe)

    # La première approbation passe : les deux propositions coexistent (aucune
    # n'est en vigueur tant qu'elle n'est pas approuvée).
    service.approuver(proposition1.id, Decision(decided_by="chef"))

    # La seconde approbation est refusée : le chevauchement est devenu réel.
    with pytest.raises(ConflictError, match="pas disponible"):
        service.approuver(proposition2.id, Decision(decided_by="chef"))

    assert (
        session.get(AffectationEquipe, proposition2.id).statut
        == StatutAffectation.PROPOSEE.value
    )


def test_aucune_disponibilite_stoquee(session: Session) -> None:
    """La disponibilité est **calculée**, jamais stockée : aucune colonne
    n'existe pour elle, aucun cache non plus."""
    colonnes = set(AffectationEquipe.__table__.columns.keys())
    assert not any("disponib" in c.lower() for c in colonnes)
    colonnes_equipes = set(Equipe.__table__.columns.keys())
    assert not any("disponib" in c.lower() for c in colonnes_equipes)
