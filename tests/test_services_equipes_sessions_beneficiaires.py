"""Services — archivage logique, présence en lot, ancre session, import Excel.

Décisions utilisateur du 20/09 (cf. ``docs/adr/0003``) : archivage réversible
des équipes et bénéficiaires, vocabulaire de présence contrôlé, documents d'une
session ancrés sur la session, import Excel partagé entre la page et l'agent.

SQLite mémoire via les hooks de compilation de ``conftest`` ; aucun commit par
les services (la transaction appartient à l'appelant).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import (
    BeneficiaireInput,
    DocumentUploadInput,
    EquipeInput,
    ParticipationInscriptionInput,
    PresenceLotEntree,
    SessionPlanificationInput,
)
from app.application.services import (
    BeneficiaireService,
    DocumentService,
    EquipeService,
    ImportBeneficiairesService,
    ParticipationService,
    PresenceService,
    SessionService,
)
from app.application.services.import_beneficiaires_service import StatutLigneImport
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.domain.base import Base
from app.domain.document import AuditEvent, Document
from app.domain.enums import (
    ActionAudit,
    StatutBeneficiaire,
    StatutEquipe,
    StatutMission,
    TypeDocument,
)
from app.domain.execution import Mission, Presence
from app.domain.organization import Organisation
from tests.document_fixtures import build_docx, build_xlsx

# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Racine documentaire temporaire : les dépôts de test n'écrivent pas ailleurs."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def documents(session: Session, storage_root: Path) -> DocumentService:
    del storage_root
    return DocumentService(session, actor_id="test")


def _audit_actions(session: Session) -> list[str]:
    return list(session.scalars(select(AuditEvent.action)))


#: Plage de référence des tests : la session du 01 au 05/09/2026 (exemple CARSO).
JOUR_1 = date(2026, 9, 1)
JOUR_5 = date(2026, 9, 5)


def _mission(session: Session) -> Mission:
    organisation = Organisation(nom="Client Sessions")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-SESS-01",
        titre="Mission sessions",
        date_debut=JOUR_1,
        date_fin=JOUR_5,
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    return mission


def _session_formation(session: Session):  # noqa: ANN202 - retour ORM
    mission = _mission(session)
    return SessionService(session).planifier(
        SessionPlanificationInput(
            mission_id=mission.id,
            theme="Jour 1",
            date_debut=JOUR_1,
            date_fin=JOUR_5,
        )
    )


def _inscrire(session: Session, session_id, nom: str = "Sow", prenom: str = "Fatou"):
    """Inscrit une personne et renvoie sa participation."""
    personne = BeneficiaireService(session).creer(BeneficiaireInput(nom=nom, prenom=prenom))
    return ParticipationService(session).inscrire(
        session_id, ParticipationInscriptionInput(beneficiaire_id=personne.id)
    )


def _xlsx(
    documents: DocumentService, session_id, lignes: list[list[object]]
) -> Document:
    """Dépose un classeur ancré sur la session et renvoie le document."""
    contenu = build_xlsx(feuille="Liste", lignes=lignes)
    return documents.enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.LISTE_BENEFICIAIRES.value,
            nom="liste.xlsx",
            stream=io.BytesIO(contenu),
            session_id=session_id,
        )
    )


# --- Archivage logique ----------------------------------------------------------


def test_archiver_une_equipe_la_retire_des_listes_actives(session: Session) -> None:
    service = EquipeService(session, actor_id="test")
    personne = service.creer(EquipeInput(nom="Diallo", prenom="Awa"))

    service.archiver(personne.id)

    assert personne.statut == StatutEquipe.ARCHIVE.value
    assert [e.id for e in service.lister(statut=StatutEquipe.ACTIF.value)] == []
    assert [e.id for e in service.lister(statut=StatutEquipe.ARCHIVE.value)] == [personne.id]
    # Sans filtre, la personne reste visible : l'archivage ne détruit rien.
    assert [e.id for e in service.lister()] == [personne.id]
    assert ActionAudit.EQUIPE_ARCHIVEE.value in _audit_actions(session)


def test_reactiver_une_equipe(session: Session) -> None:
    service = EquipeService(session, actor_id="test")
    personne = service.creer(EquipeInput(nom="Diallo", prenom="Awa"))

    service.archiver(personne.id)
    service.reactiver(personne.id)

    assert personne.statut == StatutEquipe.ACTIF.value
    assert ActionAudit.EQUIPE_REACTIVEE.value in _audit_actions(session)


def test_archiver_deux_fois_est_refuse(session: Session) -> None:
    service = EquipeService(session, actor_id="test")
    personne = service.creer(EquipeInput(nom="Diallo", prenom="Awa"))
    service.archiver(personne.id)

    with pytest.raises(ValidationError):
        service.archiver(personne.id)


def test_archiver_un_beneficiaire_conserve_ses_participations(session: Session) -> None:
    session_formation = _session_formation(session)
    beneficiaires = BeneficiaireService(session, actor_id="test")
    personne = beneficiaires.creer(BeneficiaireInput(nom="Ndiaye", prenom="Ibrahima"))
    ParticipationService(session, actor_id="test").inscrire(
        session_formation.id, ParticipationInscriptionInput(beneficiaire_id=personne.id)
    )

    beneficiaires.archiver(personne.id)

    participations = ParticipationService(session).lister_pour_session(session_formation.id)
    assert len(participations) == 1
    assert [b.id for b in beneficiaires.lister(statut="actif")] == []


def test_archiver_une_equipe_inconnue_leve_404(session: Session) -> None:
    with pytest.raises(NotFoundError):
        EquipeService(session).archiver(uuid4())


# --- Vocabulaire de présence et pointage daté (règle 24/09) ---------------------


@pytest.mark.parametrize("valeur", ["present", "absent", "retard", "excuse"])
def test_valeurs_de_presence_acceptees(session: Session, valeur: str) -> None:
    session_formation = _session_formation(session)
    participation = _inscrire(session, session_formation.id)

    PresenceService(session, actor_id="test").pointer(
        session_formation.id,
        JOUR_1,
        [PresenceLotEntree(participation_id=participation.id, presence=valeur)],
    )

    pointages = PresenceService(session).lister_pour_session(session_formation.id)
    assert [(p.date, p.presence) for p in pointages] == [(JOUR_1, valeur)]


def test_valeur_de_presence_inconnue_refusee(session: Session) -> None:
    session_formation = _session_formation(session)
    participation = _inscrire(session, session_formation.id)

    with pytest.raises(ValidationError):
        PresenceService(session).pointer(
            session_formation.id,
            JOUR_1,
            [PresenceLotEntree(participation_id=participation.id, presence="peut-etre")],
        )


def test_pointage_en_lot_refuse_une_participation_etrangere(session: Session) -> None:
    """Une fiche de présence ne peut pas pointer la session voisine.

    Une mission ne porte plus qu'une session (ADR 0012) : la session
    « voisine » vit sur une **autre mission**.
    """
    premiere = _session_formation(session)
    organisation = Organisation(nom="Client voisin")
    session.add(organisation)
    session.flush()
    mission_voisine = Mission(
        organisation_id=organisation.id,
        reference="M-SESS-02",
        titre="Mission voisine",
        date_debut=JOUR_1,
        date_fin=JOUR_5,
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission_voisine)
    session.flush()
    seconde = SessionService(session).planifier(
        SessionPlanificationInput(mission_id=mission_voisine.id, theme="Jour 2")
    )
    participation = _inscrire(session, seconde.id)

    with pytest.raises(ValidationError):
        PresenceService(session).pointer(
            premiere.id,
            JOUR_1,
            [PresenceLotEntree(participation_id=participation.id, presence="present")],
        )


def test_une_session_se_pointe_jour_par_jour(session: Session) -> None:
    """01, 02, 03, 04, 05/09 : cinq pointages, cinq lignes — jamais une seule."""
    session_formation = _session_formation(session)
    participation = _inscrire(session, session_formation.id)
    service = PresenceService(session, actor_id="test")

    for jour in (date(2026, 9, jour) for jour in range(1, 6)):
        service.pointer(
            session_formation.id,
            jour,
            [PresenceLotEntree(participation_id=participation.id, presence="present")],
        )

    pointages = service.lister_pour_session(session_formation.id)
    assert [p.date for p in pointages] == [date(2026, 9, jour) for jour in range(1, 6)]
    # Un seul jour est retenu quand on interroge la fiche du 03.
    fiche_du_3 = service.lister_pour_session(session_formation.id, date(2026, 9, 3))
    assert [p.date for p in fiche_du_3] == [date(2026, 9, 3)]


def test_repointer_la_meme_date_corrige_sans_doublon(session: Session) -> None:
    """Une correction est une correction : elle ne crée pas une seconde ligne."""
    session_formation = _session_formation(session)
    participation = _inscrire(session, session_formation.id)
    service = PresenceService(session, actor_id="test")

    service.pointer(
        session_formation.id,
        JOUR_1,
        [PresenceLotEntree(participation_id=participation.id, presence="absent")],
    )
    service.pointer(
        session_formation.id,
        JOUR_1,
        [PresenceLotEntree(participation_id=participation.id, presence="present")],
    )

    pointages = service.lister_pour_session(session_formation.id, JOUR_1)
    assert len(pointages) == 1
    assert pointages[0].presence == "present"


def test_pointage_hors_de_la_plage_refuse(session: Session) -> None:
    """Une session du 01 au 05/09 ne se pointe ni la veille ni le lendemain."""
    session_formation = _session_formation(session)
    participation = _inscrire(session, session_formation.id)
    service = PresenceService(session)

    for hors_plage in (date(2026, 8, 31), date(2026, 9, 6)):
        with pytest.raises(ValidationError) as erreur:
            service.pointer(
                session_formation.id,
                hors_plage,
                [PresenceLotEntree(participation_id=participation.id, presence="present")],
            )
        assert hors_plage.isoformat() in str(erreur.value)


def test_pointage_sans_dates_de_session_refuse(session: Session) -> None:
    """Sans dates, aucune date n'est supposée : le service refuse et le dit."""
    mission = _mission(session)
    mission.date_debut = None
    mission.date_fin = None
    session_formation = SessionService(session).planifier(
        SessionPlanificationInput(mission_id=mission.id, theme="Sans dates")
    )
    participation = _inscrire(session, session_formation.id)

    with pytest.raises(ValidationError) as erreur:
        PresenceService(session).pointer(
            session_formation.id,
            JOUR_1,
            [PresenceLotEntree(participation_id=participation.id, presence="present")],
        )
    assert "dates" in str(erreur.value)


