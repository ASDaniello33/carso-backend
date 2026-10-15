"""Règles métier confirmées CARSO — Offre, type d'appel, Lieu, rôles, supports.

Tests purs sur SQLite mémoire (mêmes conventions que ``test_services.py``) : ils
vérifient les règles de modélisation validées avec CARSO :

1. ``Offre`` générique répondant à ``Appel + Lot`` avec un type explicite ;
2. appel à proposition **ou** appel à manifestation d'intérêt ;
3. lieu d'exécution distinct, porté par la mission ;
4. mission référençant plusieurs offres (technique + financière) d'un même lot ;
5. chef de mission également formateur, sans rôle contradictoire ;
6. support de formation : relation Formateur ↔ Mission ↔ Document.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import (
    AppelAPropositionInput,
    LieuInput,
    MissionDepuisOffresInput,
    SupportFormationInput,
)
from app.application.services import (
    AppelAPropositionService,
    LieuService,
    MissionService,
    OffreService,
    SupportFormationService,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.base import Base
from app.domain.document import Document
from app.domain.enums import (
    RoleMission,
    StatutOffre,
    TypeAppel,
    TypeDocument,
    TypeOffre,
    est_formateur,
)
from app.domain.execution import AffectationEquipe, Equipe, Mission
from app.domain.organization import AppelAProposition, Lot, Offre, Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


# --- Fixtures métier ------------------------------------------------------------


def _appel_valide(
    session: Session, *, type_appel: str = TypeAppel.APPEL_A_PROPOSITION.value
) -> AppelAProposition:
    org = Organisation(nom="Client")
    session.add(org)
    session.flush()
    appel = AppelAPropositionService(session).enregistrer(
        AppelAPropositionInput(
            organisation_id=org.id,
            reference="AO-2026-01",
            titre="Appel test",
            type=type_appel,
        )
    )
    session.flush()
    return appel


def _lot(session: Session, appel: AppelAProposition, numero: str = "1") -> Lot:
    lot = Lot(appel_a_proposition_id=appel.id, numero=numero, titre=f"Lot {numero}")
    session.add(lot)
    session.flush()
    return lot


def _offre(
    session: Session,
    lot: Lot,
    *,
    offre_type: str = TypeOffre.OFFRE_TECHNIQUE.value,
    reference: str = "OF-1",
) -> Offre:
    """Crée un brouillon d'offre pour ce lot et ce type."""
    return OffreService(session).creer_brouillon(
        lot.id,
        reference,
        f"Offre {reference}",
        type=offre_type,
    )


def _decision():
    from app.application.trace import Decision

    return Decision(decided_by="carso")


def _approuver(session: Session, offre: Offre) -> Offre:
    """Amène une offre jusqu'à l'approbation (brouillon → revue → approuve)."""
    service = OffreService(session)
    if offre.statut == StatutOffre.BROUILLON.value:
        service.soumettre_revue(offre.id)
    service.publier(offre.id, _decision())
    return offre


# --- 1. Offre générique : type explicite ----------------------------------------


