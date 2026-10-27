"""Lot L0 — services Mission/Session : règles 5 et 6, machine à états, traçabilité.

Tests purs sur SQLite mémoire, mêmes conventions que ``test_services.py`` :
hooks de compilation pour les types PG, aucune assertion sur le commit (la
transaction appartient à l'appelant).
"""

import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import (
    MissionDepuisAppelInput,
    MissionDepuisOffresInput,
    MissionDirecteInput,
    MissionOffresRattachementInput,
    SessionPlanificationInput,
)
from app.application.services import MissionService, SessionService
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import (
    ActionAudit,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
    TypeOffre,
)
from app.domain.execution import Mission
from app.domain.organization import Appel, Lot, Offre, Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_server_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
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


# --- Fixtures métier ----------------------------------------------------------


def _make_offre(
    session: Session, *, statut: str | None = None, reference: str = "OF-2026-9"
) -> Offre:
    """Offre rattachée à un appel/lot existants ; approuvée sauf indication."""
    org = Organisation(nom="Client Offre")
    session.add(org)
    session.flush()
    appel = Appel(
        organisation_id=org.id, reference=f"AO-{reference}", titre="Appel test"
    )
    session.add(appel)
    session.flush()
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot test")
    session.add(lot)
    session.flush()
    offre = Offre(
        organisation_id=org.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type=TypeOffre.OFFRE_TECHNIQUE.value,
        reference=reference,
        titre="Offre test",
        statut=statut or StatutOffre.APPROUVE.value,
    )
    session.add(offre)
    session.flush()
    return offre


def _make_mission(session: Session) -> Mission:
    offre = _make_offre(session)
    mission = Mission(
        organisation_id=offre.organisation_id,
        reference="M-2026-9",
        titre="Mission test",
        statut=StatutMission.PLANIFIE.value,
    )
    mission.offres = [offre]
    session.add(mission)
    session.flush()
    return mission


