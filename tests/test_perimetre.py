"""Périmètre d'accès — unités (rôles, missions approuvées, dossier lié).

Ce que ces tests verrouillent :

- l'**administration** et l'**équipe interne** gardent l'accès complet au métier ;
- un **formateur** ne voit que ses missions **approuvées** (une proposition en
  attente ou refusée n'ouvre aucun droit) et les collègues de ces missions ;
- un compte restreint sans personne du vivier liée a un périmètre **vide** ;
- ``exiger_complet`` refuse les gestes de pilotage hors périmètre ;
- le **dossier lié** (organisation, appel, lots, offres) se déduit des missions.

Tests purs sur SQLite mémoire (mêmes hooks de compilation que la suite).
"""

import re
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dossier import DOSSIER_VIDE, dossier_autorise
from app.application.perimetre import (
    PERIMETRE_COMPLET,
    PERIMETRE_VIDE,
    Perimetre,
    perimetre_effectif,
    poser_perimetre,
    pour_utilisateur,
    retablir_perimetre,
)
from app.core.errors import PermissionDeniedError
from app.domain.base import Base
from app.domain.enums import (
    RoleUtilisateur,
    StatutAffectation,
    StatutMission,
    StatutUtilisateur,
    TypeOffre,
)
from app.domain.execution import AffectationEquipe, Equipe, Mission, MissionOffre
from app.domain.identity import Utilisateur
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
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


# --- Fabriques minimales -------------------------------------------------------


def _mission(session: Session, reference: str) -> Mission:
    organisation = Organisation(nom=f"Client {reference}")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference=reference,
        titre=f"Mission {reference}",
        statut=StatutMission.EN_COURS.value,
    )
    session.add(mission)
    session.flush()
    return mission


def _equipe(session: Session, nom: str) -> Equipe:
    membre = Equipe(nom=nom, prenom="Test", email=f"{nom.lower()}@example.com")
    session.add(membre)
    session.flush()
    return membre


def _utilisateur(
    session: Session, *, role: str, equipe_id: UUID | None = None
) -> Utilisateur:
    compte = Utilisateur(
        email=f"compte-{uuid4().hex[:8]}@carso.test",
        nom="Compte",
        prenom="Test",
        password_hash="x",
        role=role,
        statut=StatutUtilisateur.ACTIF.value,
        equipe_id=equipe_id,
    )
    session.add(compte)
    session.flush()
    return compte


def _affecter(
    session: Session, mission: Mission, equipe: Equipe, statut: str
) -> AffectationEquipe:
    affectation = AffectationEquipe(
        mission_id=mission.id,
        equipe_id=equipe.id,
        role_dans_mission="formateur",
        statut=statut,
    )
    session.add(affectation)
    session.flush()
    return affectation


# --- Rôles complets ------------------------------------------------------------


def test_administration_et_equipe_interne_ont_acces_complet(session: Session) -> None:
    """L'accès complet n'est **pas** modal : seuls les rôles restreints sont calculés."""
    for role in (RoleUtilisateur.ADMINISTRATEUR.value, RoleUtilisateur.COLLABORATEUR.value):
        compte = _utilisateur(session, role=role)
        perimetre = pour_utilisateur(session, compte)
        assert perimetre.est_complet
        assert not perimetre.est_restreint
        assert perimetre.utilisateur_id == compte.id
        # Une mission inconnue reste accessible : le filtrage n'existe pas.
        assert perimetre.mission_autorisee(uuid4())


def test_formateur_sans_personne_liee_a_un_perimetre_vide(session: Session) -> None:
    """Aucun droit par défaut : on ne présume jamais d'une affectation."""
    compte = _utilisateur(session, role=RoleUtilisateur.FORMATEUR.value)
    perimetre = pour_utilisateur(session, compte)
    assert perimetre.est_restreint
    assert perimetre.missions_autorisees == frozenset()
    assert perimetre.equipes_autorisees == frozenset()
    assert not perimetre.mission_autorisee(uuid4())
    assert not perimetre.equipe_autorisee(uuid4())


def test_formateur_voit_seulement_ses_missions_approuvees(session: Session) -> None:
    """Proposition en attente et refus n'ouvrent aucun droit (règle 6 [C])."""
    membre = _equipe(session, "Alice")
    compte = _utilisateur(
        session, role=RoleUtilisateur.FORMATEUR.value, equipe_id=membre.id
    )
    approuvee = _mission(session, "M-APPROUVEE")
    en_attente = _mission(session, "M-ATTENTE")
    refusee = _mission(session, "M-REFUSEE")
    _affecter(session, approuvee, membre, StatutAffectation.APPROUVEE.value)
    _affecter(session, en_attente, membre, StatutAffectation.PROPOSEE.value)
    _affecter(session, refusee, membre, StatutAffectation.REFUSEE.value)

    perimetre = pour_utilisateur(session, compte)

    assert perimetre.missions_autorisees == frozenset({approuvee.id})
    assert perimetre.mission_autorisee(approuvee.id)
    assert not perimetre.mission_autorisee(en_attente.id)
    assert not perimetre.mission_autorisee(refusee.id)


