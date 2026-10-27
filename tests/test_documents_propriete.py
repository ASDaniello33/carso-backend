"""Propriété documentaire (ADR 0013) : un document sans objet métier appartient
à son propriétaire — et à l'administration. La visibilité publique est la
décision du propriétaire, jamais d'un autre compte.

Tests au niveau **service** (le périmètre est posé explicitement : en mode lab
l'API n'a qu'un compte technique unique, elle ne peut pas jouer deux rôles).
La liste ``lister_tous`` et la bascule de visibilité sont couvertes ici aussi,
plus la garde anti-ancrage.
"""

import io
import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services.document_service import DocumentService
from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.core.config import get_settings
from app.domain.base import Base
from app.domain.document import Document
from app.domain.enums import RoleUtilisateur, StatutUtilisateur
from app.domain.identity import Utilisateur

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d4944415478da63fcffff3f0300050001ff0a6e2d"
    "6e0000000049454e44ae426082"
)


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
    get_settings().storage_root  # lu par le service via tmp_path en pratique
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def stockage_tmp(monkeypatch, tmp_path) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    yield


def _compte(session: Session, role: str, email: str) -> Utilisateur:
    utilisateur = Utilisateur(
        email=email,
        nom="Test",
        prenom="Propriete",
        password_hash="x",
        role=role,
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
        est_complet=utilisateur.role != RoleUtilisateur.FORMATEUR.value,
    )


def _deposer(session: Session, perimetre: Perimetre, nom: str = "note-interne.png") -> Document:
    return DocumentService(session, perimetre=perimetre).enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom=nom,
            stream=io.BytesIO(_PNG),
            created_by=str(perimetre.utilisateur_id),
        )
    )


@pytest.fixture
def comptes(session: Session) -> dict[str, Utilisateur]:
    return {
        "proprietaire": _compte(
            session, RoleUtilisateur.COLLABORATEUR.value, "proprietaire@carso.mg"
        ),
        "collegue": _compte(
            session, RoleUtilisateur.COLLABORATEUR.value, "collegue@carso.mg"
        ),
        "admin": _compte(session, RoleUtilisateur.ADMINISTRATEUR.value, "admin@carso.mg"),
        "formateur": _compte(session, RoleUtilisateur.FORMATEUR.value, "formateur@carso.mg"),
    }


# --- Dépôt et propriété ------------------------------------------------------


def test_un_document_sans_ancre_appartient_a_son_deposant(
    session: Session, comptes
) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    assert document.proprietaire_id == comptes["proprietaire"].id
    assert document.visibilite == "prive"
    assert document.mission_id is None  # aucun objet métier : document personnel


def test_un_collegue_ne_voit_pas_le_document_personnel_d_un_autre(
    session: Session, comptes
) -> None:
    """Règle validée : propriétaire + administrateurs, **pas** les autres
    collaborateurs — même avec un accès complet au métier ancré."""
    document = _deposer(session, _perimetre(comptes["proprietaire"]))

    collegue = DocumentService(session, perimetre=_perimetre(comptes["collegue"]))
    with pytest.raises(NotFoundError):
        collegue.obtenir(document.id)
    # La liste ne le montre pas non plus.
    assert all(d.id != document.id for d in collegue.lister_tous())


def test_un_administrateur_voit_les_documents_personnels(session: Session, comptes) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    admin = DocumentService(session, perimetre=_perimetre(comptes["admin"]))
    assert admin.obtenir(document.id).id == document.id
    assert any(d.id == document.id for d in admin.lister_tous())


def test_le_proprietaire_voit_son_document(session: Session, comptes) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    moi = DocumentService(session, perimetre=_perimetre(comptes["proprietaire"]))
    assert moi.obtenir(document.id).id == document.id


# --- Visibilité --------------------------------------------------------------