class TestOffreType:
    def test_offre_porte_son_type_et_son_appel(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        offre = _offre(session, lot, offre_type=TypeOffre.OFFRE_FINANCIERE.value)

        assert offre.type == TypeOffre.OFFRE_FINANCIERE.value
        assert offre.appel_a_proposition_id == appel.id
        assert offre.lot_id == lot.id

    def test_un_lot_porte_technique_et_financiere(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        technique = _offre(session, lot, offre_type=TypeOffre.OFFRE_TECHNIQUE.value)
        financiere = _offre(
            session,
            lot,
            offre_type=TypeOffre.OFFRE_FINANCIERE.value,
            reference="OF-2",
        )

        assert {technique.type, financiere.type} == {
            TypeOffre.OFFRE_TECHNIQUE.value,
            TypeOffre.OFFRE_FINANCIERE.value,
        }

    def test_type_duplique_refuse(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        _offre(session, lot, offre_type=TypeOffre.OFFRE_TECHNIQUE.value)
        with pytest.raises(ConflictError, match="existe déjà"):
            _offre(
                session,
                lot,
                offre_type=TypeOffre.OFFRE_TECHNIQUE.value,
                reference="OF-2",
            )

    def test_type_inconnu_refuse(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        with pytest.raises(ValidationError, match="Type d'offre inconnu"):
            OffreService(session).creer_brouillon(lot.id, "OF-X", "Offre", type="invente")

    def test_appel_incoherent_refuse(self, session: Session) -> None:
        appel = _appel_valide(session)
        autre = AppelAPropositionService(session).enregistrer(
            AppelAPropositionInput(
                organisation_id=appel.organisation_id,
                reference="AO-2",
                titre="Autre appel",
            )
        )
        lot = _lot(session, appel)
        with pytest.raises(ValidationError, match="ne correspond pas"):
            OffreService(session).creer_brouillon(
                lot.id,
                "OF-X",
                "Offre",
                type=TypeOffre.OFFRE_TECHNIQUE.value,
                appel_a_proposition_id=autre.id,
            )

    def test_lot_manuel_sur_appel_recu_autorise(self, session: Session) -> None:
        """Un lot saisi à la main sur un appel encore ``recu`` ouvre les offres."""
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        offre = _offre(session, lot)
        assert offre.lot_id == lot.id
        assert offre.appel_a_proposition_id == appel.id

    def test_appel_rejete_refuse_le_brouillon(self, session: Session) -> None:
        """Un appel rejeté ne reçoit plus d'offre, même si un lot existe."""
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        AppelAPropositionService(session).rejeter_appel(appel.id, _decision())
        with pytest.raises(ValidationError, match="appel rejeté"):
            _offre(session, lot)


# --- 2. Type d'appel -------------------------------------------------------------


class TestTypeAppel:
    def test_manifestation_d_interet_est_un_type_distinct(self, session: Session) -> None:
        appel = _appel_valide(
            session, type_appel=TypeAppel.APPEL_A_MANIFESTATION_INTERET.value
        )
        assert appel.type == TypeAppel.APPEL_A_MANIFESTATION_INTERET.value

    def test_autre_type_d_appel_accepte(self, session: Session) -> None:
        """Un appel d'un autre type se reçoit et se consulte comme les autres."""
        appel = _appel_valide(session, type_appel=TypeAppel.AUTRE.value)
        assert appel.type == TypeAppel.AUTRE.value

    def test_type_par_defaut_est_appel_a_proposition(self, session: Session) -> None:
        appel = _appel_valide(session)
        assert appel.type == TypeAppel.APPEL_A_PROPOSITION.value

    def test_type_inconnu_refuse(self, session: Session) -> None:
        org = Organisation(nom="Client")
        session.add(org)
        session.flush()
        with pytest.raises(ValidationError, match="Type d'appel inconnu"):
            AppelAPropositionService(session).enregistrer(
                AppelAPropositionInput(
                    organisation_id=org.id,
                    reference="AO-X",
                    titre="Appel",
                    type="type_invente",
                )
            )


# --- 3. Lieu ----------------------------------------------------------------------


class TestLieu:
    def test_lieu_cree_puis_reutilise(self, session: Session) -> None:
        service = LieuService(session)
        lieu = service.creer(LieuInput(nom="Toliara", ville="Toliara"))
        assert lieu.nom == "Toliara"
        assert service.obtenir_ou_creer("toliara").id == lieu.id

    def test_lieu_duplique_refuse(self, session: Session) -> None:
        service = LieuService(session)
        service.creer(LieuInput(nom="Antananarivo"))
        with pytest.raises(ConflictError, match="existe déjà"):
            service.creer(LieuInput(nom="Antananarivo"))

    def test_mission_reference_un_lieu(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        offre = _approuver(session, _offre(session, lot))
        lieu = LieuService(session).creer(LieuInput(nom="Nosy Be"))

        mission = MissionService(session).creer_depuis_offres(
            MissionDepuisOffresInput(
                offre_ids=(offre.id,),
                reference="M-1",
                titre="Mission",
                lieu_id=lieu.id,
            )
        )
        assert mission.lieu_id == lieu.id

    def test_lieu_inconnu_refuse(self, session: Session) -> None:
        from uuid import uuid4

        appel = _appel_valide(session)
        lot = _lot(session, appel)
        offre = _approuver(session, _offre(session, lot))
        with pytest.raises(NotFoundError, match="Lieu"):
            MissionService(session).creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(offre.id,),
                    reference="M-1",
                    titre="Mission",
                    lieu_id=uuid4(),
                )
            )


# --- 4. Mission ↔ plusieurs offres -----------------------------------------------


class TestMissionDepuisPlusieursOffres:
    def test_technique_et_financiere_alimentent_la_mission(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        technique = _approuver(
            session, _offre(session, lot, offre_type=TypeOffre.OFFRE_TECHNIQUE.value)
        )
        financiere = _approuver(
            session,
            _offre(
                session,
                lot,
                offre_type=TypeOffre.OFFRE_FINANCIERE.value,
                reference="OF-2",
            ),
        )

        mission = MissionService(session).creer_depuis_offres(
            MissionDepuisOffresInput(
                offre_ids=(technique.id, financiere.id),
                reference="M-1",
                titre="Mission",
            )
        )

        assert {o.id for o in mission.offres} == {technique.id, financiere.id}

    def test_offres_de_lots_differents_refusees(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot_a = _lot(session, appel, "1")
        lot_b = _lot(session, appel, "2")
        offre_a = _approuver(session, _offre(session, lot_a))
        offre_b = _approuver(
            session, _offre(session, lot_b, reference="OF-2")
        )

        with pytest.raises(ValidationError, match="même lot"):
            MissionService(session).creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(offre_a.id, offre_b.id),
                    reference="M-1",
                    titre="Mission",
                )
            )

    def test_offres_non_approuvees_refusees(self, session: Session) -> None:
        appel = _appel_valide(session)
        lot = _lot(session, appel)
        offre = _offre(session, lot)  # brouillon
        with pytest.raises(ConflictError, match="approuvées"):
            MissionService(session).creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(offre.id,), reference="M-1", titre="Mission"
                )
            )

    def test_liste_vide_refusee(self, session: Session) -> None:
        with pytest.raises(ValidationError, match="Au moins une offre"):
            MissionService(session).creer_depuis_offres(
                MissionDepuisOffresInput(offre_ids=(), reference="M-1", titre="Mission")
            )


# --- 5 & 6. Rôles et supports de formation ---------------------------------------


def _mission_avec_equipe(
    session: Session, *roles: str
) -> tuple[Mission, list[Equipe]]:
    appel = _appel_valide(session)
    lot = _lot(session, appel)
    offre = _approuver(session, _offre(session, lot))
    mission = MissionService(session).creer_depuis_offres(
        MissionDepuisOffresInput(
            offre_ids=(offre.id,), reference="M-1", titre="Mission"
        )
    )
    personnes: list[Equipe] = []
    for index, role in enumerate(roles):
        personne = Equipe(nom=f"Nom{index}", prenom=f"Prenom{index}")
        session.add(personne)
        session.flush()
        session.add(
            AffectationEquipe(
                mission_id=mission.id,
                equipe_id=personne.id,
                role_dans_mission=role,
            )
        )
        personnes.append(personne)
    session.flush()
    return mission, personnes


class TestRolesEtSupports:
    def test_chef_de_mission_est_formateur(self) -> None:
        assert est_formateur(RoleMission.CHEF_DE_MISSION.value) is True
        assert est_formateur(RoleMission.FORMATEUR.value) is True
        assert est_formateur(RoleMission.COACH_FORMATEUR.value) is False

    def test_support_par_un_formateur(self, session: Session) -> None:
        mission, (formateur,) = _mission_avec_equipe(session, RoleMission.FORMATEUR.value)
        document = Document(
            nom="Module.pdf",
            type_document=TypeDocument.AUTRE.value,
            storage_path=f"missions/{mission.id}/Module.pdf",
            mission_id=mission.id,
        )
        session.add(document)
        session.flush()

        support = SupportFormationService(session).ajouter(
            SupportFormationInput(
                mission_id=mission.id,
                equipe_id=formateur.id,
                document_id=document.id,
                libelle="Module entrepreneuriat",
            )
        )
        assert support.document_id == document.id
        assert support.equipe_id == formateur.id

    def test_chef_de_mission_peut_porter_un_support(self, session: Session) -> None:
        mission, (chef,) = _mission_avec_equipe(session, RoleMission.CHEF_DE_MISSION.value)
        document = Document(
            nom="Module.pdf",
            type_document=TypeDocument.AUTRE.value,
            storage_path=f"missions/{mission.id}/Module.pdf",
            mission_id=mission.id,
        )
        session.add(document)
        session.flush()

        support = SupportFormationService(session).ajouter(
            SupportFormationInput(
                mission_id=mission.id, equipe_id=chef.id, document_id=document.id
            )
        )
        assert support.id is not None

    def test_non_formateur_refuse(self, session: Session) -> None:
        mission, (logistique,) = _mission_avec_equipe(
            session, RoleMission.ASSISTANCE_LOGISTIQUE.value
        )
        document = Document(
            nom="Module.pdf",
            type_document=TypeDocument.AUTRE.value,
            storage_path=f"missions/{mission.id}/Module.pdf",
            mission_id=mission.id,
        )
        session.add(document)
        session.flush()

        with pytest.raises(ConflictError, match="formateur"):
            SupportFormationService(session).ajouter(
                SupportFormationInput(
                    mission_id=mission.id,
                    equipe_id=logistique.id,
                    document_id=document.id,
                )
            )

    def test_personne_non_affectee_refusee(self, session: Session) -> None:
        mission, _ = _mission_avec_equipe(session, RoleMission.FORMATEUR.value)
        etranger = Equipe(nom="Etranger", prenom="X")
        document = Document(
            nom="Module.pdf",
            type_document=TypeDocument.AUTRE.value,
            storage_path=f"missions/{mission.id}/Module.pdf",
            mission_id=mission.id,
        )
        session.add_all([etranger, document])
        session.flush()

        with pytest.raises(ConflictError, match="affectée"):
            SupportFormationService(session).ajouter(
                SupportFormationInput(
                    mission_id=mission.id,
                    equipe_id=etranger.id,
                    document_id=document.id,
                )
            )

    def test_support_duplique_refuse(self, session: Session) -> None:
        mission, (formateur,) = _mission_avec_equipe(session, RoleMission.FORMATEUR.value)
        document = Document(
            nom="Module.pdf",
            type_document=TypeDocument.AUTRE.value,
            storage_path=f"missions/{mission.id}/Module.pdf",
            mission_id=mission.id,
        )
        session.add(document)
        session.flush()
        service = SupportFormationService(session)
        service.ajouter(
            SupportFormationInput(
                mission_id=mission.id, equipe_id=formateur.id, document_id=document.id
            )
        )
        with pytest.raises(ConflictError, match="déjà un support"):
            service.ajouter(
                SupportFormationInput(
                    mission_id=mission.id,
                    equipe_id=formateur.id,
                    document_id=document.id,
                )
            )