def test_retrait_de_l_inscription_emporte_les_pointages(session: Session) -> None:
    """Un pointage sans inscription n'a aucun sens : il part avec elle."""
    session_formation = _session_formation(session)
    participation = _inscrire(session, session_formation.id)
    PresenceService(session, actor_id="test").pointer(
        session_formation.id,
        JOUR_1,
        [PresenceLotEntree(participation_id=participation.id, presence="present")],
    )
    session.flush()
    assert len(PresenceService(session).lister_pour_session(session_formation.id)) == 1

    ParticipationService(session, actor_id="test").retirer(participation.id)
    session.flush()

    restants = list(
        session.scalars(select(Presence).where(Presence.participation_id == participation.id))
    )
    assert restants == []


def test_pointage_en_lot_est_atomique(session: Session) -> None:
    """Une ligne invalide ne laisse rien derrière elle après annulation.

    L'atomicité est portée par la transaction de l'appelant (instruction/04 §5) :
    le service ne committe jamais. Un échec en cours d'envoi, suivi du rollback
    de l'appelant, doit donc ramener la fiche à son état initial.
    """
    session_formation = _session_formation(session)
    beneficiaires = BeneficiaireService(session)
    premiere = beneficiaires.creer(BeneficiaireInput(nom="Sow", prenom="Fatou"))
    seconde = beneficiaires.creer(BeneficiaireInput(nom="Ba", prenom="Moussa"))
    participations = ParticipationService(session)
    p1 = participations.inscrire(
        session_formation.id, ParticipationInscriptionInput(beneficiaire_id=premiere.id)
    )
    p2 = participations.inscrire(
        session_formation.id, ParticipationInscriptionInput(beneficiaire_id=seconde.id)
    )
    # L'inscription de départ est acquise : seul le pointage est en jeu ici.
    session.commit()

    with pytest.raises(ValidationError):
        PresenceService(session).pointer(
            session_formation.id,
            JOUR_1,
            [
                PresenceLotEntree(participation_id=p1.id, presence="present"),
                PresenceLotEntree(participation_id=p2.id, presence="hors-vocabulaire"),
            ],
        )

    session.rollback()

    # Relecture SQL (et non d'objets en mémoire) : rien n'a été persisté.
    persistees = list(
        session.scalars(
            select(Presence.presence).where(Presence.participation_id.in_([p1.id, p2.id]))
        )
    )
    assert persistees == []


