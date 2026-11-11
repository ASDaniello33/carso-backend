"""Dossier personnel — la définition est celle du serveur, pas de l'écran.

Demande 11/11 : les documents personnels d'un compte sont ceux qu'il a importés
dans son dossier, ou fait produire par son agent personnel, et qui ne sont
rattachés à **aucun** objet métier (organisation, appel, offre, mission,
intervenant, session). Le défaut constaté : l'écran calculait cette règle de son
côté, et l'administration y voyait des documents métier.

Ce que ces tests verrouillent :

- un document **sans ancre** de mon compte est mien ;
- un document **avec ancre** de mon compte n'est **pas** personnel, même s'il
  est rangé dans le dossier du compte ou si je l'ai déposé moi-même ;
- le document sans ancre d'un **collègue** n'est pas mien (un administrateur ne
  voit pas « son » dossier peuplé du dossier des autres) ;
- un fichier **resté sous un dossier métier** après que son ancre a été
  effacée redevient personnel : les six ancres font foi, pas le chemin ;
- un appel **sans compte** refuse la lecture (pas de dossier sans personne).
"""

import io
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import DecisionInput, DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services.document_service import DocumentService
from app.core.config import get_settings
from app.core.errors import PermissionDeniedError
from app.domain.base import Base
from app.domain.document import Document
from app.domain.enums import RoleUtilisateur, StatutMission, StatutUtilisateur
from app.domain.execution import Mission
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


def _compte(session: Session, role: str, email: str) -> Utilisateur:
    utilisateur = Utilisateur(
        email=email,
        nom="Test",
        prenom="Dossier",
        password_hash="x",
        role=role,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(utilisateur)
    session.flush()
    return utilisateur


def _perimetre(utilisateur: Utilisateur | None) -> Perimetre:
    if utilisateur is None:
        return Perimetre(role="administrateur", utilisateur_id=None, equipe_id=None, est_complet=True)
    return Perimetre(
        role=utilisateur.role,
        utilisateur_id=utilisateur.id,
        equipe_id=None,
        est_complet=utilisateur.role != RoleUtilisateur.FORMATEUR.value,
    )


def _deposer(
    session: Session,
    perimetre: Perimetre,
    *,
    nom: str,
    mission_id=None,
) -> Document:
    return DocumentService(session, perimetre=perimetre).enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom=nom,
            stream=io.BytesIO(b"contenu personnel"),
            created_by=str(perimetre.utilisateur_id),
            mission_id=mission_id,
        )
    )


def _noms(session: Session, utilisateur: Utilisateur | None) -> set[str]:
    return {
        d.nom
        for d in DocumentService(session, perimetre=_perimetre(utilisateur)).lister_personnels()
    }


@pytest.fixture
def comptes(session: Session) -> dict[str, Utilisateur]:
    return {
        "moi": _compte(session, RoleUtilisateur.COLLABORATEUR.value, "moi@carso.mg"),
        "collegue": _compte(session, RoleUtilisateur.COLLABORATEUR.value, "collegue@carso.mg"),
        "admin": _compte(session, RoleUtilisateur.ADMINISTRATEUR.value, "admin@carso.mg"),
    }


# --- Ce qui est personnel ----------------------------------------------------


def test_un_document_sans_ancre_de_mon_compte_est_personnel(session, comptes) -> None:
    _deposer(session, _perimetre(comptes["moi"]), nom="affiche.txt")
    assert _noms(session, comptes["moi"]) == {"affiche.txt"}


def test_un_document_ancre_sur_une_mission_n_est_pas_personnel(session, comptes) -> None:
    """Le cas du défaut : un livrable de mission déposé par moi reste un
    document **de mission**. Le dossier personnel n'est pas une corbeille de
    tout ce que j'ai touché."""
    organisation = Organisation(nom="Client Dossier")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-DOSSIER-01",
        titre="Mission",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()

    _deposer(session, _perimetre(comptes["moi"]), nom="checklist.txt", mission_id=mission.id)
    _deposer(session, _perimetre(comptes["moi"]), nom="affiche.txt")

    assert _noms(session, comptes["moi"]) == {"affiche.txt"}


def test_le_document_sans_ancre_d_un_collegue_n_est_pas_le_mien(session, comptes) -> None:
    _deposer(session, _perimetre(comptes["collegue"]), nom="chez-collegue.txt")
    _deposer(session, _perimetre(comptes["moi"]), nom="chez-moi.txt")
    assert _noms(session, comptes["moi"]) == {"chez-moi.txt"}


def test_l_administrateur_ne_voit_pas_les_dossiers_des_autres(session, comptes) -> None:
    """« Mon dossier » d'un administrateur ne contient que le sien : il *voit*
    les documents des autres par la gestion, pas ici."""
    _deposer(session, _perimetre(comptes["moi"]), nom="chez-moi.txt")
    _deposer(session, _perimetre(comptes["admin"]), nom="chez-admin.txt")
    assert _noms(session, comptes["admin"]) == {"chez-admin.txt"}


def test_un_fichier_reste_sous_un_dossier_metier_redevient_personnel(
    session, comptes
) -> None:
    """Un document peut garder ``missions/<id>/`` comme chemin de stockage alors
    que ses ancres ont été effacées. Le chemin ne décide pas : la fiche est un
    document personnel, et le fichier doit se télécharger."""
    document = _deposer(session, _perimetre(comptes["moi"]), nom="orphelin.txt")
    document.mission_id = None
    document.storage_path = "missions/00000000-0000-0000-0000-000000000000/orphelin.txt"
    session.flush()

    personnels = DocumentService(session, perimetre=_perimetre(comptes["moi"])).lister_personnels()
    assert [d.nom for d in personnels] == ["orphelin.txt"]
    assert personnels[0].storage_path.startswith("missions/")


def test_les_fiches_supprimees_ne_sont_pas_dans_le_dossier(session, comptes) -> None:
    document = _deposer(session, _perimetre(comptes["moi"]), nom="parti.txt")
    service = DocumentService(session, perimetre=_perimetre(comptes["moi"]))
    service.supprimer_document(
        document.id,
        DecisionInput(decided_by=str(comptes["moi"].id), reason="verification"),
    )
    assert _noms(session, comptes["moi"]) == set()


def test_un_appel_sans_compte_refuse_la_lecture(session, comptes) -> None:
    """Un dossier personnel sans personne n'existe pas (scripts, balayage)."""
    with pytest.raises(PermissionDeniedError):
        DocumentService(session, perimetre=_perimetre(None)).lister_personnels()


def test_aucune_ligne_du_dossier_n_a_d_ancre(session, comptes) -> None:
    """Contrôle direct sur la requête : la lecture ne rend jamais un document
    rattaché à un objet métier, quel que soit son type ou son chemin."""
    _deposer(session, _perimetre(comptes["moi"]), nom="affiche.txt")
    personnels = session.scalars(
        select(Document).where(Document.proprietaire_id == comptes["moi"].id)
    ).all()

    service = DocumentService(session, perimetre=_perimetre(comptes["moi"]))
    for document in service.lister_personnels():
        assert (document.organisation_id, document.appel_id, document.offre_id) == (
            None,
            None,
            None,
        )
        assert (document.mission_id, document.equipe_id, document.session_id) == (
            None,
            None,
            None,
        )
    assert len(personnels) == 1
