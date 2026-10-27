"""Reliaison compte ↔ fiche vivier et rôle de compte à la création (15/10).

Deux défauts corrigés dans ``EquipeService.creer`` :

- ``role_compte`` fourni à la création était **silencieusement ignoré** : la
  fiche naissait sans rôle de compte, et l'activation d'une inscription
  retombait sur « collaborateur » ;
- une fiche vivier **supprimée** déliait son compte (FK ``SET NULL``) sans
  aucun chemin de réparation : la recréer avec le même email laissait le
  compte orphelin — un formateur ainsi délié garde un périmètre **vide**
  (constat utilisateur : affecté et approuvé, dashboard « Missions = 0 »).

La création de la fiche est le moment où la source de vérité renaît : c'est
là que le lien se rétablit — jamais au détriment d'un compte déjà lié, jamais
pour un administrateur.
"""

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import EquipeInput
from app.application.perimetre import pour_utilisateur
from app.application.services import EquipeService
from app.domain.base import Base
from app.domain.enums import (
    RoleUtilisateur,
    StatutAffectation,
    StatutMission,
    StatutUtilisateur,
)
from app.domain.execution import AffectationEquipe, Mission
from app.domain.identity import Utilisateur
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


def _compte(
    email: str,
    *,
    role: str,
    equipe_id: object = None,
    statut: str = StatutUtilisateur.ACTIF.value,
) -> Utilisateur:
    return Utilisateur(
        email=email,
        nom="Rabe",
        prenom="Zaza",
        password_hash="x" * 20,
        role=role,
        statut=statut,
        equipe_id=equipe_id,  # type: ignore[arg-type]
    )


def test_creer_conserve_role_compte(session: Session) -> None:
    """Bug corrigé : le rôle de compte fourni ne doit plus être perdu."""
    fiche = EquipeService(session).creer(
        EquipeInput(
            nom="Rakoto",
            prenom="Voahangy",
            email="voahangy@carso.mg",
            role_compte=RoleUtilisateur.FORMATEUR.value,
        )
    )
    assert fiche.role_compte == RoleUtilisateur.FORMATEUR.value


def test_fiche_recreee_relit_le_compte_orphelin(session: Session) -> None:
    """Le scénario du bug : fiche supprimée → compte délié → fiche recréée.

    La reliaison doit rendre ses missions au compte : le périmètre repart des
    affectations **approuvées de la nouvelle fiche**.
    """
    email = "formateur@carso.mg"
    compte = _compte(email, role=RoleUtilisateur.FORMATEUR.value)
    session.add(compte)
    organisation = Organisation(nom="Client")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-1",
        titre="Mission 1",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()

    # La fiche recréée porte une affectation approuvée (geste postérieur à la
    # création, comme dans l'usage réel : proposer puis approuver).
    fiche = EquipeService(session).creer(
        EquipeInput(nom="Rabe", prenom="Zaza", email=email)
    )
    session.add(
        AffectationEquipe(
            mission_id=mission.id,
            equipe_id=fiche.id,
            role_dans_mission="formateur",
            statut=StatutAffectation.APPROUVEE.value,
            source_affectation="manual",
        )
    )
    session.flush()

    assert compte.equipe_id == fiche.id
    perimetre = pour_utilisateur(session, compte)
    assert not perimetre.est_complet
    assert mission.id in perimetre.missions_autorisees


def test_fiche_recreee_resynchronise_identite_et_role(session: Session) -> None:
    """La reliaison passe par la même synchronisation qu'une modification."""
    email = "sync@carso.mg"
    compte = _compte(email, role=RoleUtilisateur.COLLABORATEUR.value)
    compte.nom = "Ancien"
    session.add(compte)
    fiche = EquipeService(session).creer(
        EquipeInput(
            nom="Nouveau",
            prenom="Zaza",
            email=email.upper(),  # l'appariement est insensible à la casse
            role_compte=RoleUtilisateur.FORMATEUR.value,
        )
    )
    assert compte.equipe_id == fiche.id
    assert compte.nom == "Nouveau"
    assert compte.email == email
    assert compte.role == RoleUtilisateur.FORMATEUR.value


def test_admin_jamais_relit(session: Session) -> None:
    """Un administrateur n'est pas lié au vivier, même à email identique."""
    email = "admin@carso.mg"
    compte = _compte(email, role=RoleUtilisateur.ADMINISTRATEUR.value)
    session.add(compte)
    EquipeService(session).creer(EquipeInput(nom="Ad", prenom="Min", email=email))
    assert compte.equipe_id is None


def test_compte_deja_lie_non_vole(session: Session) -> None:
    """Deux fiches, même email : le compte lié reste lié à la sienne."""
    email = "partage@carso.mg"
    premiere = EquipeService(session).creer(
        EquipeInput(nom="Un", prenom="Premier", email=email)
    )
    compte = _compte(email, role=RoleUtilisateur.FORMATEUR.value, equipe_id=premiere.id)
    session.add(compte)
    session.flush()
    EquipeService(session).creer(EquipeInput(nom="Deux", prenom="Second", email=email))
    assert compte.equipe_id == premiere.id


def test_fiche_sans_email_ou_sans_compte(session: Session) -> None:
    """Cas ordinaires : pas d'email, pas d'orphelin — création inchangée."""
    service = EquipeService(session)
    sans_email = service.creer(EquipeInput(nom="Sans", prenom="Email"))
    assert sans_email.email is None
    fiche = service.creer(
        EquipeInput(nom="Personne", prenom="Derrière", email="inconnu@carso.mg")
    )
    assert fiche.id is not None
    assert session.scalars(select(Utilisateur)).all() == []