# --- Ancre documentaire de session ---------------------------------------------


def test_document_ancre_sur_une_session(session: Session, documents: DocumentService) -> None:
    session_formation = _session_formation(session)

    document = _xlsx(documents, session_formation.id, [["nom", "prenom"], ["Sow", "Fatou"]])

    assert document.session_id == session_formation.id
    assert document.storage_path.startswith(f"sessions/{session_formation.id}/")
    assert [d.id for d in documents.lister_pour("session_id", session_formation.id)] == [
        document.id
    ]


def test_ancre_session_inconnue_range_non_classe(
    session: Session, documents: DocumentService
) -> None:
    """Ancre introuvable ⇒ ``generated/{id}/`` + ``non_classe`` (comportement documenté).

    Un document sans objet métier reste un document : aucune FK n'est inventée,
    l'utilisateur peut toujours enregistrer ce qu'il demande.
    """
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.CHECKLIST.value,
            nom="checklist.docx",
            stream=io.BytesIO(build_docx(paragraphes=["checklist"])),
            session_id=uuid4(),
        )
    )
    assert document.type_document == TypeDocument.NON_CLASSE.value
    assert document.session_id is None
    assert document.storage_path.startswith("generated/")


def test_deux_ancres_sont_refusees(session: Session, documents: DocumentService) -> None:
    session_formation = _session_formation(session)
    with pytest.raises(ValidationError):
        documents.enregistrer_document(
            DocumentUploadInput(
                type_document=TypeDocument.RAPPORT.value,
                nom="rapport.docx",
                stream=io.BytesIO(b"contenu"),
                session_id=session_formation.id,
                mission_id=session_formation.mission_id,
            )
        )


