"""Tests — suppression réelle cascadée (``ServiceSuppression``).

On construit un vrai sous-arbre métier sur SQLite (organisation → appel → lot →
offre → mission → session → participation, plus équipe, affectations, supports,
budgets et documents avec fichiers réels), puis on vérifie :

- que ``impact`` dénombre **sans rien modifier** ;
- que ``supprimer`` emporte exactement le sous-arbre attendu, dans l'ordre des
  contraintes ``RESTRICT`` du schéma ;
- que les documents ne sont **jamais détruits** (règle validée 23/09) : la fiche
  survit, détachée de son ancre, avec le statut « Supprimé », et seul son fichier
  quitte le disque ;
- qu'un document officiel **avertit** sans bloquer la suppression ;
- qu'un modèle de document **survit** à la suppression de son template ;
- qu'un seul événement d'audit porte l'inventaire complet.
"""

from __future__ import annotations

import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.services.suppression_service import (
    TYPES_SUPPRIMABLES,
    ServiceSuppression,
)
from app.application.trace import Decision
from app.core.errors import NotFoundError, ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.document import AuditEvent, Document
from app.domain.enums import StatutDocument, StatutMission
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    Equipe,
    Mission,
    MissionOffre,
    ModeleDocument,
    Participation,
    Presence,
    SessionFormation,
    SupportFormation,
)
from app.domain.organization import (
    AppelAProposition,
    Budget,
    LigneBudget,
    Lot,
    Offre,
    Organisation,
)
from app.infrastructure.repositories.document import ModeleDocumentRepository

_DECISION = Decision(decided_by="admin@carso.test", reason="doublon de test")


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    """Stockage réel sur disque temporaire (les fichiers sont vraiment écrits)."""
    return LocalDocumentStorage(tmp_path, max_bytes=1_000_000)


