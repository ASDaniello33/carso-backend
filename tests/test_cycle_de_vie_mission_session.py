"""Cycle de vie mission/session (ADR 0012) : planification, session créée,
transitions automatiques, annulation symétrique, traçabilité SYSTEME.

Tests purs sur SQLite mémoire, mêmes conventions que ``test_services.py`` :
hooks de compilation pour les types PG, aucune assertion sur le commit (la
transaction appartient à l'appelant).
"""

import re
from collections.abc import Iterator
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import MissionDirecteInput
from app.application.services import MissionService, SessionService
from app.application.services.cycle_de_vie_service import CycleDeVieService
from app.core.errors import ConflictError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import (
    ActionAudit,
    ActorType,
    RoleMission,
    StatutAffectation,
    StatutMission,
    StatutSessionFormation,
)
from app.domain.execution import AffectationEquipe, Equipe, Mission
from app.domain.organization import Organisation
from app.domain.state_machines import TRANSITIONS


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


def _organisation(session: Session) -> Organisation:
    org = Organisation(nom="Client Cycle de vie")
    session.add(org)
    session.flush()
    return org


def _mission_directe(
    session: Session,
    *,
    reference: str = "M-CV-01",
    date_debut: date | None = date(2026, 9, 1),
    date_fin: date | None = date(2026, 9, 5),
) -> Mission:
    org = _organisation(session)
    return MissionService(session).creer_directe(
        MissionDirecteInput(
            organisation_id=org.id,
            reference=reference,
            titre="Mission cycle de vie",
            date_debut=date_debut,
            date_fin=date_fin,
        )
    )


def _equipe(session: Session, *, nom: str = "Rasoa") -> Equipe:
    personne = Equipe(nom=nom, prenom="Mino", fonction="Responsable RH")
    session.add(personne)
    session.flush()
    return personne


def _affectation_approuvee(
    session: Session, mission: Mission, equipe: Equipe
) -> AffectationEquipe:
    affectation = AffectationEquipe(
        mission_id=mission.id,
        equipe_id=equipe.id,
        role_dans_mission=RoleMission.FORMATEUR.value,
        statut=StatutAffectation.APPROUVEE.value,
    )
    session.add(affectation)
    session.flush()
    return affectation


# --- Planification ----------------------------------------------------------


def test_une_mission_nait_en_planification(session: Session) -> None:
    """ADR 0012 : la création ne planifie rien — la mission se prépare d'abord."""
    mission = _mission_directe(session)
    assert mission.statut == StatutMission.EN_PLANIFICATION.value
    assert mission.session is None


def test_planification_cree_la_session_et_passe_la_mission(session: Session) -> None:
    mission = _mission_directe(session)

    mission = MissionService(session).planifier(mission.id)

    assert mission.statut == StatutMission.PLANIFIE.value
    session_form = mission.session
    assert session_form is not None
    assert session_form.statut == StatutSessionFormation.PLANIFIE.value
    assert session_form.mission_id == mission.id
    # Les dates et le thème de la session viennent de la mission.
    assert session_form.date_debut == mission.date_debut
    assert session_form.date_fin == mission.date_fin
    assert session_form.theme == mission.titre

    actions = session.scalars(select(AuditEvent.action)).all()
    assert ActionAudit.SESSION_CREEE.value in actions
    assert ActionAudit.MISSION_PLANIFIEE.value in actions


def test_planification_exige_les_dates(session: Session) -> None:
    """Sans dates, la session ne pourrait ni démarrer ni se clôturer : refus."""
    mission = _mission_directe(session, date_debut=None, date_fin=None)

    with pytest.raises(ValidationError, match="date de début et une date de fin"):
        MissionService(session).planifier(mission.id)

    assert mission.statut == StatutMission.EN_PLANIFICATION.value
    assert mission.session is None


def test_planification_sans_equipe_approuvee_avertit_sans_bloquer(session: Session) -> None:
    """Règle validée : une mission sans équipe reste planifiable (avertissement)."""
    mission = _mission_directe(session)
    equipe = _equipe(session)
    # Une proposition en attente ne compte pas : elle n'entre jamais en vigueur.
    session.add(
        AffectationEquipe(
            mission_id=mission.id,
            equipe_id=equipe.id,
            role_dans_mission=RoleMission.FORMATEUR.value,
            statut=StatutAffectation.PROPOSEE.value,
        )
    )
    session.flush()

    service = MissionService(session)
    service.planifier(mission.id)
    assert "Aucune équipe" in (service.avertissement_equipe(mission.id) or "")

    _affectation_approuvee(session, mission, equipe)
    session.flush()
    assert MissionService(session).avertissement_equipe(mission.id) is None