# --- Import Excel partagé -------------------------------------------------------


def _import_service(session: Session) -> ImportBeneficiairesService:
    return ImportBeneficiairesService(session, actor_id="test")


def test_apercu_n_ecrit_aucun_beneficiaire(session: Session, documents: DocumentService) -> None:
    session_formation = _session_formation(session)
    document = _xlsx(
        documents,
        session_formation.id,
        [["nom", "prenom"], ["Sow", "Fatou"], ["Ndiaye", ""]],
    )

    apercu = _import_service(session).preparer(document.id, session_id=session_formation.id)

    assert apercu.nb_lignes == 2
    assert apercu.nb_valides == 1
    statuts = [ligne.statut for ligne in apercu.lignes]
    assert statuts == [StatutLigneImport.VALIDE.value, StatutLigneImport.INCOMPLETE.value]
    # Aucune écriture : ni bénéficiaire, ni participation.
    assert BeneficiaireService(session).lister() == []
    assert ParticipationService(session).lister_pour_session(session_formation.id) == []


def test_colonnes_obligatoires_absentes(session: Session, documents: DocumentService) -> None:
    session_formation = _session_formation(session)
    document = _xlsx(documents, session_formation.id, [["prenom", "ville"], ["Fatou", "Dakar"]])

    with pytest.raises(ValidationError):
        _import_service(session).preparer(document.id, session_id=session_formation.id)


def test_confirmer_cree_et_inscrit_a_la_session(
    session: Session, documents: DocumentService
) -> None:
    session_formation = _session_formation(session)
    document = _xlsx(
        documents,
        session_formation.id,
        [["nom", "prenom", "contact"], ["Sow", "Fatou", "fatou@example.org"]],
    )
    service = _import_service(session)
    apercu = service.preparer(document.id, session_id=session_formation.id)

    resultat = service.confirmer(session_id=session_formation.id, lignes=apercu.lignes)

    assert (resultat.crees, resultat.inscrits, resultat.ignores) == (1, 1, 0)
    cree = BeneficiaireService(session).lister()[0]
    assert (cree.nom, cree.prenom, cree.contact) == ("Sow", "Fatou", "fatou@example.org")
    participations = ParticipationService(session).lister_pour_session(session_formation.id)
    assert [p.beneficiaire_id for p in participations] == [cree.id]
    assert ActionAudit.BENEFICIAIRES_IMPORTES.value in _audit_actions(session)