def test_formateur_voit_les_collegues_de_ses_missions_seulement(session: Session) -> None:
    """Le vivier visible = sa fiche + les personnes approuvées sur ses missions."""
    membre = _equipe(session, "Alice")
    collegue = _equipe(session, "Bruno")
    inconnu = _equipe(session, "Chloé")
    compte = _utilisateur(
        session, role=RoleUtilisateur.FORMATEUR.value, equipe_id=membre.id
    )
    sienne = _mission(session, "M-ALICE")
    autre = _mission(session, "M-AUTRE")
    _affecter(session, sienne, membre, StatutAffectation.APPROUVEE.value)
    _affecter(session, sienne, collegue, StatutAffectation.APPROUVEE.value)
    _affecter(session, autre, inconnu, StatutAffectation.APPROUVEE.value)
    # Un collègue **proposé** (non approuvé) sur sa mission ne devient pas visible.
    propose = _equipe(session, "Diane")
    _affecter(session, sienne, propose, StatutAffectation.PROPOSEE.value)

    perimetre = pour_utilisateur(session, compte)

    assert perimetre.equipe_autorisee(membre.id)
    assert perimetre.equipe_autorisee(collegue.id)
    assert not perimetre.equipe_autorisee(inconnu.id)
    assert not perimetre.equipe_autorisee(propose.id)
    assert not perimetre.equipe_autorisee(None)


# --- Contexte (ContextVar) et refus -------------------------------------------


def test_perimetre_effectif_par_defaut_et_restauration() -> None:
    """Sans périmètre posé : accès complet (appel interne) ; posé : il prime."""
    assert perimetre_effectif() is PERIMETRE_COMPLET
    jeton = poser_perimetre(PERIMETRE_VIDE)
    try:
        assert perimetre_effectif() is PERIMETRE_VIDE
        assert perimetre_effectif().est_restreint
    finally:
        retablir_perimetre(jeton)
    assert perimetre_effectif() is PERIMETRE_COMPLET


def test_exiger_complet_refuse_un_compte_restreint() -> None:
    """Défense en profondeur : le refus porte le rôle, jamais un secret."""
    with pytest.raises(PermissionDeniedError) as erreur:
        PERIMETRE_VIDE.exiger_complet("créer une mission")
    assert erreur.value.details == {"role": None}
    PERIMETRE_COMPLET.exiger_complet("créer une mission")  # ne lève pas
    complet = Perimetre(role=RoleUtilisateur.COLLABORATEUR.value, est_complet=True)
    complet.exiger_complet("créer une mission")  # ne lève pas


def test_mission_et_equipe_non_renseignees_ne_sont_jamais_autorisees() -> None:
    """``None`` n'est jamais un laissez-passer implicite."""
    perimetre = Perimetre(est_complet=False)
    assert not perimetre.mission_autorisee(None)
    assert not perimetre.equipe_autorisee(None)


# --- Dossier lié ---------------------------------------------------------------


def test_dossier_du_formateur_se_limite_a_ses_missions(session: Session) -> None:
    """Le dossier lié se déduit mission → organisation, offres, lots, appel."""
    organisation = Organisation(nom="Client")
    session.add(organisation)
    session.flush()
    appel = Appel(
        organisation_id=organisation.id, reference="AO-1", titre="Appel"
    )
    session.add(appel)
    session.flush()
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot 1")
    session.add(lot)
    session.flush()
    offre = Offre(
        organisation_id=organisation.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type=TypeOffre.OFFRE_TECHNIQUE.value,
        reference="O-1",
        titre="Offre",
    )
    session.add(offre)
    session.flush()
    mission = _mission(session, "M-DOSSIER")
    mission.organisation_id = organisation.id
    session.add(MissionOffre(mission_id=mission.id, offre_id=offre.id))
    session.flush()

    # Une mission hors périmètre porte un autre dossier : il doit rester fermé.
    autre_offre = Offre(
        organisation_id=organisation.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type=TypeOffre.OFFRE_FINANCIERE.value,
        reference="O-2",
        titre="Offre 2",
    )
    autre_mission = _mission(session, "M-HORS")
    session.add(autre_offre)
    session.flush()
    session.add(MissionOffre(mission_id=autre_mission.id, offre_id=autre_offre.id))
    session.flush()

    membre = _equipe(session, "Alice")
    compte = _utilisateur(
        session, role=RoleUtilisateur.FORMATEUR.value, equipe_id=membre.id
    )
    _affecter(session, mission, membre, StatutAffectation.APPROUVEE.value)
    perimetre = pour_utilisateur(session, compte)

    dossier = dossier_autorise(session, perimetre)

    assert dossier.contient_organisation(organisation.id)
    assert dossier.contient_appel(appel.id)
    assert dossier.contient_lot(lot.id)
    assert dossier.contient_offre(offre.id)
    assert not dossier.contient_offre(autre_offre.id)


def test_dossier_sans_mission_est_vide(session: Session) -> None:
    """Un périmètre restreint sans mission ne rattache rien — jamais tout."""
    assert dossier_autorise(session, PERIMETRE_VIDE) == DOSSIER_VIDE
    assert dossier_autorise(session, PERIMETRE_VIDE).organisations == frozenset()
