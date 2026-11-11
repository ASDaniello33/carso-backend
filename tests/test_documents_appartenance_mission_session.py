"""Appartenance partagée mission / session — aucune pièce en double.

Décision utilisateur 10/11/2026 : la fiche de présence appartient à la session,
et la liste des bénéficiaires, la checklist et le rapport appartiennent à la
**mission comme à sa session**. Une mission a une session (``sessions.mission_id``
unique).

Ce que ces tests verrouillent :

- déposer depuis la mission rend la pièce visible depuis la session ;
- déposer depuis la session rend la pièce visible depuis la mission ;
- il n'existe **qu'une** ligne ``documents`` et **qu'un** fichier : aucune copie ;
- réviser la pièce ne la fait disparaître d'aucun des deux écrans ;
- redéposer la même pièce depuis l'autre écran la révise, elle ne la duplique
  pas.

Tests au niveau **service** : la règle appartient au domaine documentaire, pas
à une route.
"""

import io
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services.document_service import DocumentService
from app.core.config import get_settings
from app.domain.base import Base
from app.domain.document import Document
from app.domain.enums import RoleUtilisateur, StatutMission, StatutUtilisateur
from app.domain.execution import Mission, SessionFormation
from app.domain.identity import Utilisateur
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


@pytest.fixture(autouse=True)
def stockage_tmp(monkeypatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    yield


def _admin(session: Session) -> Utilisateur:
    utilisateur = Utilisateur(
        email="admin@carso.mg",
        nom="Test",
        prenom="Ancres",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(utilisateur)
    session.flush()
    return utilisateur


def _perimetre(utilisateur: Utilisateur) -> Perimetre:
    return Perimetre(
        role=utilisateur.role,
        utilisateur_id=utilisateur.id,
        equipe_id=None,
        est_complet=True,
    )


@pytest.fixture
def mission_et_session(session: Session) -> tuple[Mission, SessionFormation]:
    """Une mission et **sa** session, la relation réelle de la base."""
    organisation = Organisation(nom="Client Ancres")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-ANCRES-01",
        titre="Mission partagée",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    session_formation = SessionFormation(mission_id=mission.id, theme="Session partagée")
    session.add(session_formation)
    session.flush()
    return mission, session_formation


def _deposer(
    session: Session,
    perimetre: Perimetre,
    *,
    nom: str,
    type_document: str = "checklist",
    mission_id=None,
    session_id=None,
) -> Document:
    return DocumentService(session, perimetre=perimetre).enregistrer_document(
        DocumentUploadInput(
            type_document=type_document,
            nom=nom,
            stream=io.BytesIO(b"contenu de la checklist"),
            created_by=str(perimetre.utilisateur_id),
            mission_id=mission_id,
            session_id=session_id,
        )
    )


def _lignes(session: Session, nom: str) -> int:
    return session.scalar(
        select(func.count()).select_from(Document).where(Document.nom == nom)
    )


# --- Dépôt depuis la mission, lecture depuis la session ---------------------


def test_depot_sur_la_mission_visible_depuis_la_session(
    session: Session, mission_et_session
) -> None:
    mission, session_formation = mission_et_session
    perimetre = _perimetre(_admin(session))

    document = _deposer(session, perimetre, nom="checklist.txt", mission_id=mission.id)

    # Une seule ligne, deux ancres : la pièce n'est pas copiée.
    assert _lignes(session, "checklist.txt") == 1
    assert document.mission_id == mission.id
    assert document.session_id == session_formation.id

    service = DocumentService(session, perimetre=perimetre)
    assert [d.id for d in service.lister_pour("mission_id", mission.id)] == [document.id]
    assert [d.id for d in service.lister_pour("session_id", session_formation.id)] == [
        document.id
    ]


def test_depot_sur_la_session_visible_depuis_la_mission(
    session: Session, mission_et_session
) -> None:
    """Le sens inverse compte autant : la liste des bénéficiaires se dépose au
    jour J depuis la session et sert encore à la préparation de la mission."""
    mission, session_formation = mission_et_session
    perimetre = _perimetre(_admin(session))

    document = _deposer(
        session,
        perimetre,
        nom="beneficiaires.txt",
        type_document="liste_beneficiaires",
        session_id=session_formation.id,
    )

    assert _lignes(session, "beneficiaires.txt") == 1
    assert document.session_id == session_formation.id
    assert document.mission_id == mission.id  # l'ancre manquante est complétée

    service = DocumentService(session, perimetre=perimetre)
    assert [d.id for d in service.lister_pour("mission_id", mission.id)] == [document.id]
    assert [d.id for d in service.lister_pour("session_id", session_formation.id)] == [
        document.id
    ]


def test_un_seul_fichier_sur_le_disque(session: Session, mission_et_session) -> None:
    """Aucune copie d'octets : le document n'existe qu'une fois sur disque."""
    mission, session_formation = mission_et_session
    perimetre = _perimetre(_admin(session))
    document = _deposer(session, perimetre, nom="rapport.txt", mission_id=mission.id)

    racine = Path(get_settings().storage_root)
    fichiers = [p for p in racine.rglob("*") if p.is_file()]
    assert len(fichiers) == 1
    assert document.storage_path in {str(p.relative_to(racine)).replace("\\", "/") for p in fichiers}


# --- Révision et non-duplication -------------------------------------------


def test_reviser_une_piece_ne_la_retire_d_aucun_des_deux_ecrans(
    session: Session, mission_et_session
) -> None:
    mission, session_formation = mission_et_session
    perimetre = _perimetre(_admin(session))
    premier = _deposer(session, perimetre, nom="checklist.txt", mission_id=mission.id)

    service = DocumentService(session, perimetre=perimetre)
    second = service.remplacer_document(
        premier.id,
        DocumentUploadInput(
            type_document="checklist",
            nom="checklist.txt",
            stream=io.BytesIO(b"contenu revise"),
            created_by=str(perimetre.utilisateur_id),
        ),
    )

    assert second.version == 2
    assert second.mission_id == mission.id
    assert second.session_id == session_formation.id

    # La version 2 apparaît des deux côtés, la version 1 reste l'historique.
    for filtre, identifiant in (
        ("mission_id", mission.id),
        ("session_id", session_formation.id),
    ):
        ids = {d.id for d in service.lister_pour(filtre, identifiant)}
        assert second.id in ids
        assert premier.id in ids  # même nom : l'historique des versions reste lisible


def test_redeposer_depuis_l_autre_ecran_revise_au_lieu_de_dupliquer(
    session: Session, mission_et_session
) -> None:
    """La duplication qu'il fallait éviter : déposer depuis la mission, puis
    depuis la session. C'est la même pièce, elle se révise."""
    mission, session_formation = mission_et_session
    perimetre = _perimetre(_admin(session))
    service = DocumentService(session, perimetre=perimetre)

    premier = service.deposer_livrable(
        DocumentUploadInput(
            type_document="checklist",
            nom="checklist.txt",
            stream=io.BytesIO(b"version mission"),
            created_by=str(perimetre.utilisateur_id),
            mission_id=mission.id,
        )
    )
    second = service.deposer_livrable(
        DocumentUploadInput(
            type_document="checklist",
            nom="checklist.txt",
            stream=io.BytesIO(b"version session"),
            created_by=str(perimetre.utilisateur_id),
            session_id=session_formation.id,
        )
    )

    assert second.version == 2
    # Une seule famille pour ce nom : deux lignes de version, pas quatre.
    assert _lignes(session, "checklist.txt") == 2
    assert len({d.id for d in service.lister_pour("mission_id", mission.id)}) == 2
