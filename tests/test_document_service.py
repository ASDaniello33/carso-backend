"""Phase 4 (instruction/11) — service documentaire.

Vérifie le pipeline d'enregistrement, le versioning **sans écrasement**, les
décisions humaines tracées (Approbation + AuditEvent), l'extraction en lecture
seule et le registre des modèles de documents.

SQLite mémoire : les hooks de compilation (JSONB, gen_random_uuid) sont fournis
par ``conftest``. Aucun commit n'est fait par les services — la transaction
appartient à l'appelant, donc les tests se contentent de ``flush``/requêtes.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DecisionInput, DocumentUploadInput
from app.application.services import DocumentService, ModeleDocumentService
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.documents.previews import chemin_apercu
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.document import Approbation, AuditEvent, Document
from app.domain.enums import (
    ActionAudit,
    ActorType,
    DecisionApprobation,
    StatutDocument,
    StatutMission,
    StatutOffre,
    TypeDocument,
    TypeOffre,
    TypeProposition,
)
from app.domain.execution import Equipe, Mission
from app.domain.organization import Appel, Lot, Offre, Organisation
from tests.document_fixtures import (
    build_docx,
    build_pdf,
    build_pdf_sans_texte,
    build_xlsx,
)

MISSION_TEXTE = "MISSION CARSO 2026"
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=2 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


@dataclass(frozen=True)
class Donnees:
    """Ancres métier disponibles pour les dépôts documentaires."""

    organisation: Organisation
    appel: Appel
    lot: Lot
    offre: Offre
    mission: Mission
    equipe: Equipe


@pytest.fixture
def donnees(session: Session) -> Donnees:
    organisation = Organisation(nom="Client Formation")
    session.add(organisation)
    session.flush()

    appel = Appel(
        organisation_id=organisation.id, reference="AO-2026-01", titre="Formation bureautique"
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
        reference="OF-2026-01",
        titre="Offre bureautique",
        statut=StatutOffre.BROUILLON.value,
    )
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-2026-01",
        titre="Mission bureautique",
        statut=StatutMission.PLANIFIE.value,
    )
    equipe = Equipe(nom="Haddad", prenom="Omar", email="ohaddad@example.com")
    session.add_all([offre, mission, equipe])
    session.flush()

    return Donnees(
        organisation=organisation,
        appel=appel,
        lot=lot,
        offre=offre,
        mission=mission,
        equipe=equipe,
    )


def _entree(
    *,
    nom: str = "Fiche technique.pdf",
    contenu: bytes | None = None,
    type_document: str = TypeDocument.FICHE_TECHNIQUE.value,
    created_by: str | None = "carso",
    proposed_by_agent: str | None = None,
    **ancres: UUID | None,
) -> DocumentUploadInput:
    return DocumentUploadInput(
        type_document=type_document,
        nom=nom,
        stream=io.BytesIO(contenu if contenu is not None else build_pdf(MISSION_TEXTE)),
        created_by=created_by,
        proposed_by_agent=proposed_by_agent,
        **ancres,
    )


def _evenements(session: Session, action: str) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def _chemin(storage: LocalDocumentStorage, document: Document) -> Path:
    return storage.root / document.storage_path


# --- Dépôt ----------------------------------------------------------------------


def test_depot_cree_un_brouillon_trace(
    service: DocumentService,
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    assert document.statut == StatutDocument.DRAFT.value
    assert document.version == 1
    assert document.storage_path == f"missions/{donnees.mission.id}/Fiche technique.pdf"
    assert document.mime_type == "application/pdf"
    assert document.storage_disk == "local"
    assert document.taille_octets is not None and document.taille_octets > 0
    assert _chemin(storage, document).is_file()

    evenements = _evenements(session, ActionAudit.DOCUMENT_ENREGISTRE.value)
    assert len(evenements) == 1
    assert evenements[0].actor_type == ActorType.HUMAIN.value
    assert evenements[0].entity_id == str(document.id)


def test_depot_par_agent_place_en_zone_proposition(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    """Règle 6 [C] : un dépôt d'agent n'est jamais officiel."""
    document = service.enregistrer_document(
        _entree(
            mission_id=donnees.mission.id,
            proposed_by_agent="AgentAssistantMission",
            created_by=None,
        )
    )

    assert document.statut == StatutDocument.PROPOSED.value
    assert document.doc_metadata == {"proposed_by_agent": "AgentAssistantMission"}
    evenement = _evenements(session, ActionAudit.DOCUMENT_ENREGISTRE.value)[0]
    assert evenement.actor_type == ActorType.AGENT.value


# --- Règle CARSO 26/10 : CV et documents d'appel sans cycle de décision ---------


def test_cv_depose_par_humain_nait_importe(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    """Un CV ne porte pas de cycle de décision : déposé par un humain, il
    naît « importé » — puis archivé ou supprimé selon son cycle de vie."""
    document = service.enregistrer_document(
        _entree(
            type_document=TypeDocument.CV.value,
            nom="CV Mino Rasoa.pdf",
            equipe_id=donnees.equipe.id,
        )
    )

    assert document.statut == StatutDocument.IMPORTE.value


def test_document_d_appel_depose_par_humain_nait_importe(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    """Même règle pour les pièces de l'appel : « importé » au dépôt humain,
    jamais « draft » — le document source d'un appel ne se soumet pas."""
    document = service.enregistrer_document(
        _entree(
            type_document=TypeDocument.APPEL_PROPOSITION.value,
            nom="Appel source.pdf",
            appel_id=donnees.appel.id,
        )
    )

    assert document.statut == StatutDocument.IMPORTE.value


