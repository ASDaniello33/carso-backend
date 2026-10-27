"""Scénario de bout en bout des règles de gestion validées le 26/10.

Rejoue la chaîne complète côté **service** — chaque étape appelle le service
réel, jamais une écriture directe, et la base est une seule transaction :

1. un appel **sans lot** est enregistré puis validé : il reçoit son **lot
   unique « 1 »** au titre de l'appel ;
2. l'appel (à proposition) reçoit une offre **technique** et une offre
   **financière** ; une offre « autre » y est refusée ;
3. la mission est créée **avant** l'approbation des offres, puis l'offre
   technique approuvée est **rattachée** ;
4. la **planification** crée la session (dates obligatoires, avertissement
   sans équipe approuvée) ;
5. une **affectation approuvée** équipe la mission ; une seconde mission qui
   chevauche voit sa proposition **refusée** (disponibilité calculée) ;
6. le **balayage** passe la session et la mission « en cours » au jour J,
   puis « clôturées » après la fin — tracées en ``SYSTEME`` ;
7. une mission distincte est **annulée** : sa session suit, et la personne
   redevient disponible sur ces dates.
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

from app.application.dto import (
    AffectationProposee,
    AppelInput,
    MissionDepuisAppelInput,
    MissionDirecteInput,
    MissionOffresRattachementInput,
)
from app.application.services import (
    AffectationService,
    AppelService,
    MissionService,
    OffreService,
)
from app.application.services.cycle_de_vie_service import CycleDeVieService
from app.application.trace import Decision
from app.core.errors import ConflictError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import (
    ActionAudit,
    ActorType,
    RoleMission,
    SourceAffectation,
    StatutAppel,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
    TypeOffre,
)
from app.domain.execution import Equipe, Mission
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
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


def test_chaine_complete_des_regles_de_gestion(session: Session) -> None:
    org = Organisation(nom="Bailleur Sud-Ouest")
    session.add(org)
    session.flush()

    # --- 1. Appel sans lot → validation → lot unique « 1 » ------------------
    appels = AppelService(session)
    appel = appels.enregistrer(
        AppelInput(
            organisation_id=org.id,
            reference="AO-ULA-2026-01",
            titre="Appui aux incubés de la région Sud-Ouest",
        )
    )
    appels.enregistrer_extraction(
        appel.id, {"organisation": {"nom": org.nom}, "lots": []}, proposed_by_agent="agent-ao"
    )
    appels.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))
    assert appel.statut == StatutAppel.VALIDE.value

    from app.domain.organization import Lot

    lots = session.scalars(select(Lot)).all()
    assert len(lots) == 1
    lot = lots[0]
    assert lot.numero == "1"
    assert lot.titre == appel.titre

    # --- 2. Offres : type contrôlé par la nature de l'appel ------------------
    offres = OffreService(session)
    with pytest.raises(ValidationError, match="nature de l'appel"):
        offres.creer_brouillon(lot.id, "OFFRE-AUTRE", "Autre", type=TypeOffre.AUTRE.value)

    technique = offres.creer_brouillon(
        lot.id, "OFFRE-TECH", "Offre technique", type=TypeOffre.OFFRE_TECHNIQUE.value
    )
    financiere = offres.creer_brouillon(
        lot.id, "OFFRE-FIN", "Offre financière", type=TypeOffre.OFFRE_FINANCIERE.value
    )
    assert technique.statut == StatutOffre.BROUILLON.value
    assert financiere.statut == StatutOffre.BROUILLON.value

    # --- 3. Mission avant approbation, puis rattachement ---------------------
    missions = MissionService(session)
    mission = missions.creer_depuis_appel(
        MissionDepuisAppelInput(
            appel_id=appel.id,
            lot_id=lot.id,
            reference="M-ULA-2026-01",
            titre="Mission incubation",
            date_debut=date(2026, 11, 2),
            date_fin=date(2026, 11, 6),
        )
    )
    assert mission.statut == StatutMission.EN_PLANIFICATION.value
    assert mission.offres == []

    # Le lot a déjà sa mission : une seconde est refusée.
    with pytest.raises(ConflictError, match="déjà sa mission"):
        missions.creer_depuis_appel(
            MissionDepuisAppelInput(
                appel_id=appel.id,
                lot_id=lot.id,
                reference="M-ULA-2026-01-BIS",
                titre="Doublon",
            )
        )

    # Le rattachement exige une offre approuvée du même lot.
    with pytest.raises(ConflictError, match="approuvées"):
        missions.rattacher_offres(
            mission.id, MissionOffresRattachementInput(offre_ids=(technique.id,))
        )

    offres.soumettre_revue(technique.id)
    offres.publier(technique.id, Decision(decided_by="chef.carso"))
    mission = missions.rattacher_offres(
        mission.id, MissionOffresRattachementInput(offre_ids=(technique.id,))
    )
    assert [o.id for o in mission.offres] == [technique.id]

    # --- 4. Planification : dates obligatoires, session créée ----------------
    avertissement = missions.avertissement_equipe(mission.id)
    assert avertissement is not None  # aucune équipe approuvée : information

    mission = missions.planifier(mission.id)
    assert mission.statut == StatutMission.PLANIFIE.value
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.PLANIFIE.value
    assert mission.session.date_debut == date(2026, 11, 2)

    # --- 5. Disponibilité : affectation puis chevauchement refusé ------------
    affectations = AffectationService(session)
    rasoa = Equipe(nom="Rasoa", prenom="Mino", fonction="Chargée de programme")
    session.add(rasoa)
    session.flush()

    # Une seconde mission, planifiée, porte l'affectation (10-12 novembre).
    seconde = Mission(
        organisation_id=org.id,
        reference="M-ULA-2026-02",
        titre="Mission novembre",
        date_debut=date(2026, 11, 10),
        date_fin=date(2026, 11, 12),
        statut=StatutMission.EN_PLANIFICATION.value,
    )
    session.add(seconde)
    session.flush()
    missions.planifier(seconde.id)
    assert seconde.session is not None

    affectation = affectations.proposer(
        AffectationProposee(
            mission_id=seconde.id,
            equipe_id=rasoa.id,
            role_dans_mission=RoleMission.FORMATEUR.value,
            source=SourceAffectation.MANUAL.value,
            proposed_by="test",
        )
    )
    affectations.approuver(affectation.id, Decision(decided_by="chef"))

    # Une mission voisine (11-13 novembre) chevauche le 11 et le 12 : refusée.
    voisine = Mission(
        organisation_id=org.id,
        reference="M-ULA-2026-03",
        titre="Mission voisine",
        date_debut=date(2026, 11, 11),
        date_fin=date(2026, 11, 13),
        statut=StatutMission.EN_PLANIFICATION.value,
    )
    session.add(voisine)
    session.flush()
    with pytest.raises(ConflictError, match="pas disponible"):
        affectations.proposer(
            AffectationProposee(
                mission_id=voisine.id,
                equipe_id=rasoa.id,
                role_dans_mission=RoleMission.FORMATEUR.value,
                source=SourceAffectation.MANUAL.value,
                proposed_by="test",
            )
        )

    # --- 6. Balayage : en cours au jour J, clôturé après la fin --------------
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 11, 3))
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.EN_COURS.value
    assert mission.statut == StatutMission.EN_COURS.value

    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 11, 8))
    assert mission.session.statut == StatutSessionFormation.CLOTURE.value
    assert mission.statut == StatutMission.CLOTURE.value

    systeme = session.scalars(
        select(AuditEvent).where(AuditEvent.actor_type == ActorType.SYSTEME.value)
    ).all()
    assert {e.action for e in systeme} >= {
        ActionAudit.SESSION_STATUT_CHANGE.value,
        ActionAudit.MISSION_STATUT_CHANGE.value,
    }

    # --- 7. Annulation d'une mission vivante : session suit, équipe libérée --
    missions.annuler(seconde.id, motif="Report bailleur")
    assert seconde.statut == StatutMission.ANNULE.value
    assert seconde.session is not None
    assert seconde.session.statut == StatutSessionFormation.ANNULE.value

    # Rasoa est de nouveau disponible : la mission annulée ne bloque plus.
    affectations.proposer(
        AffectationProposee(
            mission_id=voisine.id,
            equipe_id=rasoa.id,
            role_dans_mission=RoleMission.FORMATEUR.value,
            source=SourceAffectation.MANUAL.value,
            proposed_by="test",
        )
    )


def test_prestation_directe_reste_possible(session: Session) -> None:
    """Chemin conservé : une mission sans appel ni lot, identifiée comme telle."""
    org = Organisation(nom="Client direct")
    session.add(org)
    session.flush()
    mission = MissionService(session).creer_directe(
        MissionDirecteInput(
            organisation_id=org.id,
            reference="M-DIRECTE-01",
            titre="Prestation directe",
            date_debut=date(2026, 10, 1),
            date_fin=date(2026, 10, 3),
        )
    )
    assert mission.lot_id is None
    assert mission.statut == StatutMission.EN_PLANIFICATION.value
    missions = MissionService(session)
    missions.planifier(mission.id)
    assert mission.session is not None


def test_dates_de_mission_ajoutables_avant_planification(session: Session) -> None:
    """Une mission née sans dates doit pouvoir les recevoir (ADR 0012).

    La planification exige des dates ; sans porte de sortie, une mission créée
    sans elles resterait bloquée à vie en « en planification ». Elles se
    modifient donc tant que la mission n'est pas planifiée — ensuite la
    session porte le calendrier et le refus est explicite, jamais silencieux.
    """
    org = Organisation(nom="Bailleur Ahanta")
    session.add(org)
    session.flush()
    missions = MissionService(session)
    mission = missions.creer_directe(
        MissionDirecteInput(
            organisation_id=org.id,
            reference="M-SANS-DATES",
            titre="Mission à dater",
        )
    )
    assert mission.date_debut is None
    assert mission.date_fin is None

    # Tant que la mission n'est pas planifiée, les dates se posent (et se
    # reprennent : une faute de frappe ne fige rien).
    mission = missions.modifier(
        mission.id,
        date_debut=date(2026, 12, 1),
        date_fin=date(2026, 12, 5),
        acteur="HUMAIN",
    )
    assert mission.date_debut == date(2026, 12, 1)
    assert mission.date_fin == date(2026, 12, 5)

    mission = missions.modifier(mission.id, date_fin=date(2026, 12, 3), acteur="HUMAIN")
    assert mission.date_fin == date(2026, 12, 3)

    # Début après fin : refusé, rien n'est écrit.
    with pytest.raises(ValidationError, match="date de début.*date de fin"):
        missions.modifier(mission.id, date_debut=date(2026, 12, 4), acteur="HUMAIN")
    assert mission.date_debut == date(2026, 12, 1)

    # Planifiée : la session reprend les dates de la mission.
    missions.planifier(mission.id)
    assert mission.session is not None
    assert mission.session.date_debut == date(2026, 12, 1)
    assert mission.session.date_fin == date(2026, 12, 3)

    # Passé ce stade, le calendrier appartient à la session : refus explicite.
    with pytest.raises(ValidationError, match="avant sa planification"):
        missions.modifier(mission.id, date_debut=date(2026, 12, 10), acteur="HUMAIN")
    assert mission.date_debut == date(2026, 12, 1)
