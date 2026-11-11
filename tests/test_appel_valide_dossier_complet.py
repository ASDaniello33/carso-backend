"""Règle du dossier complet — « un appel est validé s'il a au moins une offre et une mission ».

Règle confirmée le 10/11/2026. Elle comble un trou réel constaté en base : un
appel **saisi à la main** n'a aucune proposition d'extraction, donc aucune
décision humaine à prendre ; il restait ``recu`` à vie, même avec des offres et
une mission.

Deux chemins sont couverts séparément, parce qu'ils peuvent se casser
indépendamment :

- la **règle** (``AppelService.marquer_valide_si_complet``), testée sur un
  dossier construit directement en base ;
- les **accroches** (``OffreService`` et ``MissionService``), testées en créant
  l'offre ou la mission par le service : c'est là que l'appel doit basculer
  sans qu'aucun écran n'ait à le demander.
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

from app.application.dto import MissionDepuisAppelInput, MissionDirecteInput
from app.application.services import AppelService, MissionService, OffreService
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, StatutAppel, StatutOffre, TypeOffre
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


# --- Dossier construit directement (sans passer par les services) -------------


def _appel_recu(session: Session, reference: str = "DEMO-2026") -> Appel:
    """Appel **saisi à la main** : statut ``recu``, aucune proposition."""
    org = Organisation(nom="Organisation émettrice")
    session.add(org)
    session.flush()
    appel = Appel(organisation_id=org.id, reference=reference, titre="Formation DEVWEB")
    session.add(appel)
    session.flush()
    assert appel.donnees_extraites is None
    return appel


def _lot(session: Session, appel: Appel) -> Lot:
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot unique")
    session.add(lot)
    session.flush()
    return lot


def _offre_brute(session: Session, lot: Lot, reference: str = "OFFRE-TECH-L1") -> Offre:
    """Offre posée par l'ORM : aucune accroche de service ne s'exécute."""
    offre = Offre(
        organisation_id=lot.appel.organisation_id,
        appel_id=lot.appel_id,
        lot_id=lot.id,
        type=TypeOffre.OFFRE_TECHNIQUE.value,
        reference=reference,
        titre="Offre technique",
        statut=StatutOffre.BROUILLON.value,
    )
    session.add(offre)
    session.flush()
    return offre


def _mission_brute(session: Session, lot: Lot, reference: str = "MISSION-DEMO") -> Mission:
    """Mission posée par l'ORM : l'accroche de ``MissionService`` ne s'exécute pas."""
    mission = Mission(
        organisation_id=lot.appel.organisation_id,
        lot_id=lot.id,
        reference=reference,
        titre="Mission de démonstration",
    )
    session.add(mission)
    session.flush()
    return mission


def _evenements(session: Session) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.APPEL_A_PROPOSITION_VALIDE.value
            )
        ).all()
    )


# --- La règle elle-même ------------------------------------------------------


