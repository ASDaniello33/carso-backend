"""Modification des dates d'une mission après planification (ADR 0015).

La demande : l'utilisateur doit pouvoir modifier la date de début et la date
de fin d'une mission ; la modification implique le changement des dates de la
session déjà créée **et** le réalignement des statuts de la mission et de la
session. Règles tranchées avec l'utilisateur :

- le statut se **recalcule depuis les dates** (le calendrier commande, comme
  partout ailleurs — le réalignement est pas à pas, jamais un saut) ;
- une mission ``annule`` est **figée** : ses dates ne se modifient plus ;
- une session créée à la main restée ``en_planification`` **garde son
  statut** : sa confirmation reste un geste humain distinct.

Tests purs sur SQLite mémoire, mêmes conventions que
``test_cycle_de_vie_mission_session.py`` : hooks de compilation pour les
types PG, aucune assertion sur le commit (la transaction appartient à
l'appelant).
"""

import re
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import MissionDirecteInput, SessionPlanificationInput, SessionUpdateInput
from app.application.services import MissionService, SessionService
from app.application.services.cycle_de_vie_service import CycleDeVieService
from app.core.errors import ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import (
    ActionAudit,
    ActorType,
    StatutMission,
    StatutSessionFormation,
)
from app.domain.execution import Mission
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


def _mission(
    session: Session,
    *,
    reference: str,
    date_debut: date,
    date_fin: date,
) -> Mission:
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


def _evenements(session: Session, action: str, entity_type: str) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == action,
                AuditEvent.entity_type == entity_type,
            )
        )
    )


# --- Dates reportées sur la session existante -------------------------------


def test_modifier_les_dates_deplace_la_session_existante(session: Session) -> None:
    """La session créée à la planification reçoit les nouvelles dates."""
    mission = _mission(
        session, reference="M-ADR15-1", date_debut=date(2026, 12, 1), date_fin=date(2026, 12, 5)
    )
    MissionService(session).planifier(mission.id)
    session_id_avant = mission.session.id

    mission = MissionService(session).modifier(
        mission.id,
        date_debut=date(2026, 12, 20),
        date_fin=date(2026, 12, 22),
        acteur=ActorType.HUMAIN.value,
    )

    assert mission.date_debut == date(2026, 12, 20)
    assert mission.date_fin == date(2026, 12, 22)
    assert mission.session is not None
    assert mission.session.id == session_id_avant  # la même session, déplacée
    assert mission.session.date_debut == date(2026, 12, 20)
    assert mission.session.date_fin == date(2026, 12, 22)
    # Dates futures : les deux entités restent « planifié », rien à réaligner.
    assert mission.statut == StatutMission.PLANIFIE.value
    assert mission.session.statut == StatutSessionFormation.PLANIFIE.value
    # La propagation de session est tracée (date posée sur la session).
    traces = _evenements(session, ActionAudit.SESSION_MODIFIEE.value, "session")
    assert any(
        (ev.after or {}).get("date_debut") == "2026-12-20"
        and (ev.after or {}).get("date_fin") == "2026-12-22"
        for ev in traces
    )


def test_report_en_futur_realigne_les_statuts_depuis_en_cours(session: Session) -> None:
    """Mission repoussée dans le futur : les statuts recalculés redeveniennent « planifié »."""
    mission = _mission(
        session, reference="M-ADR15-2", date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 10)
    )
    MissionService(session).planifier(mission.id)
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 6, 5))
    assert mission.statut == StatutMission.EN_COURS.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.EN_COURS.value

    mission = MissionService(session).modifier(
        mission.id,
        date_debut=date(2026, 12, 20),
        date_fin=date(2026, 12, 22),
        acteur=ActorType.HUMAIN.value,
    )

    # Le calendrier commande : les deux entités repassent « planifié ».
    assert mission.statut == StatutMission.PLANIFIE.value
    assert mission.session.statut == StatutSessionFormation.PLANIFIE.value
    traces_mission = _evenements(session, ActionAudit.MISSION_STATUT_CHANGE.value, "mission")
    assert any(
        (ev.event_metadata or {}).get("realigne_sur_nouvelles_dates")
        and (ev.event_metadata or {}).get("cible_calendaire") == "planifie"
        and (ev.after or {}).get("statut") == "planifie"
        for ev in traces_mission
    )
    traces_session = _evenements(session, ActionAudit.SESSION_STATUT_CHANGE.value, "session")
    assert any(
        (ev.event_metadata or {}).get("realigne_sur_nouvelles_dates")
        and (ev.after or {}).get("statut") == "planifie"
        for ev in traces_session
    )


