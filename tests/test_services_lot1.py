"""Lot C1 — tests services CRUD (SQLite mémoire, sans commit).

Couvre : créations, modifications partielles, 404 sur entités absentes,
conflits (numéro de lot, double inscription), traçabilité AuditEvent.
Le commit appartient à l'appelant : les services sont vérifiés sans commit.
"""

import re
from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import (
    BeneficiaireInput,
    BeneficiaireUpdateInput,
    EquipeInput,
    EquipeUpdateInput,
    LotInput,
    LotUpdateInput,
    OrganisationInput,
    OrganisationUpdateInput,
)
from app.application.services import (
    BeneficiaireService,
    EquipeService,
    LotService,
    OrganisationService,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.document import AuditEvent
from app.domain.organization import Appel, Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    from app.domain.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _audit_actions(session: Session) -> list[str]:
    return list(session.scalars(select(AuditEvent.action)))


def _seed_appel(session: Session) -> Appel:
    org = Organisation(nom="Client Lots")
    session.add(org)
    session.flush()
    appel = Appel(organisation_id=org.id, reference="AO-LOT1-01", titre="Appel lots")
    session.add(appel)
    session.flush()
    return appel


# --- Organisations ------------------------------------------------------------


def test_organisation_creer_obtenir_modifier(session: Session) -> None:
    service = OrganisationService(session)
    org = service.creer(OrganisationInput(nom="Acme", email="c@acme.ma"))
    assert org.id is not None
    assert service.obtenir(org.id).nom == "Acme"
    modifiee = service.modifier(org.id, OrganisationUpdateInput(telephone="0600"))
    assert modifiee.telephone == "0600"
    assert modifiee.nom == "Acme"  # champs non fournis inchangés
    assert _audit_actions(session) == [
        "organisation.enregistree",
        "organisation.modifiee",
    ]


def test_organisation_inexistante_404(session: Session) -> None:
    service = OrganisationService(session)
    with pytest.raises(NotFoundError):
        service.obtenir(uuid4())
    with pytest.raises(NotFoundError):
        service.modifier(uuid4(), OrganisationUpdateInput(nom="X"))


def test_organisation_modifier_sans_changement_422(session: Session) -> None:
    service = OrganisationService(session)
    org = service.creer(OrganisationInput(nom="Acme"))
    with pytest.raises(ValidationError):
        service.modifier(org.id, OrganisationUpdateInput())


def test_organisation_lister(session: Session) -> None:
    service = OrganisationService(session)
    service.creer(OrganisationInput(nom="Une"))
    service.creer(OrganisationInput(nom="Deux"))
    assert len(service.lister()) == 2


# --- Équipes ------------------------------------------------------------------


def test_equipe_creer_modifier(session: Session) -> None:
    service = EquipeService(session)
    equipe = service.creer(EquipeInput(nom="Ben", prenom="Ali", profil="Formateur"))
    assert service.obtenir(equipe.id).profil == "Formateur"
    service.modifier(equipe.id, EquipeUpdateInput(profil="Expert", statut="actif"))
    assert service.obtenir(equipe.id).statut == "actif"
    assert _audit_actions(session) == ["equipe.enregistree", "equipe.modifiee"]


def test_equipe_inexistante_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        EquipeService(session).obtenir(uuid4())


# --- Lots ---------------------------------------------------------------------


def test_lot_ajouter_lister(session: Session) -> None:
    appel = _seed_appel(session)
    service = LotService(session)
    lot = service.ajouter(
        LotInput(appel_id=appel.id, numero="1", titre="Lot 1")
    )
    assert service.obtenir(lot.id).titre == "Lot 1"
    lots = service.lister_pour_appel(appel.id)
    assert [lot_item.numero for lot_item in lots] == ["1"]
    assert _audit_actions(session) == ["lot.enregistre"]


def test_lot_appel_inexistant_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        LotService(session).ajouter(
            LotInput(appel_id=uuid4(), numero="1", titre="Lot 1")
        )


def test_lot_numero_duplique_409(session: Session) -> None:
    appel = _seed_appel(session)
    service = LotService(session)
    service.ajouter(LotInput(appel_id=appel.id, numero="1", titre="Lot 1"))
    with pytest.raises(ConflictError):
        service.ajouter(LotInput(appel_id=appel.id, numero="1", titre="Doublon"))


def test_lot_modifier_numero_duplique_409(session: Session) -> None:
    appel = _seed_appel(session)
    service = LotService(session)
    service.ajouter(LotInput(appel_id=appel.id, numero="1", titre="Lot 1"))
    lot2 = service.ajouter(LotInput(appel_id=appel.id, numero="2", titre="Lot 2"))
    with pytest.raises(ConflictError):
        service.modifier(lot2.id, LotUpdateInput(numero="1"))
    # renommage licite vers un numéro libre
    service.modifier(lot2.id, LotUpdateInput(titre="Lot 2 bis", zone="Nord"))
    assert service.obtenir(lot2.id).zone == "Nord"


def test_lot_zones_source(session: Session) -> None:
    appel = _seed_appel(session)
    service = LotService(session)
    lot = service.ajouter(
        LotInput(
            appel_id=appel.id,
            numero="1",
            titre="Lot 1",
            objectifs="Former 100 agents",
            resultats_attendus="Certification",
            date_fin=date(2026, 12, 31),
            donnees_source={"page": 3, "confiance": "haute"},
        )
    )
    assert lot.donnees_source == {"page": 3, "confiance": "haute"}


# --- Bénéficiaires ------------------------------------------------------------


def test_beneficiaire_creer_modifier(session: Session) -> None:
    service = BeneficiaireService(session)
    ben = service.creer(BeneficiaireInput(nom="Tazi", prenom="Sara", contact="0612345678"))
    assert service.obtenir(ben.id).contact == "0612345678"
    service.modifier(ben.id, BeneficiaireUpdateInput(organisation_origine="Coopérative X"))
    assert service.obtenir(ben.id).organisation_origine == "Coopérative X"
    assert _audit_actions(session) == [
        "beneficiaire.enregistre",
        "beneficiaire.modifie",
    ]
    assert len(service.lister()) == 1


def test_beneficiaire_inexistant_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        BeneficiaireService(session).obtenir(uuid4())