def test_le_proprietaire_peut_rendre_son_document_public(session: Session, comptes) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))

    service = DocumentService(session, perimetre=_perimetre(comptes["proprietaire"]))
    document = service.changer_visibilite(document.id, "public")
    assert document.visibilite == "public"

    # Public : visible de tout compte actif, même sans ancre.
    collegue = DocumentService(session, perimetre=_perimetre(comptes["collegue"]))
    assert collegue.obtenir(document.id).id == document.id
    assert any(d.id == document.id for d in collegue.lister_tous())
    # Et d'un formateur : le partage public s'adresse à tous les comptes.
    formateur = DocumentService(session, perimetre=_perimetre(comptes["formateur"]))
    assert formateur.obtenir(document.id).id == document.id


def test_un_autre_compte_ne_peut_pas_publier_le_document_d_autrui(
    session: Session, comptes
) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    collegue = DocumentService(session, perimetre=_perimetre(comptes["collegue"]))
    with pytest.raises(NotFoundError):
        # La fiche est invisible pour lui : refus sans révéler son existence.
        collegue.changer_visibilite(document.id, "public")


def test_l_administration_peut_publier_un_document_personnel(session: Session, comptes) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    admin = DocumentService(session, perimetre=_perimetre(comptes["admin"]))
    assert admin.changer_visibilite(document.id, "public").visibilite == "public"


def test_visibilite_inconnue_refusee(session: Session, comptes) -> None:
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    service = DocumentService(session, perimetre=_perimetre(comptes["proprietaire"]))
    with pytest.raises(ValidationError, match="Visibilité inconnue"):
        service.changer_visibilite(document.id, "cache")


def test_la_visibilite_d_un_document_ancré_est_refusée(session: Session, comptes) -> None:
    """Un document rattaché suit le périmètre de son objet : l'indicateur
    n'aurait aucun effet — refus plutôt qu'une donnée muette."""
    from app.domain.organization import Organisation

    org = Organisation(nom="Client propriété")
    session.add(org)
    session.flush()
    service = DocumentService(session, perimetre=_perimetre(comptes["proprietaire"]))
    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom="lettre.png",
            stream=io.BytesIO(_PNG),
            created_by=str(comptes["proprietaire"].id),
            organisation_id=org.id,
        )
    )
    with pytest.raises(ValidationError, match="rattaché à un objet métier"):
        service.changer_visibilite(document.id, "public")


def test_le_proprietaire_est_renseigne_sur_les_deux_chemins(
    session: Session, comptes
) -> None:
    """Un remplacement de version reste la propriété du compte qui **dépose**
    la nouvelle version — ici sur un document **ancré** (visible de l'équipe
    interne), où deux comptes peuvent se relayer.

    Sur un document **personnel**, seul le propriétaire voit la fiche : un autre
    compte ne peut pas la remplacer (404, couvert par le test d'invisibilité).
    """
    from app.domain.organization import Organisation

    org = Organisation(nom="Client relais")
    session.add(org)
    session.flush()
    service_p = DocumentService(session, perimetre=_perimetre(comptes["proprietaire"]))
    document = service_p.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom="rapport.png",
            stream=io.BytesIO(_PNG),
            created_by=str(comptes["proprietaire"].id),
            organisation_id=org.id,
        )
    )
    assert document.proprietaire_id == comptes["proprietaire"].id

    service_c = DocumentService(session, perimetre=_perimetre(comptes["collegue"]))
    version2 = service_c.remplacer_document(
        document.id,
        DocumentUploadInput(
            type_document="autre",
            nom="rapport.png",
            stream=io.BytesIO(_PNG),
            created_by=str(comptes["collegue"].id),
            organisation_id=org.id,
        ),
    )
    assert version2.proprietaire_id == comptes["collegue"].id


def test_permission_refusee_pour_un_compte_visible_mais_pas_proprietaire(
    session: Session, comptes
) -> None:
    """Un compte qui **voit** la fiche (public) mais n'est ni propriétaire ni
    administrateur reçoit un refus explicite, pas un 404 muet."""
    document = _deposer(session, _perimetre(comptes["proprietaire"]))
    service = DocumentService(session, perimetre=_perimetre(comptes["proprietaire"]))
    service.changer_visibilite(document.id, "public")

    collegue = DocumentService(session, perimetre=_perimetre(comptes["collegue"]))
    with pytest.raises(PermissionDeniedError):
        collegue.changer_visibilite(document.id, "prive")