def test_planification_est_idempotente(session: Session) -> None:
    """Replanifier ne crée jamais une seconde session."""
    mission = _mission_directe(session)
    service = MissionService(session)

    service.planifier(mission.id)
    mission = service.planifier(mission.id)

    assert mission.statut == StatutMission.PLANIFIE.value
    sessions = SessionService(session).lister_pour_mission(mission.id)
    assert len(sessions) == 1


def test_le_lieu_de_la_mission_devient_le_lieu_de_la_session(session: Session) -> None:
    from app.application.services.lieu_service import LieuService
    from app.application.dto import LieuInput

    lieu = LieuService(session).creer(LieuInput(nom="Toliara"))
    mission = _mission_directe(session)
    MissionService(session).changer_lieu(mission.id, lieu.id)

    mission = MissionService(session).planifier(mission.id)
    assert mission.session is not None
    assert mission.session.lieu == "Toliara"


# --- Transitions automatiques -----------------------------------------------


def test_balayage_demarre_et_cloture_aux_bonnes_dates(session: Session) -> None:
    """Jour J : en cours. Lendemain de la fin : clôturée. Les deux tracées SYSTEME."""
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)
    session_form = mission.session
    assert session_form is not None

    avant = date(2026, 8, 30)
    CycleDeVieService(session).appliquer_transitions_dues(avant)
    assert session_form.statut == StatutSessionFormation.PLANIFIE.value
    assert mission.statut == StatutMission.PLANIFIE.value

    # Jour J (1er septembre) : la session démarre, la mission suit.
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 1))
    assert session_form.statut == StatutSessionFormation.EN_COURS.value
    assert mission.statut == StatutMission.EN_COURS.value

    # Dernier jour : toujours en cours (la fin n'est pas « dépassée »).
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 5))
    assert session_form.statut == StatutSessionFormation.EN_COURS.value
    assert mission.statut == StatutMission.EN_COURS.value

    # Jour suivant la fin : clôture.
    compteurs = CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 6))
    assert session_form.statut == StatutSessionFormation.CLOTURE.value
    assert mission.statut == StatutMission.CLOTURE.value
    assert compteurs["session.date_fin_depassee"] == 1
    assert compteurs["session_cloturee"] == 1

    systeme = session.scalars(
        select(AuditEvent).where(AuditEvent.actor_type == ActorType.SYSTEME.value)
    ).all()
    assert len(systeme) == 4  # session en_cours, mission en_cours, session cloture, mission cloture
    assert all(e.event_metadata.get("automatique") is True for e in systeme)


def test_balayage_est_idempotent(session: Session) -> None:
    """Rejouer le même balayage ne réécrit rien."""
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)

    premier = CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 1))
    assert premier["session.date_debut_atteinte"] == 1

    second = CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 1))
    assert second["session.date_debut_atteinte"] == 0

    assert mission.statut == StatutMission.EN_COURS.value
    # Création, session créée, planification, puis démarrage session + mission :
    # le second balayage n'ajoute aucune trace.
    assert len(session.scalars(select(AuditEvent)).all()) == 5


def test_les_statuts_automatiques_ne_se_saisissent_pas(session: Session) -> None:
    """La route de statut refuse « en cours » et « clôturé » : jamais saisis."""
    mission = _mission_directe(session)
    service = MissionService(session)

    with pytest.raises(ValidationError, match="automatiquement"):
        service.changer_statut(mission.id, StatutMission.EN_COURS.value)
    with pytest.raises(ValidationError, match="automatiquement"):
        service.changer_statut(mission.id, StatutMission.CLOTURE.value)
    with pytest.raises(ValidationError, match="planification"):
        service.changer_statut(mission.id, StatutMission.PLANIFIE.value)


def test_mission_sans_session_utilise_ses_propres_dates(session: Session) -> None:
    """Héritage : une mission planifiée avant l'ADR 0012, sans session."""
    org = _organisation(session)
    mission = Mission(
        organisation_id=org.id,
        reference="M-HERITAGE",
        titre="Mission héritée",
        date_debut=date(2026, 9, 1),
        date_fin=date(2026, 9, 5),
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()

    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 2))
    assert mission.statut == StatutMission.EN_COURS.value

    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 7))
    assert mission.statut == StatutMission.CLOTURE.value