def test_raccourcissement_dans_le_passe_realigne_en_cloture(session: Session) -> None:
    """Fenêtre déplacée tout entière dans le passé : les deux entités se clôturent."""
    mission = _mission(
        session, reference="M-ADR15-3", date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 30)
    )
    MissionService(session).planifier(mission.id)
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 6, 10))
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 7, 1))
    assert mission.statut == StatutMission.CLOTURE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.CLOTURE.value

    mission = MissionService(session).modifier(
        mission.id,
        date_fin=date(2026, 6, 20),
        acteur=ActorType.HUMAIN.value,
    )

    # La fenêtre reste passée : la cible calendaire est « cloture » — les
    # statuts s'y trouvaient déjà, aucun réalignement nécessaire.
    assert mission.date_fin == date(2026, 6, 20)
    assert mission.statut == StatutMission.CLOTURE.value
    assert mission.session.date_fin == date(2026, 6, 20)
    assert mission.session.statut == StatutSessionFormation.CLOTURE.value


def test_deplacement_dans_le_present_realigne_en_cours(session: Session) -> None:
    """Mission clôturée ramenée sur le présent : les statuts repassent « en cours »."""
    mission = _mission(
        session, reference="M-ADR15-4", date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 10)
    )
    MissionService(session).planifier(mission.id)
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 7, 1))
    assert mission.statut == StatutMission.CLOTURE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.CLOTURE.value

    mission = MissionService(session).modifier(
        mission.id,
        date_debut=date(2026, 11, 1),
        date_fin=date(2026, 12, 31),
        acteur=ActorType.HUMAIN.value,
    )

    # Aujourd'hui (9/11/2026) encadré par les nouvelles dates : « en cours ».
    assert mission.statut == StatutMission.EN_COURS.value
    assert mission.session.statut == StatutSessionFormation.EN_COURS.value


def test_realignement_pas_a_pas_jamais_de_saut(session: Session) -> None:
    """La marche calendaire passe par chaque cran, un AuditEvent par cran."""
    mission = _mission(
        session, reference="M-ADR15-5", date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 30)
    )
    MissionService(session).planifier(mission.id)

    mission = MissionService(session).modifier(
        mission.id,
        date_debut=date(2026, 5, 1),
        date_fin=date(2026, 5, 10),
        acteur=ActorType.HUMAIN.value,
    )

    # Fenêtre tout entière passée : « planifié » → « en cours » → « clôturé ».
    assert mission.statut == StatutMission.CLOTURE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.CLOTURE.value
    traces = [
        ev
        for ev in _evenements(session, ActionAudit.MISSION_STATUT_CHANGE.value, "mission")
        if (ev.event_metadata or {}).get("realigne_sur_nouvelles_dates")
    ]
    assert len(traces) == 2
    assert [(ev.event_metadata or {}).get("statut_avant") for ev in traces] == [
        "planifie",
        "en_cours",
    ]
    assert [(ev.after or {}).get("statut") for ev in traces] == ["en_cours", "cloture"]


# --- Garde-fous --------------------------------------------------------------


def test_mission_annulee_dates_figees(session: Session) -> None:
    """Une mission annulée est figée : ses dates ne se modifient plus."""
    mission = _mission(
        session, reference="M-ADR15-6", date_debut=date(2026, 12, 1), date_fin=date(2026, 12, 5)
    )
    MissionService(session).planifier(mission.id)
    MissionService(session).annuler(mission.id)

    with pytest.raises(ValidationError, match="figée"):
        MissionService(session).modifier(
            mission.id,
            date_debut=date(2027, 1, 10),
            date_fin=date(2027, 1, 12),
            acteur=ActorType.HUMAIN.value,
        )
    assert mission.date_debut == date(2026, 12, 1)
    assert mission.session is not None
    assert mission.session.date_debut == date(2026, 12, 1)
    # Le titre reste modifiable : seul le calendrier est figé.
    mission = MissionService(session).modifier(
        mission.id, titre="Mission replanifiée plus tard", acteur=ActorType.HUMAIN.value
    )
    assert mission.titre == "Mission replanifiée plus tard"