class TestMissionService:
    def test_creation_depuis_appel_reference_les_offres_approuvees_du_lot(
        self, session: Session
    ) -> None:
        """Chemin UI unique : tout se déduit de l'appel et du lot."""
        offre = _make_offre(session, reference="OF-2026-A")
        offre_fin = Offre(
            organisation_id=offre.organisation_id,
            appel_id=offre.appel_id,
            lot_id=offre.lot_id,
            type=TypeOffre.OFFRE_FINANCIERE.value,
            reference="OF-2026-A-FIN",
            titre="Offre financière",
            statut=StatutOffre.APPROUVE.value,
        )
        session.add(offre_fin)
        session.flush()
        service = MissionService(session)

        mission = service.creer_depuis_appel(
            MissionDepuisAppelInput(
                appel_id=offre.appel_id,
                lot_id=offre.lot_id,
                reference="M-2026-10",
                titre="Formation trésorerie",
            )
        )

        assert mission.statut == StatutMission.EN_PLANIFICATION.value
        # L'organisation est déduite de l'appel, jamais fournie.
        assert mission.organisation_id == offre.organisation_id
        assert mission.lot_id == offre.lot_id
        assert sorted(o.reference for o in mission.offres) == ["OF-2026-A", "OF-2026-A-FIN"]
        evenement = session.scalars(select(AuditEvent)).one()
        assert evenement.action == ActionAudit.MISSION_CREEE.value
        assert evenement.after["appel_id"] == str(offre.appel_id)
        assert evenement.after["lot_id"] == str(offre.lot_id)

    def test_creation_depuis_appel_lot_hors_appel_refuse(self, session: Session) -> None:
        """Un lot d'un autre appel ne peut pas porter la mission."""
        offre = _make_offre(session, reference="OF-2026-B")
        autre_appel = Appel(
            organisation_id=offre.organisation_id, reference="AO-AUTRE", titre="Autre"
        )
        session.add(autre_appel)
        session.flush()
        autre_lot = Lot(appel_id=autre_appel.id, numero="1", titre="L2")
        session.add(autre_lot)
        session.flush()
        service = MissionService(session)

        with pytest.raises(ValidationError, match="n'appartient pas à l'appel"):
            service.creer_depuis_appel(
                MissionDepuisAppelInput(
                    appel_id=autre_appel.id,
                    lot_id=offre.lot_id,
                    reference="M-2026-11",
                    titre="Mission",
                )
            )

    def test_creation_depuis_appel_sans_offre_approuvee_puis_rattachement(
        self, session: Session
    ) -> None:
        """ADR 0012 : la mission naît **avant** l'approbation des offres.

        Elle ne référence alors aucune offre ; l'offre approuvée plus tard se
        rattache par ``rattacher_offres`` — et une offre non approuvée y est
        refusée.
        """
        offre = _make_offre(session, statut=StatutOffre.EN_REVUE.value, reference="OF-2026-C")
        service = MissionService(session)

        mission = service.creer_depuis_appel(
            MissionDepuisAppelInput(
                appel_id=offre.appel_id,
                lot_id=offre.lot_id,
                reference="M-2026-12",
                titre="Mission",
            )
        )
        assert mission.statut == StatutMission.EN_PLANIFICATION.value
        assert mission.offres == []

        # L'offre n'est pas encore approuvée : le rattachement est refusé.
        with pytest.raises(ConflictError, match="approuvées"):
            service.rattacher_offres(
                mission.id, MissionOffresRattachementInput(offre_ids=(offre.id,))
            )

        offre.statut = StatutOffre.APPROUVE.value
        session.flush()
        mission = service.rattacher_offres(
            mission.id, MissionOffresRattachementInput(offre_ids=(offre.id,))
        )
        assert [o.id for o in mission.offres] == [offre.id]

    def test_rattachement_refuse_une_offre_d_un_autre_lot(self, session: Session) -> None:
        """Une mission ne référence que les offres de **son** lot."""
        offre = _make_offre(session)
        autre = _make_offre(session, reference="OF-2026-AUTRE")
        service = MissionService(session)
        mission = service.creer_depuis_appel(
            MissionDepuisAppelInput(
                appel_id=offre.appel_id,
                lot_id=offre.lot_id,
                reference="M-2026-LOT",
                titre="Mission",
            )
        )
        with pytest.raises(ValidationError, match="propre lot"):
            service.rattacher_offres(
                mission.id, MissionOffresRattachementInput(offre_ids=(autre.id,))
            )

    def test_un_lot_n_accepte_qu_une_mission(self, session: Session) -> None:
        """1 lot → 1 mission (ADR 0012) : un second refus explicite."""
        offre = _make_offre(session)
        service = MissionService(session)
        service.creer_depuis_appel(
            MissionDepuisAppelInput(
                appel_id=offre.appel_id,
                lot_id=offre.lot_id,
                reference="M-LOT-A",
                titre="Première",
            )
        )
        with pytest.raises(ConflictError, match="d\u00e9j\u00e0 sa mission"):
            service.creer_depuis_appel(
                MissionDepuisAppelInput(
                    appel_id=offre.appel_id,
                    lot_id=offre.lot_id,
                    reference="M-LOT-B",
                    titre="Seconde",
                )
            )

    def test_creation_depuis_appel_inconnu(self, session: Session) -> None:
        service = MissionService(session)
        with pytest.raises(NotFoundError):
            service.creer_depuis_appel(
                MissionDepuisAppelInput(
                    appel_id=uuid4(),
                    lot_id=uuid4(),
                    reference="M-2026-13",
                    titre="Mission",
                )
            )

    def test_creation_depuis_offre_approuvee(self, session: Session) -> None:
        offre = _make_offre(session)
        service = MissionService(session)

        mission = service.creer_depuis_offres(
            MissionDepuisOffresInput(
                offre_ids=(offre.id,),
                reference="M-2026-9",
                titre="Formation trésorerie",
            )
        )

        assert mission.statut == StatutMission.EN_PLANIFICATION.value
        assert [o.id for o in mission.offres] == [offre.id]
        assert mission.lot_id == offre.lot_id
        assert mission.organisation_id == offre.organisation_id
        evenement = session.scalars(select(AuditEvent)).one()
        assert evenement.action == ActionAudit.MISSION_CREEE.value

    def test_creation_depuis_offre_non_approuvee_refusee(self, session: Session) -> None:
        offre = _make_offre(session, statut=StatutOffre.EN_REVUE.value)
        service = MissionService(session)

        with pytest.raises(ConflictError, match="approuvée"):
            service.creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(offre.id,),
                    reference="M-2026-9",
                    titre="Formation",
                )
            )

    def test_offre_inconnue(self, session: Session) -> None:
        service = MissionService(session)
        with pytest.raises(NotFoundError):
            service.creer_depuis_offres(
                MissionDepuisOffresInput(offre_ids=(uuid4(),), reference="M-X", titre="x")
            )

    def test_reference_dupliquee_refusee(self, session: Session) -> None:
        offre_a = _make_offre(session)
        offre_b = _make_offre(session, reference="OF-2026-10")
        service = MissionService(session)
        service.creer_depuis_offres(
            MissionDepuisOffresInput(
                offre_ids=(offre_a.id,), reference="M-DUP", titre="A"
            )
        )

        with pytest.raises(ConflictError, match="Référence"):
            service.creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(offre_b.id,), reference="M-DUP", titre="B"
                )
            )

    def test_une_offre_n_accepte_qu_une_mission(self, session: Session) -> None:
        """Une offre appartient à une seule mission (409 sinon)."""
        offre = _make_offre(session)
        service = MissionService(session)
        service.creer_depuis_offres(
            MissionDepuisOffresInput(
                offre_ids=(offre.id,), reference="M-UNIQ", titre="Première"
            )
        )
        with pytest.raises(ConflictError, match="déjà une de ces offres"):
            service.creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(offre.id,), reference="M-UNIQ-2", titre="Seconde"
                )
            )

    def test_creation_directe_verifie_organisation(self, session: Session) -> None:
        service = MissionService(session)
        with pytest.raises(NotFoundError, match="Organisation"):
            service.creer_directe(
                MissionDirecteInput(organisation_id=uuid4(), reference="M-1", titre="x")
            )

    def test_annulation_et_refus_des_statuts_automatiques(self, session: Session) -> None:
        """ADR 0012 : seule l'annulation est manuelle ; ``en_cours`` et
        ``cloture`` sont appliqués par le balayage, jamais saisis."""
        mission = _make_mission(session)
        service = MissionService(session)

        with pytest.raises(ValidationError, match="automatiquement"):
            service.changer_statut(mission.id, StatutMission.EN_COURS.value)
        with pytest.raises(ValidationError, match="automatiquement"):
            service.changer_statut(mission.id, StatutMission.CLOTURE.value)
        with pytest.raises(ValidationError, match="planification"):
            service.changer_statut(mission.id, StatutMission.PLANIFIE.value)

        mission = service.changer_statut(mission.id, StatutMission.ANNULE.value)
        assert mission.statut == StatutMission.ANNULE.value

        # État terminal : plus rien ne bouge.
        with pytest.raises(ConflictError):
            service.changer_statut(mission.id, StatutMission.ANNULE.value)

    def test_statut_inconnu_refuse(self, session: Session) -> None:
        mission = _make_mission(session)
        service = MissionService(session)

        with pytest.raises(ValidationError, match="inconnu"):
            service.changer_statut(mission.id, "statut_invente")