class TestRegleDossierComplet:
    def test_offre_seule_ne_valide_pas(self, session: Session) -> None:
        """Une offre sans mission ne suffit pas : la règle exige les deux."""
        appel = _appel_recu(session)
        _offre_brute(session, _lot(session, appel))

        assert AppelService(session).marquer_valide_si_complet(appel.id) is False
        assert appel.statut == StatutAppel.RECU.value

    def test_mission_seule_ne_valide_pas(self, session: Session) -> None:
        """Une mission sans offre ne suffit pas — même règle, autre moitié."""
        appel = _appel_recu(session)
        _mission_brute(session, _lot(session, appel))

        assert AppelService(session).marquer_valide_si_complet(appel.id) is False
        assert appel.statut == StatutAppel.RECU.value

    def test_offre_et_mission_valident_l_appel(self, session: Session) -> None:
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _offre_brute(session, lot)
        _mission_brute(session, lot)

        assert AppelService(session).marquer_valide_si_complet(appel.id) is True
        assert appel.statut == StatutAppel.VALIDE.value

    def test_plusieurs_lots_ne_changent_pas_la_regle(self, session: Session) -> None:
        """Une offre sur un lot et une mission sur un autre suffisent déjà."""
        appel = _appel_recu(session)
        lot_1 = _lot(session, appel)
        lot_2 = Lot(appel_id=appel.id, numero="2", titre="Lot 2")
        session.add(lot_2)
        session.flush()
        _offre_brute(session, lot_1)
        _mission_brute(session, lot_2)

        assert AppelService(session).marquer_valide_si_complet(appel.id) is True
        assert appel.statut == StatutAppel.VALIDE.value

    def test_validation_tracee_en_systeme(self, session: Session) -> None:
        """Le passage est tracé avec la règle : il n'a pas de décision humaine."""
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _offre_brute(session, lot)
        _mission_brute(session, lot)
        AppelService(session).marquer_valide_si_complet(appel.id)

        evenement = _evenements(session)[0]
        assert evenement.actor_type == "systeme"
        assert evenement.event_metadata["automatique"] is True
        assert evenement.event_metadata["regle"] == "au_moins_une_offre_et_une_mission"

    def test_idempotent(self, session: Session) -> None:
        """Rejouer la règle sur un appel déjà validé n'écrit rien de plus."""
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _offre_brute(session, lot)
        _mission_brute(session, lot)
        service = AppelService(session)
        assert service.marquer_valide_si_complet(appel.id) is True

        assert service.marquer_valide_si_complet(appel.id) is False
        assert len(_evenements(session)) == 1

    def test_proposition_en_attente_non_forcee(self, session: Session) -> None:
        """Un appel ``propose`` attend une décision humaine : on ne l'avance pas.

        Le dossier peut être complet alors qu'une proposition attend d'être lue ;
        valider ici approuverait en silence une extraction que personne n'a vue.
        """
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _offre_brute(session, lot)
        _mission_brute(session, lot)
        appel.statut = StatutAppel.PROPOSE
        session.flush()

        assert AppelService(session).marquer_valide_si_complet(appel.id) is False
        assert appel.statut == StatutAppel.PROPOSE.value

    def test_appel_rejete_reste_rejete(self, session: Session) -> None:
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _offre_brute(session, lot)
        _mission_brute(session, lot)
        appel.statut = StatutAppel.REJETE
        session.flush()

        assert AppelService(session).marquer_valide_si_complet(appel.id) is False
        assert appel.statut == StatutAppel.REJETE.value

    def test_appel_inconnu(self, session: Session) -> None:
        assert AppelService(session).marquer_valide_si_complet(uuid4()) is False

    def test_transition_recu_vers_valide_autorisee(self) -> None:
        """L'arête existe dans la machine à états — sinon la règle serait morte."""
        from app.domain.state_machines import validate_transition

        validate_transition("appel", StatutAppel.RECU.value, StatutAppel.VALIDE.value)


# --- Les accroches : le dossier se complète par les services ------------------


class TestAccroches:
    def test_creer_une_mission_valide_l_appel(self, session: Session) -> None:
        """Offre déjà là : la mission est la pièce manquante, l'appel bascule."""
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _offre_brute(session, lot)

        MissionService(session).creer_depuis_appel(
            MissionDepuisAppelInput(
                appel_id=appel.id,
                lot_id=lot.id,
                reference="MISSION-DEMO",
                titre="Mission de démonstration",
            )
        )

        assert appel.statut == StatutAppel.VALIDE.value

    def test_creer_une_offre_valide_l_appel(self, session: Session) -> None:
        """Mission déjà là : l'offre est la pièce manquante, l'appel bascule."""
        appel = _appel_recu(session)
        lot = _lot(session, appel)
        _mission_brute(session, lot)

        OffreService(session).creer_brouillon(
            lot.id, None, "Offre technique", type=TypeOffre.OFFRE_TECHNIQUE.value
        )

        assert appel.statut == StatutAppel.VALIDE.value

    def test_prestation_directe_sans_appel(self, session: Session) -> None:
        """Une mission sans lot n'a pas d'appel : rien à évaluer, aucune erreur."""
        org = Organisation(nom="Client direct")
        session.add(org)
        session.flush()
        MissionService(session).creer_directe(
            MissionDirecteInput(
                organisation_id=org.id,
                reference="MISSION-DIRECTE",
                titre="Prestation directe",
            )
        )
        assert _evenements(session) == []