def test_dates_de_session_suivent_la_mission(session: Session) -> None:
    """Les dates de session se changent par la mission, jamais en direct."""
    mission = _mission(
        session, reference="M-ADR15-7", date_debut=date(2026, 12, 1), date_fin=date(2026, 12, 5)
    )
    MissionService(session).planifier(mission.id)
    sessions = SessionService(session)
    assert mission.session is not None

    with pytest.raises(ValidationError, match="suivent celles de sa mission"):
        sessions.modifier(
            mission.session.id,
            SessionUpdateInput(date_debut=date(2027, 2, 10), date_fin=date(2027, 2, 12)),
        )
    assert mission.session.date_debut == date(2026, 12, 1)

    # Session désynchronisée de sa mission (correction historique) : la porte
    # se rouvre dès que les deux ne portent plus les mêmes dates.
    mission.session.date_fin = date(2026, 12, 25)
    session.flush()
    corrigee = sessions.modifier(
        mission.session.id, SessionUpdateInput(date_fin=date(2026, 12, 26))
    )
    assert corrigee.date_fin == date(2026, 12, 26)


def test_session_manuelle_en_planification_garde_son_statut(session: Session) -> None:
    """Une session créée à la main reste « en planification » : la confirmation reste humaine."""
    mission = _mission(
        session, reference="M-ADR15-8", date_debut=date(2026, 12, 1), date_fin=date(2026, 12, 5)
    )
    sessions = SessionService(session)
    sessions.planifier(
        SessionPlanificationInput(
            mission_id=mission.id,
            theme="Session manuelle",
            date_debut=date(2026, 12, 1),
            date_fin=date(2026, 12, 5),
            lieu="Toliara",
        )
    )
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.EN_PLANIFICATION.value

    MissionService(session).modifier(
        mission.id,
        date_debut=date(2026, 12, 10),
        date_fin=date(2026, 12, 12),
        acteur=ActorType.HUMAIN.value,
    )

    # Les dates suivent, le statut non : la confirmation reste un geste distinct.
    assert mission.session.date_debut == date(2026, 12, 10)
    assert mission.session.statut == StatutSessionFormation.EN_PLANIFICATION.value

    sessions.confirmer(mission.session.id)
    assert mission.session.statut == StatutSessionFormation.PLANIFIE.value
    assert mission.statut == StatutMission.PLANIFIE.value


# --- Cohabitation avec le balayage de cycle de vie (ADR 0012) -----------------


def test_le_balayage_reprend_le_controle_apres_realignement(session: Session) -> None:
    """Après un report, le calendrier reprend la main dès le prochain balayage."""
    mission = _mission(
        session, reference="M-ADR15-9", date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 30)
    )
    MissionService(session).planifier(mission.id)
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 6, 10))
    assert mission.statut == StatutMission.EN_COURS.value

    MissionService(session).modifier(
        mission.id,
        date_debut=date(2026, 12, 20),
        date_fin=date(2026, 12, 22),
        acteur=ActorType.HUMAIN.value,
    )
    assert mission.statut == StatutMission.PLANIFIE.value

    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 12, 21))
    assert mission.statut == StatutMission.EN_COURS.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.EN_COURS.value
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 12, 25))
    assert mission.statut == StatutMission.CLOTURE.value
    assert mission.session.statut == StatutSessionFormation.CLOTURE.value


def test_modifier_sans_les_dates_ne_bouge_rien(session: Session) -> None:
    """Sans dates fournies : aucun report, aucun réalignement, aucune trace de session."""
    mission = _mission(
        session, reference="M-ADR15-10", date_debut=date(2026, 12, 1), date_fin=date(2026, 12, 5)
    )
    MissionService(session).planifier(mission.id)

    mission = MissionService(session).modifier(
        mission.id, titre="Titre repris", acteur=ActorType.HUMAIN.value
    )
    assert mission.titre == "Titre repris"
    assert mission.date_debut == date(2026, 12, 1)
    assert mission.session is not None
    assert mission.session.date_debut == date(2026, 12, 1)
    assert mission.statut == StatutMission.PLANIFIE.value
    assert (
        _evenements(session, ActionAudit.SESSION_MODIFIEE.value, "session") == []
    )


def test_debut_apres_fin_toujours_refuse_sur_mission_planifiee(session: Session) -> None:
    """Le contrôle début ≤ fin s'applique aussi après planification, sans rien écrire."""
    mission = _mission(
        session, reference="M-ADR15-11", date_debut=date(2026, 12, 1), date_fin=date(2026, 12, 5)
    )
    MissionService(session).planifier(mission.id)

    with pytest.raises(ValidationError, match="date de début.*date de fin"):
        MissionService(session).modifier(
            mission.id,
            date_debut=date(2026, 12, 30),
            date_fin=date(2026, 12, 10),
            acteur=ActorType.HUMAIN.value,
        )
    assert mission.date_debut == date(2026, 12, 1)
    assert mission.date_fin == date(2026, 12, 5)
    assert mission.session is not None
    assert mission.session.date_debut == date(2026, 12, 1)