def test_doublon_signale_jamais_fusionne(
    session: Session, documents: DocumentService
) -> None:
    """Un bénéficiaire déjà connu est inscrit, pas dupliqué (clinrules 04)."""
    session_formation = _session_formation(session)
    existant = BeneficiaireService(session).creer(
        BeneficiaireInput(nom="Sow", prenom="Fatou")
    )
    document = _xlsx(
        documents, session_formation.id, [["nom", "prenom"], ["SOW", "fatou"]]
    )
    service = _import_service(session)

    apercu = service.preparer(document.id, session_id=session_formation.id)
    assert apercu.lignes[0].statut == StatutLigneImport.DOUBLON.value

    resultat = service.confirmer(session_id=session_formation.id, lignes=apercu.lignes)

    assert (resultat.crees, resultat.doublons_signales) == (0, 1)
    assert len(BeneficiaireService(session).lister()) == 1
    participations = ParticipationService(session).lister_pour_session(session_formation.id)
    assert [p.beneficiaire_id for p in participations] == [existant.id]


def test_doublon_deja_inscrit_est_signale(session: Session, documents: DocumentService) -> None:
    session_formation = _session_formation(session)
    personne = BeneficiaireService(session).creer(
        BeneficiaireInput(nom="Sow", prenom="Fatou")
    )
    ParticipationService(session).inscrire(
        session_formation.id, ParticipationInscriptionInput(beneficiaire_id=personne.id)
    )
    document = _xlsx(documents, session_formation.id, [["nom", "prenom"], ["Sow", "Fatou"]])

    apercu = _import_service(session).preparer(document.id, session_id=session_formation.id)

    assert apercu.lignes[0].statut == StatutLigneImport.DEJA_INSCRIT.value


def test_doublon_archive_n_est_pas_ressuscite(
    session: Session, documents: DocumentService
) -> None:
    """Réactiver une personne archivée est une décision, pas un effet de bord."""
    session_formation = _session_formation(session)
    beneficiaires = BeneficiaireService(session)
    personne = beneficiaires.creer(BeneficiaireInput(nom="Sow", prenom="Fatou"))
    beneficiaires.archiver(personne.id)
    document = _xlsx(documents, session_formation.id, [["nom", "prenom"], ["Sow", "Fatou"]])
    service = _import_service(session)

    apercu = service.preparer(document.id, session_id=session_formation.id)
    assert apercu.lignes[0].statut == StatutLigneImport.DOUBLON_ARCHIVE.value

    resultat = service.confirmer(session_id=session_formation.id, lignes=apercu.lignes)

    assert (resultat.crees, resultat.inscrits) == (0, 0)
    assert personne.statut == StatutBeneficiaire.ARCHIVE.value
    assert ParticipationService(session).lister_pour_session(session_formation.id) == []


def test_les_corrections_de_utilisateur_font_foi(
    session: Session, documents: DocumentService
) -> None:
    """La ligne confirmée porte la correction humaine, pas la valeur du fichier."""
    session_formation = _session_formation(session)
    document = _xlsx(
        documents, session_formation.id, [["nom", "prenom"], ["Soww", "Fatou"]]
    )
    service = _import_service(session)
    apercu = service.preparer(document.id, session_id=session_formation.id)
    corrigee = apercu.lignes[0]
    corrigee = type(corrigee)(
        nom="Sow", prenom=corrigee.prenom, contact="nouveau@example.org"
    )

    service.confirmer(session_id=session_formation.id, lignes=[corrigee])

    cree = BeneficiaireService(session).lister()[0]
    assert (cree.nom, cree.contact) == ("Sow", "nouveau@example.org")


def test_import_sans_session_remplit_seulement_le_referentiel(
    session: Session, documents: DocumentService
) -> None:
    """Ancre mission ici : un import hors session n'inscrit personne."""
    mission = _mission(session)
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.LISTE_BENEFICIAIRES.value,
            nom="liste.xlsx",
            stream=io.BytesIO(
                build_xlsx(feuille="Liste", lignes=[["nom", "prenom"], ["Sow", "Fatou"]])
            ),
            mission_id=mission.id,
        )
    )
    service = _import_service(session)

    apercu = service.preparer(document.id)
    resultat = service.confirmer(session_id=None, lignes=apercu.lignes)

    assert (resultat.crees, resultat.inscrits) == (1, 0)


def test_importer_refuse_un_document_non_xlsx(
    session: Session, documents: DocumentService
) -> None:
    session_formation = _session_formation(session)
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.CHECKLIST.value,
            nom="checklist.txt",
            stream=io.BytesIO(b"pas un classeur"),
            session_id=session_formation.id,
        )
    )

    with pytest.raises(ValidationError):
        _import_service(session).inspecter(document.id)