def test_la_machine_a_etats_refuse_les_sauts(session: Session) -> None:
    """Un statut ne change jamais sans passer par ``validate_transition``."""
    assert TRANSITIONS["mission"][StatutMission.EN_PLANIFICATION.value] == {
        StatutMission.PLANIFIE.value,
        StatutMission.ANNULE.value,
    }
    assert TRANSITIONS["mission"][StatutMission.PLANIFIE.value] == {
        StatutMission.EN_COURS.value,
        StatutMission.ANNULE.value,
    }
    assert TRANSITIONS["session"][StatutSessionFormation.PLANIFIE.value] == {
        StatutSessionFormation.EN_COURS.value,
        StatutSessionFormation.ANNULE.value,
    }
    assert TRANSITIONS["mission"][StatutMission.CLOTURE.value] == set()


# --- Annulation symétrique --------------------------------------------------


def test_annuler_la_mission_annule_sa_session(session: Session) -> None:
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)

    mission = MissionService(session).annuler(mission.id, motif="Report bailleur")

    assert mission.statut == StatutMission.ANNULE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.ANNULE.value
    annulations = session.scalars(
        select(AuditEvent).where(AuditEvent.action == ActionAudit.MISSION_STATUT_CHANGE.value)
    ).all()
    assert all(e.event_metadata.get("annulation") is True for e in annulations)


def test_annuler_la_session_annule_sa_mission(session: Session) -> None:
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)
    session_form = mission.session
    assert session_form is not None

    SessionService(session).annuler(session_form.id, motif="Imprévu")

    assert session_form.statut == StatutSessionFormation.ANNULE.value
    assert mission.statut == StatutMission.ANNULE.value


def test_annuler_depuis_la_route_de_statut_passe_par_le_service(session: Session) -> None:
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)

    mission = MissionService(session).changer_statut(
        mission.id, StatutMission.ANNULE.value
    )
    assert mission.statut == StatutMission.ANNULE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.ANNULE.value


def test_session_cloturee_ne_bloque_pas_la_mission_classee(session: Session) -> None:
    """Une mission clôturée ne peut plus être annulée (état terminal)."""
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 6))
    assert mission.statut == StatutMission.CLOTURE.value

    with pytest.raises(ConflictError):
        MissionService(session).annuler(mission.id)


# --- Confirmation d'une session manuelle ------------------------------------


def test_session_manuelle_se_confirme_et_planifie_la_mission(session: Session) -> None:
    """Chemin manuel : la session naît « en planification », la confirmation
    planifie la mission avec elle."""
    from app.application.dto import SessionPlanificationInput

    mission = _mission_directe(session)
    session_form = SessionService(session).planifier(
        SessionPlanificationInput(mission_id=mission.id, theme="Session manuelle")
    )
    assert session_form.statut == StatutSessionFormation.EN_PLANIFICATION.value
    assert mission.statut == StatutMission.EN_PLANIFICATION.value

    SessionService(session).confirmer(session_form.id)

    assert session_form.statut == StatutSessionFormation.PLANIFIE.value
    assert mission.statut == StatutMission.PLANIFIE.value


def test_session_manuelle_refusee_si_la_mission_a_deja_sa_session(session: Session) -> None:
    from app.application.dto import SessionPlanificationInput

    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)

    with pytest.raises(ConflictError, match="déjà sa session"):
        SessionService(session).planifier(
            SessionPlanificationInput(mission_id=mission.id, theme="Bis")
        )


def test_une_mission_ne_bloque_plus_son_equipe_apres_annulation(session: Session) -> None:
    """Une mission annulée ne compte plus dans la disponibilité (indirectement
    couvert par test_disponibilite_affectation ; ici on vérifie l'état)."""
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)
    MissionService(session).annuler(mission.id)
    assert mission.statut == StatutMission.ANNULE.value
    # La mission annulée n'ouvre plus le staffing (statuts vivants seulement).
    from app.application.services.affectation_service import _MISSIONS_STAFFABLES

    assert StatutMission.ANNULE not in _MISSIONS_STAFFABLES
    assert _MISSIONS_STAFFABLES == {StatutMission.EN_PLANIFICATION, StatutMission.PLANIFIE}


def test_balayage_ne_bouge_pas_une_mission_annulee(session: Session) -> None:
    """L'annulation est terminale : même le jour J, rien ne redémarre."""
    mission = _mission_directe(session)
    MissionService(session).planifier(mission.id)
    MissionService(session).annuler(mission.id)

    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 9, 3))
    assert mission.statut == StatutMission.ANNULE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.ANNULE.value