class TestSessionService:
    def test_creation_manuelle_puis_confirmation(self, session: Session) -> None:
        """Chemin manuel (ADR 0012) : la session naît « en planification », la
        confirmation la passe « planifiée » — et planifie la mission avec elle."""
        mission = _make_mission(session)
        service = SessionService(session)

        session_formation = service.planifier(
            SessionPlanificationInput(mission_id=mission.id, theme="J1")
        )

        assert session_formation.statut == StatutSessionFormation.EN_PLANIFICATION.value
        evenement = session.scalars(select(AuditEvent)).one()
        assert evenement.action == ActionAudit.SESSION_PLANIFIEE.value

        session_formation = service.confirmer(session_formation.id)
        assert session_formation.statut == StatutSessionFormation.PLANIFIE.value
        assert mission.statut == StatutMission.PLANIFIE.value

    def test_planification_mission_inconnue(self, session: Session) -> None:
        service = SessionService(session)
        with pytest.raises(NotFoundError, match="Mission"):
            service.planifier(SessionPlanificationInput(mission_id=uuid4()))

    def test_transitions_session(self, session: Session) -> None:
        """Transitions manuelles : confirmation et annulation seulement ;
        « en cours » et « clôturé » sont automatiques (ADR 0012)."""
        mission = _make_mission(session)
        service = SessionService(session)
        session_formation = service.planifier(SessionPlanificationInput(mission_id=mission.id))

        session_formation = service.changer_statut(
            session_formation.id, StatutSessionFormation.PLANIFIE.value
        )
        assert session_formation.statut == StatutSessionFormation.PLANIFIE.value

        # Les statuts automatiques ne se saisissent pas.
        with pytest.raises(ValidationError, match="automatiquement"):
            service.changer_statut(
                session_formation.id, StatutSessionFormation.EN_COURS.value
            )
        with pytest.raises(ValidationError, match="automatiquement"):
            service.changer_statut(
                session_formation.id, StatutSessionFormation.CLOTURE.value
            )
        # Re-confirmer une session déjà planifiée : interdit par la machine.
        with pytest.raises(ConflictError):
            service.changer_statut(
                session_formation.id, StatutSessionFormation.PLANIFIE.value
            )

    def test_statut_inconnu_refuse(self, session: Session) -> None:
        mission = _make_mission(session)
        service = SessionService(session)
        session_formation = service.planifier(SessionPlanificationInput(mission_id=mission.id))

        with pytest.raises(ValidationError, match="inconnu"):
            service.changer_statut(session_formation.id, "statut_invente")

    def test_sessions_dune_mission(self, session: Session) -> None:
        """1 mission → 1 session (ADR 0012) : une seconde création est refusée."""
        mission = _make_mission(session)
        service = SessionService(session)
        service.planifier(SessionPlanificationInput(mission_id=mission.id, theme="J1"))

        with pytest.raises(ConflictError, match="d\u00e9j\u00e0 sa session"):
            service.planifier(SessionPlanificationInput(mission_id=mission.id, theme="J2"))

        sessions = service.lister_pour_mission(mission.id)
        assert len(sessions) == 1

    def test_liste_mission_inconnue(self, session: Session) -> None:
        service = SessionService(session)
        with pytest.raises(NotFoundError, match="Mission"):
            service.lister_pour_mission(uuid4())
