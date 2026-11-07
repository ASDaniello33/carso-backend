"""Incrément 115 — rôle prévu à l'activation, création d'admin, outils agents.

Tests **hors ligne** (SQLite en mémoire, aucun réseau) :

- le rôle porté par la fiche Équipes est exposé sur la demande en attente
  (``role_souhaite``), pour que l'écran de validation le préremplisse ;
- un administrateur se crée directement, actif, tracé, avec un mot de passe
  haché jamais renvoyé ;
- ``grep`` est retiré de la requête modèle par le middleware d'exclusion ;
- l'aperçu XLSX expose feuilles, dimensions et en-tête.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.security import hasher_mot_de_passe, verifier_mot_de_passe
from app.application.services import UtilisateurService
from app.core.errors import ConflictError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, RoleUtilisateur, StatutUtilisateur
from app.domain.execution import Equipe
from app.domain.identity import Utilisateur


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _equipe(session: Session, email: str, *, role_compte: str | None = None) -> Equipe:
    """Personne du vivier active — prérequis de toute inscription."""
    nom, _, _domaine = email.partition("@")
    personne = Equipe(
        nom=nom.capitalize(),
        prenom="Test",
        email=email,
        statut="actif",
        role_compte=role_compte,
    )
    session.add(personne)
    session.flush()
    return personne


def _admin(session: Session) -> Utilisateur:
    admin = Utilisateur(
        email="admin-inc115@carso.mg",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    return admin


# --- Role souhaité exposé sur la file de validation ----------------------------


def test_demande_en_attente_expose_le_role_de_la_fiche(session: Session) -> None:
    """Le rôle « Formateur » de la fiche Équipes est visible avant validation."""
    service = UtilisateurService(session)
    _equipe(session, "formateur.inc115@carso.mg", role_compte=RoleUtilisateur.FORMATEUR.value)
    service.inscrire(
        email="formateur.inc115@carso.mg",
        nom="Rakoto",
        prenom="Aina",
        mot_de_passe="motdepasse1",
    )

    demandes = service.lister_demandes()
    assert len(demandes) == 1
    assert demandes[0].role_souhaite == RoleUtilisateur.FORMATEUR.value


def test_demande_sans_fiche_equipe_n_invente_aucun_role(session: Session) -> None:
    """Sans fiche liée, ``role_souhaite`` reste vide (jamais un rôle deviné)."""
    service = UtilisateurService(session)
    user = Utilisateur(
        email="orpheline.inc115@carso.mg",
        nom="Sans",
        prenom="Fiche",
        password_hash=hasher_mot_de_passe("motdepasse1"),
        role=RoleUtilisateur.EN_ATTENTE.value,
        statut=StatutUtilisateur.PENDING.value,
    )
    session.add(user)
    session.flush()

    demandes = service.lister_demandes()
    assert demandes[0].role_souhaite is None


# --- Création directe d'un administrateur --------------------------------------


def test_creation_administrateur_active_et_tracee(session: Session) -> None:
    service = UtilisateurService(session)
    auteur = _admin(session)

    cree = service.creer_administrateur(
        email=" Nouvel.Admin@CARSO.mg ",
        nom="Rasoanaivo",
        prenom="Hery",
        mot_de_passe="motdepasse1",
        admin_id=auteur.id,
    )

    assert cree.email == "nouvel.admin@carso.mg"
    assert cree.role == RoleUtilisateur.ADMINISTRATEUR.value
    assert cree.statut == StatutUtilisateur.ACTIF.value
    assert cree.equipe_id is None
    assert cree.approved_by == auteur.id
    assert verifier_mot_de_passe("motdepasse1", cree.password_hash)
    assert "motdepasse1" not in (cree.password_hash or "")

    evenement = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == ActionAudit.UTILISATEUR_ADMIN_CREE.value
        )
    ).first()
    assert evenement is not None
    assert str(evenement.entity_id) == str(cree.id)


def test_creation_administrateur_refuse_un_doublon(session: Session) -> None:
    service = UtilisateurService(session)
    auteur = _admin(session)

    with pytest.raises(ConflictError):
        service.creer_administrateur(
            email="admin-inc115@carso.mg",
            nom="Doublon",
            prenom="Test",
            mot_de_passe="motdepasse1",
            admin_id=auteur.id,
        )


def test_creation_administrateur_exige_un_mot_de_passe_solide(session: Session) -> None:
    service = UtilisateurService(session)
    auteur = _admin(session)

    with pytest.raises(ValidationError):
        service.creer_administrateur(
            email="faible.inc115@carso.mg",
            nom="Mot",
            prenom="Faible",
            mot_de_passe="court",
            admin_id=auteur.id,
        )


# --- Exclusion de grep ---------------------------------------------------------


class _RequeteFactice:
    """Requête modèle minimale : une liste d'outils et ``override``."""

    def __init__(self, outils: list[object]) -> None:
        self.tools = outils

    def override(self, *, tools: list[object]) -> _RequeteFactice:
        return _RequeteFactice(tools)


def test_middleware_retire_grep_et_garde_les_autres_outils() -> None:
    from app.agents.exclusion_outils import ExclusionOutilsMiddleware

    recus: list[_RequeteFactice] = []
    initiale = _RequeteFactice(
        [
            {"name": "grep"},
            {"name": "read_file"},
            {"name": "glob"},
            {"name": "chercher_dans_document"},
        ]
    )

    ExclusionOutilsMiddleware().wrap_model_call(
        initiale, lambda requete: recus.append(requete) or "ok"
    )

    noms = [outil.get("name") for outil in recus[0].tools if isinstance(outil, dict)]
    assert "grep" not in noms
    assert noms == ["read_file", "glob", "chercher_dans_document"]


def test_middleware_ne_touche_pas_une_requete_sans_grep() -> None:
    from app.agents.exclusion_outils import ExclusionOutilsMiddleware

    recus: list[_RequeteFactice] = []
    initiale = _RequeteFactice([{"name": "read_file"}])

    ExclusionOutilsMiddleware().wrap_model_call(
        initiale, lambda requete: recus.append(requete) or "ok"
    )

    assert recus[0] is initiale


# --- Aperçu XLSX ---------------------------------------------------------------


def test_structure_feuilles_xlsx(tmp_path) -> None:
    """L'aperçu d'un classeur dit ses feuilles, dimensions et en-tête."""
    openpyxl = pytest.importorskip("openpyxl")
    from app.tools.documents import _structure_feuilles_xlsx

    chemin = tmp_path / "beneficiaires.xlsx"
    classeur = openpyxl.Workbook()
    feuille = classeur.active
    feuille.title = "Bénéficiaires"
    feuille.append(["Nom", "Prénom", "Ville"])
    feuille.append(["Rakoto", "Aina", "Toliara"])
    classeur.save(chemin)

    structure = _structure_feuilles_xlsx(chemin)

    assert structure[0]["nom"] == "Bénéficiaires"
    assert structure[0]["lignes"] == 2
    assert structure[0]["colonnes"] == 3
    assert structure[0]["entete"][:3] == ["Nom", "Prénom", "Ville"]