def test_cv_depose_par_agent_reste_une_proposition(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    """La règle ne contourne pas le HITL : le dépôt d'un agent, même sur un
    CV, reste une proposition en attente de décision humaine (règle 6 [C])."""
    document = service.enregistrer_document(
        _entree(
            type_document=TypeDocument.CV.value,
            nom="CV proposé.pdf",
            equipe_id=donnees.equipe.id,
            proposed_by_agent="AgentRH",
            created_by=None,
        )
    )

    assert document.statut == StatutDocument.PROPOSED.value


def test_ancre_multiple_refusee(service: DocumentService) -> None:
    with pytest.raises(ValidationError):
        service.enregistrer_document(
            _entree(mission_id=uuid4(), appel_id=uuid4())
        )


def test_ancre_absente_range_sous_generated(
    service: DocumentService, storage: LocalDocumentStorage
) -> None:
    document = service.enregistrer_document(_entree())

    assert document.type_document == TypeDocument.NON_CLASSE.value
    assert document.mission_id is None
    assert document.storage_path == f"generated/{document.id}/Fiche technique.pdf"
    assert _chemin(storage, document).is_file()


def test_ancre_inexistante_range_sous_generated(
    service: DocumentService, storage: LocalDocumentStorage
) -> None:
    document = service.enregistrer_document(_entree(mission_id=uuid4()))

    assert document.type_document == TypeDocument.NON_CLASSE.value
    assert document.mission_id is None
    assert document.storage_path.startswith(f"generated/{document.id}/")


def test_type_de_document_inconnu_refuse(
    service: DocumentService, donnees: Donnees
) -> None:
    with pytest.raises(ValidationError):
        service.enregistrer_document(
            _entree(mission_id=donnees.mission.id, type_document="type_invente")
        )


def test_nom_traversant_refuse_et_rien_ecrit_hors_racine(
    service: DocumentService, storage: LocalDocumentStorage, tmp_path: Path, donnees: Donnees
) -> None:
    with pytest.raises(ValidationError):
        service.enregistrer_document(
            _entree(nom="../../evil.pdf", mission_id=donnees.mission.id)
        )

    assert list(tmp_path.glob("*.pdf")) == []
    assert not (tmp_path.parent / "evil.pdf").exists()
    # Même une lecture directe du storage refuse le chemin traversant.
    with pytest.raises(ValidationError):
        storage.exists("missions/../../evil.pdf")


def test_doublon_de_version_1_refuse(service: DocumentService, donnees: Donnees) -> None:
    service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    with pytest.raises(ConflictError):
        service.enregistrer_document(_entree(mission_id=donnees.mission.id))


@pytest.mark.parametrize(
    ("champ", "scope"),
    [
        ("organisation_id", "organisations"),
        ("appel_id", "appel_proposition"),
        ("offre_id", "offres"),
        ("mission_id", "missions"),
        ("equipe_id", "equipes"),
    ],
)
def test_chaque_ancre_determine_le_dossier_de_stockage(
    service: DocumentService, donnees: Donnees, champ: str, scope: str
) -> None:
    ancre = {
        "organisation_id": donnees.organisation.id,
        "appel_id": donnees.appel.id,
        "offre_id": donnees.offre.id,
        "mission_id": donnees.mission.id,
        "equipe_id": donnees.equipe.id,
    }[champ]

    document = service.enregistrer_document(
        _entree(nom=f"{champ}.pdf", **{champ: ancre})
    )

    assert document.storage_path.startswith(f"{scope}/")


# --- Décisions humaines ---------------------------------------------------------


def test_cycle_humain_brouillon_soumission_approbation(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    # Le chemin direct draft → approved est interdit par la machine à états.
    with pytest.raises(ConflictError):
        service.approuver_document(document.id, DecisionInput(decided_by="resp"))

    service.soumettre_document(document.id)
    approuve = service.approuver_document(
        document.id, DecisionInput(decided_by="resp", reason="Document conforme")
    )

    assert approuve.statut == StatutDocument.APPROVED.value
    approbations = list(session.scalars(select(Approbation)))
    assert len(approbations) == 1
    assert approbations[0].decision == DecisionApprobation.APPROUVE.value
    assert approbations[0].proposal_type == TypeProposition.DOCUMENT_OFFICIEL.value
    assert approbations[0].decided_by == "resp"
    assert approbations[0].decision_reason == "Document conforme"
    assert len(_evenements(session, ActionAudit.DOCUMENT_SOUMIS.value)) == 1
    # Convention de traçabilité du projet : l'événement d'audit d'une décision
    # porte l'action ``proposal_type`` (le sens de la décision — approuvé ou
    # refusé — est porté par la ligne ``Approbation``).
    evenements_decision = _evenements(session, TypeProposition.DOCUMENT_OFFICIEL.value)
    assert len(evenements_decision) == 1
    assert evenements_decision[0].actor_id == "resp"


def test_refus_archive_la_proposition_sans_supprimer_le_fichier(
    service: DocumentService, session: Session, storage: LocalDocumentStorage, donnees: Donnees
) -> None:
    document = service.enregistrer_document(
        _entree(mission_id=donnees.mission.id, proposed_by_agent="AgentAssistantMission")
    )

    refuse = service.refuser_document(
        document.id, DecisionInput(decided_by="resp", reason="Version non conforme")
    )

    assert refuse.statut == StatutDocument.ARCHIVED.value
    assert _chemin(storage, document).is_file()
    approbations = list(session.scalars(select(Approbation)))
    assert approbations[0].decision == DecisionApprobation.REJETE.value
    assert approbations[0].proposed_by_agent == "AgentAssistantMission"


def test_archivage_trace_sans_suppression(
    service: DocumentService,
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    archive = service.archiver_document(
        document.id, DecisionInput(decided_by="resp", reason="Obsolète")
    )

    assert archive.statut == StatutDocument.ARCHIVED.value
    assert _chemin(storage, document).is_file()
    evenements = _evenements(session, ActionAudit.DOCUMENT_ARCHIVE.value)
    assert len(evenements) == 1
    assert evenements[0].event_metadata == {"decided_by": "resp", "reason": "Obsolète"}


# --- Versioning -----------------------------------------------------------------


def test_remplacement_cree_une_version_sans_ecraser_la_precedente(
    service: DocumentService,
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    v1 = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    contenu_v1 = _chemin(storage, v1).read_bytes()

    v2 = service.remplacer_document(v1.id, _entree(contenu=build_pdf("VERSION 2")))

    assert v2.version == 2
    assert v2.statut == StatutDocument.DRAFT.value
    assert v2.nom == v1.nom
    assert v2.storage_path.endswith("Fiche technique_v2.pdf")
    assert v1.storage_path.endswith("Fiche technique.pdf")

    # Le fichier officiel est intact : le remplacement n'écrase rien.
    assert _chemin(storage, v1).read_bytes() == contenu_v1
    assert _chemin(storage, v2).read_bytes() == build_pdf("VERSION 2")

    versions = service.versions(v1.id)
    assert [version.version for version in versions] == [2, 1]
    evenements = _evenements(session, ActionAudit.DOCUMENT_REMPLACE.value)
    assert evenements[0].event_metadata == {
        "document_remplace_id": str(v1.id),
        "version_remplacee": 1,
        "storage_path": v2.storage_path,
    }


def test_promotion_d_une_version_archive_la_precedente(
    service: DocumentService, session: Session, storage: LocalDocumentStorage, donnees: Donnees
) -> None:
    v1 = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.soumettre_document(v1.id)
    service.approuver_document(v1.id, DecisionInput(decided_by="resp"))

    v2 = service.remplacer_document(
        v1.id,
        _entree(
            contenu=build_pdf("VERSION 2"),
            proposed_by_agent="AgentGenerateurOffre",
        ),
    )
    assert v2.statut == StatutDocument.PROPOSED.value

    # Tant que la décision n'est pas prise, la v1 reste officielle.
    assert service.obtenir(v1.id).statut == StatutDocument.APPROVED.value

    approuve = service.approuver_document(v2.id, DecisionInput(decided_by="resp"))

    assert approuve.statut == StatutDocument.APPROVED.value
    assert service.obtenir(v1.id).statut == StatutDocument.ARCHIVED.value
    assert _chemin(storage, v1).is_file()

    approbations = {
        approbation.entity_id: approbation
        for approbation in session.scalars(select(Approbation))
    }
    assert approbations[v1.id].decision == DecisionApprobation.APPROUVE.value
    assert approbations[v2.id].proposed_by_agent == "AgentGenerateurOffre"


def test_remplacement_ne_permet_pas_de_renommer(
    service: DocumentService, donnees: Donnees
) -> None:
    v1 = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    with pytest.raises(ValidationError):
        service.remplacer_document(v1.id, _entree(nom="Autre nom.pdf"))


def test_remplacement_d_un_document_inexistant(service: DocumentService) -> None:
    with pytest.raises(NotFoundError):
        service.remplacer_document(uuid4(), _entree())


# --- Extraction -----------------------------------------------------------------


def test_extraction_pdf(
    service: DocumentService, donnees: Donnees
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    extrait = service.extraire_texte(document.id)

    assert MISSION_TEXTE in extrait.texte
    assert extrait.adaptateur == "pypdf"
    assert extrait.nb_pages == 1
    assert extrait.extension == "pdf"
    assert extrait.tronque is False


def test_extraction_docx_inclut_les_tableaux(
    service: DocumentService, donnees: Donnees
) -> None:
    contenu = build_docx(
        paragraphes=["Fiche technique CARSO"],
        tableau=[["Objectif", "Former 20 agents"]],
    )
    document = service.enregistrer_document(
        _entree(
            nom="Fiche.docx",
            contenu=contenu,
            type_document=TypeDocument.FICHE_TECHNIQUE.value,
            mission_id=donnees.mission.id,
        )
    )

    extrait = service.extraire_texte(document.id)

    assert "Fiche technique CARSO" in extrait.texte
    assert "Objectif | Former 20 agents" in extrait.texte


def test_extraction_xlsx_expose_les_feuilles(
    service: DocumentService, donnees: Donnees
) -> None:
    contenu = build_xlsx(
        feuille="Beneficiaires",
        lignes=[["Nom", "Prenom"], ["Diallo", "Awa"]],
    )
    document = service.enregistrer_document(
        _entree(
            nom="Beneficiaires.xlsx",
            contenu=contenu,
            type_document=TypeDocument.LISTE_BENEFICIAIRES.value,
            mission_id=donnees.mission.id,
        )
    )

    extrait = service.extraire_texte(document.id)

    assert extrait.feuilles == ("Beneficiaires",)
    assert extrait.nb_feuilles == 1
    assert "Diallo\tAwa" in extrait.texte


def test_extraction_tronquee_respecte_le_plafond(
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le plafond de service borne la réponse, sans modifier le document."""
    monkeypatch.setattr(get_settings(), "extraction_max_chars", 5)
    service = DocumentService(session, storage=storage)
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    extrait = service.extraire_texte(document.id)

    assert extrait.tronque is True
    assert len(extrait.texte) <= 5


def test_extraction_pdf_sans_texte_explique_l_absence_d_ocr(
    service: DocumentService, donnees: Donnees
) -> None:
    document = service.enregistrer_document(
        _entree(contenu=build_pdf_sans_texte(), mission_id=donnees.mission.id)
    )

    with pytest.raises(ValidationError) as erreur:
        service.extraire_texte(document.id)

    assert "OCR" in str(erreur.value)


def test_extraction_type_sans_extracteur_explique(
    service: DocumentService, donnees: Donnees
) -> None:
    """Le .doc est accepté au dépôt mais aucun extracteur n'existe pour ce format."""
    document = service.enregistrer_document(
        _entree(
            nom="Ancien format.doc",
            contenu=OLE_SIGNATURE + b"\x00" * 32,
            type_document=TypeDocument.FICHE_TECHNIQUE.value,
            mission_id=donnees.mission.id,
        )
    )

    with pytest.raises(ValidationError) as erreur:
        service.extraire_texte(document.id)

    assert "extensions_supportees" in erreur.value.details


def test_extraction_est_en_lecture_seule(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    evenements_avant = session.scalar(select(func.count()).select_from(AuditEvent))
    documents_avant = session.scalar(select(func.count()).select_from(Document))

    service.extraire_texte(document.id)
    service.extraire_texte(document.id)

    assert session.scalar(select(func.count()).select_from(AuditEvent)) == evenements_avant
    assert session.scalar(select(func.count()).select_from(Document)) == documents_avant


# --- Listing et téléchargement --------------------------------------------------


def test_listing_par_objet_metier(
    service: DocumentService, donnees: Donnees
) -> None:
    service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.enregistrer_document(
        _entree(
            nom="Appel.pdf",
            type_document=TypeDocument.APPEL_PROPOSITION.value,
            appel_id=donnees.appel.id,
        )
    )

    documents_mission = service.lister_pour("mission_id", donnees.mission.id)

    assert len(documents_mission) == 1
    assert documents_mission[0].mission_id == donnees.mission.id

    with pytest.raises(ValidationError):
        service.lister_pour("champ_inconnu", donnees.mission.id)


def test_telechargement_renvoie_le_nom_de_version(
    service: DocumentService, donnees: Donnees
) -> None:
    v1 = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    v2 = service.remplacer_document(v1.id, _entree(contenu=build_pdf("VERSION 2")))

    nom_fichier, chemin = service.telecharger(v2.id)

    assert nom_fichier == "Fiche technique_v2.pdf"
    assert chemin.read_bytes() == build_pdf("VERSION 2")


# --- Registre des modèles -------------------------------------------------------


def _modele_service(session: Session) -> ModeleDocumentService:
    return ModeleDocumentService(session, actor_id="test")


def test_modele_enregistre_versionne_et_resolu(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    gabarit = service.enregistrer_document(
        _entree(
            nom="Modele client.docx",
            contenu=build_docx(paragraphes=["Modèle CARSO"]),
            type_document=TypeDocument.TEMPLATE.value,
            organisation_id=donnees.organisation.id,
        )
    )

    modeles = _modele_service(session)
    v1 = modeles.enregistrer_modele(
        donnees.organisation.id,
        nom="Modèle offre client",
        type_document=TypeDocument.OFFRE.value,
        document_template_id=gabarit.id,
        registered_by="carso",
    )
    v2 = modeles.enregistrer_modele(
        donnees.organisation.id,
        nom="Modèle offre client 2027",
        type_document=TypeDocument.OFFRE.value,
        document_template_id=gabarit.id,
    )

    assert (v1.version, v2.version) == (1, 2)
    actif = modeles.resoudre_modele_actif(
        donnees.organisation.id, TypeDocument.OFFRE.value
    )
    assert actif.id == v2.id
    assert len(modeles.lister(donnees.organisation.id)) == 2
    assert len(_evenements(session, ActionAudit.MODELE_DOCUMENT_ENREGISTRE.value)) == 2


def test_modele_absent_echoue_explicitement(session: Session, donnees: Donnees) -> None:
    """Aucun modèle par défaut n'est inventé : l'absence est une erreur explicite."""
    with pytest.raises(NotFoundError) as erreur:
        _modele_service(session).resoudre_modele_actif(
            donnees.organisation.id, TypeDocument.OFFRE.value
        )

    assert "aucun modèle par défaut" in str(erreur.value)


def test_modele_exige_un_document_de_type_template(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    non_gabarit = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    with pytest.raises(ValidationError):
        _modele_service(session).enregistrer_modele(
            donnees.organisation.id,
            nom="Modèle invalide",
            type_document=TypeDocument.OFFRE.value,
            document_template_id=non_gabarit.id,
        )


# --- Suppression logique (règle validée 23/09) -----------------------------------


def test_suppression_logique_retire_le_fichier_garde_la_fiche(
    service: DocumentService,
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    """Le fichier quitte le disque, la fiche reste en « Supprimé » avec son motif."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    chemin = _chemin(storage, document)
    assert chemin.is_file()

    retires = service.supprimer_document(
        document.id, DecisionInput(decided_by="carso", reason="Doublon")
    )

    assert retires == 1
    assert document.statut == StatutDocument.SUPPRIME.value
    assert not chemin.exists()
    # La référence reste écrite : elle dit d'où le fichier a été retiré.
    assert document.storage_path.endswith("Fiche technique.pdf")
    assert document.doc_metadata["suppression"]["motif"] == "Doublon"
    assert document.doc_metadata["suppression"]["statut_avant"] == StatutDocument.DRAFT.value

    evenements = _evenements(session, ActionAudit.DOCUMENT_SUPPRIME.value)
    assert len(evenements) == 1
    assert evenements[0].entity_id == str(document.id)
    assert evenements[0].event_metadata["suppression_logique"] is True
    assert evenements[0].after["fichiers_retires"] == 1


def test_suppression_logique_retire_aussi_les_apercus(
    service: DocumentService,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    """Un aperçu orphelin serait un fichier fantôme : il part avec le document."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    # Le nommage des aperçus vient de sa source de vérité, jamais recomposé ici.
    apercu = storage.root / chemin_apercu(document.storage_path, 1)
    apercu.parent.mkdir(parents=True, exist_ok=True)
    apercu.write_bytes(b"\x89PNG\r\n\x1a\n")

    retires = service.supprimer_document(
        document.id, DecisionInput(decided_by="carso")
    )

    assert retires == 2
    assert not apercu.exists()


def test_suppression_logique_ne_touche_pas_les_versions(
    service: DocumentService, storage: LocalDocumentStorage, donnees: Donnees
) -> None:
    v1 = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    v2 = service.remplacer_document(v1.id, _entree(contenu=build_pdf("VERSION 2")))

    service.supprimer_document(v2.id, DecisionInput(decided_by="carso"))

    assert v2.statut == StatutDocument.SUPPRIME.value
    assert v1.statut == StatutDocument.DRAFT.value
    assert _chemin(storage, v1).is_file()
    assert service.telecharger(v1.id)[1].read_bytes() == build_pdf(MISSION_TEXTE)


def test_document_supprime_refuse_les_mutations_et_la_lecture(
    service: DocumentService, donnees: Donnees
) -> None:
    """Une fiche supprimée est un cul-de-sac : ni mutation, ni fichier à lire."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(document.id, DecisionInput(decided_by="carso"))

    # Fichier retiré : la lecture échoue explicitement.
    with pytest.raises(NotFoundError, match="retiré du stockage"):
        service.telecharger(document.id)
    with pytest.raises(NotFoundError, match="retiré du stockage"):
        service.extraire_texte(document.id)

    # La fiche ne se remet pas en circulation toute seule.
    with pytest.raises(ConflictError, match="supprimé"):
        service.soumettre_document(document.id)
    with pytest.raises(ConflictError, match="supprimé"):
        service.archiver_document(document.id, DecisionInput(decided_by="carso"))
    with pytest.raises(ConflictError, match="supprimé"):
        service.supprimer_document(document.id, DecisionInput(decided_by="carso"))


def test_document_supprime_ne_bloque_pas_le_redepot_du_meme_nom(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    """Redéposer un fichier du nom d'un document supprimé crée une nouvelle v1."""
    premier = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(premier.id, DecisionInput(decided_by="carso"))

    second = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    assert second.version == 1
    assert second.statut == StatutDocument.DRAFT.value
    assert second.id != premier.id
    # Les listes métier ne rendent que le document exploitable.
    actifs = service.lister_pour("mission_id", donnees.mission.id)
    assert [document.id for document in actifs] == [second.id]
    # L'historique complet, lui, garde les deux fiches.
    assert len(service.versions(second.id)) == 2


# --- Corbeille : échéance, restauration, purge (ADR 0006) ------------------------


def test_corbeille_expose_echeance_et_fichier_absent(
    service: DocumentService, donnees: Donnees
) -> None:
    """La corbeille dit la date, l'échéance, et ce qu'il manque pour restaurer."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(
        document.id, DecisionInput(decided_by="carso", reason="Doublon")
    )

    (fiche,) = service.lister_corbeille()

    assert fiche.document_id == document.id
    assert fiche.nom == "Fiche technique.pdf"
    assert fiche.statut_avant == StatutDocument.DRAFT.value
    assert fiche.supprime_par == "carso"
    assert fiche.motif == "Doublon"
    assert fiche.supprime_le is not None and fiche.expire_le is not None
    # L'échéance vient du tombstone : elle ne bouge pas avec le réglage courant.
    assert (fiche.expire_le - fiche.supprime_le).days == 30
    assert fiche.retention_jours == 30
    assert fiche.expiree is False
    assert fiche.jours_restants == 30
    # Le fichier a quitté le disque : restaurer exigera de le redéposer.
    assert fiche.fichier_present is False
    assert fiche.restaurable is True
    assert fiche.blocage is None

    # Montrer la corbeille ne modifie rien.
    assert document.statut == StatutDocument.SUPPRIME.value
    assert service.lister_corbeille()[0].document_id == document.id


def test_restauration_redepose_le_fichier_et_retablit_le_statut(
    service: DocumentService,
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    """Restaurer remet l'état d'avant suppression ; le fichier se redépose."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.soumettre_document(document.id)
    service.supprimer_document(document.id, DecisionInput(decided_by="carso"))
    assert not _chemin(storage, document).is_file()

    restaure = service.restaurer_document(
        document.id,
        DecisionInput(decided_by="carso", reason="Suppression par erreur"),
        flux=io.BytesIO(build_pdf("FICHIER REDEPOSE")),
    )

    assert restaure.statut == StatutDocument.PROPOSED.value
    assert _chemin(storage, restaure).is_file()
    assert restaure.taille_octets is not None and restaure.taille_octets > 0
    journal = restaure.doc_metadata["restaurations"]
    assert len(journal) == 1
    assert journal[0]["par"] == "carso"
    assert journal[0]["statut_retabli"] == StatutDocument.PROPOSED.value
    assert journal[0]["fichier_redepose"] is True

    evenement = _evenements(session, ActionAudit.DOCUMENT_RESTAURE.value)[0]
    assert evenement.entity_id == str(document.id)
    assert evenement.before == {"statut": StatutDocument.SUPPRIME.value}
    assert evenement.after["statut"] == StatutDocument.PROPOSED.value

    # La fiche est de nouveau exploitable : elle quitte la corbeille.
    assert service.lister_corbeille() == []
    assert service.extraire_texte(document.id).texte


def test_restauration_refusee_sans_fichier(
    service: DocumentService, donnees: Donnees
) -> None:
    """Sans fichier redéposé ni fichier revenu sur le disque, pas de restauration."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(document.id, DecisionInput(decided_by="carso"))

    with pytest.raises(ConflictError, match="redéposez-le"):
        service.restaurer_document(document.id, DecisionInput(decided_by="carso"))

    assert document.statut == StatutDocument.SUPPRIME.value


def test_restauration_sans_flux_si_le_fichier_est_revenu(
    service: DocumentService, storage: LocalDocumentStorage, donnees: Donnees
) -> None:
    """Un fichier remis à sa place (sauvegarde exploitant) suffit à restaurer."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(document.id, DecisionInput(decided_by="carso"))
    chemin = _chemin(storage, document)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(build_pdf("RETOUR DE SAUVEGARDE"))

    fiche = service.lister_corbeille()[0]
    assert fiche.fichier_present is True

    restaure = service.restaurer_document(document.id, DecisionInput(decided_by="carso"))

    assert restaure.statut == StatutDocument.DRAFT.value
    assert restaure.doc_metadata["restaurations"][0]["fichier_redepose"] is False


def test_restauration_refusee_si_la_version_est_reprise(
    service: DocumentService, donnees: Donnees
) -> None:
    """Redéposer le même nom crée une nouvelle v1 : restaurer créerait un doublon."""
    premier = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(premier.id, DecisionInput(decided_by="carso"))
    second = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    fiche = service.lister_corbeille()[0]
    assert fiche.restaurable is False
    assert "existe déjà" in (fiche.blocage or "")

    with pytest.raises(ConflictError, match="existe déjà"):
        service.restaurer_document(
            premier.id,
            DecisionInput(decided_by="carso"),
            flux=io.BytesIO(build_pdf("TENTATIVE")),
        )

    assert premier.statut == StatutDocument.SUPPRIME.value
    assert second.statut == StatutDocument.DRAFT.value


def test_restauration_refusee_si_une_version_officielle_est_en_place(
    service: DocumentService, donnees: Donnees
) -> None:
    """Restaurer un officiel devant un autre officiel créerait deux versions actives."""
    v1 = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    v2 = service.remplacer_document(v1.id, _entree(contenu=build_pdf("VERSION 2")))
    service.soumettre_document(v1.id)
    service.approuver_document(v1.id, DecisionInput(decided_by="carso"))
    service.supprimer_document(v1.id, DecisionInput(decided_by="carso"))
    # La v2 devient officielle alors que la v1 supprimée l'était : restaurer la
    # v1 donnerait deux versions officielles du même document.
    service.soumettre_document(v2.id)
    service.approuver_document(v2.id, DecisionInput(decided_by="carso"))

    fiche = service.lister_corbeille()[0]
    assert fiche.statut_avant == StatutDocument.APPROVED.value
    assert fiche.restaurable is False
    assert "version officielle" in (fiche.blocage or "")

    with pytest.raises(ConflictError, match="version officielle"):
        service.restaurer_document(
            v1.id,
            DecisionInput(decided_by="carso"),
            flux=io.BytesIO(build_pdf("TENTATIVE")),
        )


def test_restauration_refusee_si_le_document_n_est_pas_supprime(
    service: DocumentService, donnees: Donnees
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    with pytest.raises(ConflictError, match="pas supprimé"):
        service.restaurer_document(document.id, DecisionInput(decided_by="carso"))


def test_purge_efface_la_fiche_et_retire_le_fichier_revenu(
    service: DocumentService,
    session: Session,
    storage: LocalDocumentStorage,
    donnees: Donnees,
) -> None:
    """La purge est la seule opération qui efface une ligne documentaire."""
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))
    service.supprimer_document(document.id, DecisionInput(decided_by="carso"))
    # Un fichier revenu sur le disque entre-temps part avec la fiche.
    chemin = _chemin(storage, document)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(build_pdf("ORPHELIN"))

    retires = service.purger_document(
        document.id, DecisionInput(decided_by="admin", reason="Fin de conservation")
    )

    assert retires == 1
    assert not chemin.exists()
    session.flush()
    assert session.scalars(select(Document).where(Document.id == document.id)).first() is None
    assert service.lister_corbeille() == []
    evenement = _evenements(session, ActionAudit.DOCUMENT_PURGE.value)[0]
    assert evenement.event_metadata["purge_definitive"] is True
    assert evenement.event_metadata["reason"] == "Fin de conservation"
    # La trace garde ce que la purge a effacé.
    assert evenement.before["nom"] == "Fiche technique.pdf"
    assert evenement.before["version"] == 1


def test_purge_refusee_si_le_document_n_est_pas_supprime(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    document = service.enregistrer_document(_entree(mission_id=donnees.mission.id))

    with pytest.raises(ConflictError, match="supprimée"):
        service.purger_document(document.id, DecisionInput(decided_by="admin"))

    assert session.get(Document, document.id) is not None


def test_purge_refusee_si_un_modele_de_document_utilise_le_gabarit(
    service: DocumentService, session: Session, donnees: Donnees
) -> None:
    """Un dépendant n'est jamais détruit par la purge : elle s'arrête sur lui."""
    gabarit = service.enregistrer_document(
        _entree(
            nom="Modele client.docx",
            contenu=build_docx(paragraphes=["Modèle CARSO"]),
            type_document=TypeDocument.TEMPLATE.value,
            organisation_id=donnees.organisation.id,
        )
    )
    _modele_service(session).enregistrer_modele(
        donnees.organisation.id,
        nom="Modèle offre client",
        type_document=TypeDocument.OFFRE.value,
        document_template_id=gabarit.id,
        registered_by="carso",
    )
    service.supprimer_document(gabarit.id, DecisionInput(decided_by="carso"))

    with pytest.raises(ConflictError, match="encore référencé") as erreur:
        service.purger_document(gabarit.id, DecisionInput(decided_by="admin"))

    assert erreur.value.details["references"] == {"modeles": 1}
    assert session.get(Document, gabarit.id) is not None
    assert len(service.lister_corbeille()) == 1