@pytest.fixture
def service(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> ServiceSuppression:
    """Service branché sur la session SQLite de test et un stockage temporaire."""
    return ServiceSuppression(api_session_factory(), storage=storage)


DECISION = _DECISION


# --- Graine : un sous-arbre complet ---------------------------------------------


class SousArbre:
    """Identifiants du graphe créé, pour les assertions et les cascades."""

    def __init__(self) -> None:
        self.organisation: UUID
        self.appel: UUID
        self.lot: UUID
        self.offre: UUID
        self.mission: UUID
        self.session: UUID
        self.participation: UUID
        self.beneficiaire: UUID
        self.equipe: UUID
        self.affectation: UUID
        self.support: UUID
        self.budget: UUID
        self.ligne: UUID
        self.document: UUID
        self.document_officiel: UUID
        self.chemin_document: str
        self.chemin_officiel: str


def _graine(
    factory: sessionmaker[Session],
    storage: LocalDocumentStorage,
    *,
    document_officiel: bool = False,
) -> SousArbre:
    """Crée le sous-arbre complet et renvoie ses identifiants."""
    ids = SousArbre()
    session = factory()
    try:
        organisation = Organisation(nom="Client Cascade")
        session.add(organisation)
        session.flush()

        appel = AppelAProposition(
            organisation_id=organisation.id, reference="AO-CASC-01", titre="Appel"
        )
        session.add(appel)
        session.flush()  # l'id du parent doit exister avant de construire l'enfant
        lot = Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot 1")
        session.add(lot)
        session.flush()

        offre = Offre(
            organisation_id=organisation.id,
            appel_a_proposition_id=appel.id,
            lot_id=lot.id,
            type="offre_technique",
            reference="O-CASC-01",
            titre="Offre technique",
        )
        session.add(offre)
        session.flush()

        mission = Mission(
            organisation_id=organisation.id,
            reference="M-CASC-01",
            titre="Mission cascade",
            statut=StatutMission.PLANIFIEE.value,
        )
        session.add(mission)
        session.flush()
        session.add(MissionOffre(mission_id=mission.id, offre_id=offre.id))

        formation = SessionFormation(
            mission_id=mission.id, theme="Session cascade", statut="planifiee"
        )
        session.add(formation)
        session.flush()

        beneficiaire = Beneficiaire(nom="Benef", prenom="Aria")
        session.add(beneficiaire)
        session.flush()
        participation = Participation(
            session_id=formation.id,
            beneficiaire_id=beneficiaire.id,
        )
        session.add(participation)
        session.flush()
        # Le pointage est daté (règle 24/09) : il vit dans sa propre table.
        session.add(
            Presence(
                participation_id=participation.id,
                date=date(2026, 9, 1),
                presence="present",
            )
        )

        equipe = Equipe(nom="Haddad", prenom="Omar", email=f"{uuid4()}@example.com")
        session.add(equipe)
        session.flush()
        affectation = AffectationEquipe(
            mission_id=mission.id,
            equipe_id=equipe.id,
            role_dans_mission="formateur",
            statut="proposee",
        )
        session.add_all([participation, affectation])

        budget = Budget(offre_id=offre.id, devise="EUR")
        session.add(budget)
        session.flush()
        ligne = LigneBudget(
            budget_id=budget.id,
            categorie="honoraires",
            description="Formation",
            quantite=10,
            cout_unitaire=150,
            cout_total=1500,
        )
        session.add(ligne)

        # Documents : un brouillon rattaché à la mission, un officiel (option).
        chemin = f"missions/{mission.id}/fiche_technique.pdf"
        storage.save(chemin, BytesIO(b"%PDF-1.4 contenu de test"))
        document = Document(
            nom="fiche_technique.pdf",
            type_document="fiche_technique",
            storage_path=chemin,
            mission_id=mission.id,
            statut=StatutDocument.PROPOSED.value,
        )
        session.add(document)
        session.flush()
        # Support de formation : mission ↔ équipe ↔ document (FK RESTRICT).
        support = SupportFormation(
            mission_id=mission.id, equipe_id=equipe.id, document_id=document.id
        )
        session.add(support)

        if document_officiel:
            chemin_officiel = f"missions/{mission.id}/rapport_v1.pdf"
            storage.save(chemin_officiel, BytesIO(b"%PDF-1.4 rapport officiel"))
            officiel = Document(
                nom="rapport.pdf",
                type_document="rapport",
                storage_path=chemin_officiel,
                mission_id=mission.id,
                statut=StatutDocument.APPROVED.value,
            )
            session.add(officiel)
            session.flush()
            ids.document_officiel = officiel.id
            ids.chemin_officiel = chemin_officiel
        else:
            ids.document_officiel = uuid4()
            ids.chemin_officiel = ""

        session.commit()

        ids.organisation = organisation.id
        ids.appel = appel.id
        ids.lot = lot.id
        ids.offre = offre.id
        ids.mission = mission.id
        ids.session = formation.id
        ids.participation = participation.id
        ids.beneficiaire = beneficiaire.id
        ids.equipe = equipe.id
        ids.affectation = affectation.id
        ids.support = support.id
        ids.budget = budget.id
        ids.ligne = ligne.id
        ids.document = document.id
        ids.chemin_document = chemin
    finally:
        session.close()
    return ids


def _zip(entrees: dict[str, bytes]) -> bytes:
    """Archive ZIP réelle — la validation de contenu refuse un faux DOCX."""
    tampon = BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for nom, contenu in entrees.items():
            archive.writestr(nom, contenu)
    return tampon.getvalue()


def _compter(session_factory: sessionmaker[Session], modele: type) -> int:
    session = session_factory()
    try:
        return int(session.scalar(select(func.count()).select_from(modele)) or 0)
    finally:
        session.close()


# --- Impact ---------------------------------------------------------------------


def test_impact_compte_sans_rien_modifier(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    service = ServiceSuppression(api_session_factory(), storage=storage)

    impact = service.impact("organisations", ids.organisation)

    assert impact.bloquant is False
    assert impact.reference == "Client Cascade"
    assert impact.dependances["appels"] == 1
    assert impact.dependances["lots"] == 1
    assert impact.dependances["offres"] == 1
    assert impact.dependances["missions"] == 1
    assert impact.dependances["sessions"] == 1
    assert impact.dependances["participations"] == 1
    assert impact.dependances["lignes_budget"] == 1
    # Les documents ne sont pas détruits : ils sont annoncés à part.
    assert "documents" not in impact.dependances
    assert impact.conservees["documents"] == 1
    assert impact.message is not None
    assert "Supprimé" in impact.message
    # ``impact`` est une lecture : rien n'a bougé.
    assert _compter(api_session_factory, Organisation) == 1
    assert _compter(api_session_factory, Document) == 1
    assert storage.exists(ids.chemin_document)


def test_impact_type_inconnu_refuse(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    service = ServiceSuppression(api_session_factory(), storage=storage)
    with pytest.raises(ValidationError) as erreur:
        service.impact("utilisateurs", uuid4())
    assert set(erreur.value.details["types_supprimables"]) == set(TYPES_SUPPRIMABLES)


def test_impact_entite_absente_404(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    service = ServiceSuppression(api_session_factory(), storage=storage)
    with pytest.raises(NotFoundError):
        service.impact("missions", uuid4())


# --- Cascade --------------------------------------------------------------------


def test_suppression_organisation_emporte_tout_le_sous_arbre(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    session = api_session_factory()
    service = ServiceSuppression(session, storage=storage)

    rapport = service.supprimer("organisations", ids.organisation, DECISION)
    session.commit()

    assert rapport.reference == "Client Cascade"
    assert rapport.fichiers_supprimes == 1
    for modele in (
        Organisation,
        AppelAProposition,
        Lot,
        Offre,
        Mission,
        MissionOffre,
        SessionFormation,
        Participation,
        AffectationEquipe,
        SupportFormation,
        Budget,
        LigneBudget,
    ):
        assert _compter(api_session_factory, modele) == 0, modele.__name__
    # Les personnes ne sont pas des dépendances de l'organisation : elles
    # survivent, seuls leurs liens (participation, affectation) disparaissent.
    assert _compter(api_session_factory, Beneficiaire) == 1
    assert _compter(api_session_factory, Equipe) == 1
    # Le document survit à son organisation : fiche conservée, ancre détachée,
    # fichier retiré du disque (règle validée 23/09).
    assert rapport.conservees["documents"] == 1
    assert _compter(api_session_factory, Document) == 1
    assert not storage.exists(ids.chemin_document)
    session = api_session_factory()
    try:
        conserve = session.get(Document, ids.document)
        assert conserve is not None
        assert conserve.statut == StatutDocument.SUPPRIME.value
        assert conserve.organisation_id is None
        assert conserve.mission_id is None
        assert conserve.doc_metadata["suppression"]["par"] == DECISION.decided_by
        assert conserve.doc_metadata["suppression"]["motif"] == DECISION.reason
        assert conserve.doc_metadata["suppression"]["fichiers_retires"] == 1
    finally:
        session.close()


def test_audit_conserve_l_inventaire(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    session = api_session_factory()
    ServiceSuppression(session, storage=storage, actor_id="admin-1").supprimer(
        "organisations", ids.organisation, DECISION
    )
    session.commit()

    session = api_session_factory()
    try:
        evenement = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "suppression.effectuee")
        )
    finally:
        session.close()

    assert evenement is not None
    assert evenement.entity_id == str(ids.organisation)
    assert evenement.actor_id == "admin-1"
    assert evenement.after["dependances"]["sessions"] == 1
    assert evenement.event_metadata["decided_by"] == DECISION.decided_by
    assert evenement.event_metadata["reason"] == DECISION.reason


def test_suppression_lot_emporte_offre_mission_et_session(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    session = api_session_factory()
    ServiceSuppression(session, storage=storage).supprimer("lots", ids.lot, DECISION)
    session.commit()

    assert _compter(api_session_factory, Lot) == 0
    assert _compter(api_session_factory, Offre) == 0
    assert _compter(api_session_factory, Mission) == 0
    assert _compter(api_session_factory, SessionFormation) == 0
    # L'appel et l'organisation survivent : ils ne dépendent pas du lot.
    assert _compter(api_session_factory, AppelAProposition) == 1
    assert _compter(api_session_factory, Organisation) == 1


def test_suppression_session_laisse_la_mission_intacte(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    session = api_session_factory()
    ServiceSuppression(session, storage=storage).supprimer("sessions", ids.session, DECISION)
    session.commit()

    assert _compter(api_session_factory, SessionFormation) == 0
    assert _compter(api_session_factory, Participation) == 0
    assert _compter(api_session_factory, Mission) == 1
    assert _compter(api_session_factory, Beneficiaire) == 1


def test_suppression_beneficiaire_emporte_ses_participations(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    session = api_session_factory()
    ServiceSuppression(session, storage=storage).supprimer(
        "beneficiaires", ids.beneficiaire, DECISION
    )
    session.commit()

    assert _compter(api_session_factory, Beneficiaire) == 0
    assert _compter(api_session_factory, Participation) == 0
    assert _compter(api_session_factory, SessionFormation) == 1


def test_suppression_equipe_emporte_affectations_et_supports(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage)
    session = api_session_factory()
    ServiceSuppression(session, storage=storage).supprimer("equipes", ids.equipe, DECISION)
    session.commit()

    assert _compter(api_session_factory, Equipe) == 0
    assert _compter(api_session_factory, AffectationEquipe) == 0
    assert _compter(api_session_factory, SupportFormation) == 0
    # Le document du support n'était pas ancré sur l'équipe : il reste.
    assert _compter(api_session_factory, Document) == 1
    assert _compter(api_session_factory, Mission) == 1


def test_suppression_document_conserve_la_fiche_et_retire_le_fichier(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    """Supprimer un document ne supprime rien d'autre que son fichier.

    La fiche reste (statut « Supprimé »), les versions sœurs restent intactes et
    téléchargeables : elles n'ont jamais été visées.
    """
    session = api_session_factory()
    chemin_v1 = "missions/00000000-0000-0000-0000-000000000001/rapport_v1.pdf"
    chemin_v2 = "missions/00000000-0000-0000-0000-000000000001/rapport_v2.pdf"
    storage.save(chemin_v1, BytesIO(b"%PDF-1.4 v1"))
    storage.save(chemin_v2, BytesIO(b"%PDF-1.4 v2"))
    document = Document(
        nom="rapport.pdf", type_document="rapport", storage_path=chemin_v1, version=1
    )
    version2 = Document(
        nom="rapport.pdf", type_document="rapport", storage_path=chemin_v2, version=2
    )
    autre = Document(nom="autre.pdf", type_document="fiche", storage_path=chemin_v2)
    session.add_all([document, version2, autre])
    session.commit()

    service = ServiceSuppression(session, storage=storage)
    rapport = service.supprimer("documents", document.id, DECISION)
    session.commit()

    assert rapport.dependances == {}
    assert rapport.conservees["documents"] == 1
    assert rapport.fichiers_supprimes == 1
    assert not storage.exists(chemin_v1)
    # Les autres versions et documents ne sont pas touchés.
    assert storage.exists(chemin_v2)
    assert _compter(api_session_factory, Document) == 3
    supprime = session.get(Document, document.id)
    assert supprime is not None
    assert supprime.statut == StatutDocument.SUPPRIME.value
    assert version2.statut != StatutDocument.SUPPRIME.value
    assert autre.statut != StatutDocument.SUPPRIME.value


# --- Garde-fou : document officiel ----------------------------------------------


def test_document_officiel_avertit_sans_bloquer(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    ids = _graine(api_session_factory, storage, document_officiel=True)
    service = ServiceSuppression(api_session_factory(), storage=storage)

    impact = service.impact("organisations", ids.organisation)
    assert impact.bloquant is False
    assert "rapport.pdf" in impact.documents_officiels
    assert impact.message is not None

    session = api_session_factory()
    rapport = ServiceSuppression(session, storage=storage).supprimer(
        "organisations", ids.organisation, DECISION
    )
    session.commit()
    session.close()

    assert rapport.dependances["organisations"] == 1
    assert _compter(api_session_factory, Organisation) == 0
    # Les deux documents (fiche technique et rapport officiel) sont conservés :
    # fiches en « Supprimé », fichiers retirés.
    assert _compter(api_session_factory, Document) == 2
    assert rapport.conservees["documents"] == 2
    assert not storage.exists(ids.chemin_officiel)
    assert not storage.exists(ids.chemin_document)


def test_modele_de_document_survit_a_la_suppression_de_son_template(
    api_session_factory: sessionmaker[Session], storage: LocalDocumentStorage
) -> None:
    """Un modèle est un **dépendant** du document : il est conservé (règle 23/09).

    La FK ``document_template_id`` est en ``RESTRICT`` : la ligne du document
    restant, la contrainte n'est jamais forcée. Le modèle n'est simplement plus
    résolu comme modèle actif, puisque son fichier n'existe plus.
    """
    session = api_session_factory()
    organisation = Organisation(nom="Client Modèles")
    session.add(organisation)
    session.flush()
    chemin = f"organisations/{organisation.id}/modele.docx"
    storage.save(chemin, BytesIO(_zip({"word/document.xml": b"<w:document/>"})))
    template = Document(
        nom="modele.docx",
        type_document="template",
        storage_path=chemin,
        organisation_id=organisation.id,
    )
    session.add(template)
    session.flush()
    modele = ModeleDocument(
        organisation_id=organisation.id,
        nom="modele.docx",
        type_document="fiche_technique",
        document_template_id=template.id,
        version=1,
    )
    session.add(modele)
    session.commit()

    service = ServiceSuppression(session, storage=storage)
    impact = service.impact("documents", template.id)
    assert impact.conservees["modeles"] == 1

    rapport = service.supprimer("documents", template.id, DECISION)
    session.commit()

    assert rapport.conservees == {"documents": 1, "modeles": 1}
    assert _compter(api_session_factory, ModeleDocument) == 1
    assert _compter(api_session_factory, Document) == 1
    assert _compter(api_session_factory, Organisation) == 1
    assert not storage.exists(chemin)
    # Le modèle actif n'est plus résolu : son template a perdu son fichier.
    assert (
        ModeleDocumentRepository(session).find_active(organisation.id, "fiche_technique")
        is None
    )
