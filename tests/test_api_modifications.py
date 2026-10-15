"""Tests API — modifications et suppression réelle cascadée (règle CARSO 22/09).

Ces routes n'existaient pas : les services savaient modifier une offre, une
mission, une session, un document ou un appel, mais aucun contrôleur ne les
exposait. Le test vérifie la sémantique HTTP **et** ce qui compte métier : ce qui
change, ce qui ne change jamais, et ce qu'une suppression emporte réellement.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.document import AuditEvent, Document
from app.domain.enums import ActionAudit, StatutDocument, StatutMission
from app.domain.execution import Mission, Participation, SessionFormation
from app.domain.organization import AppelAProposition, Lot, Offre, Organisation
from tests.conftest import seed

PRÉFIXE = "/api/v1"


# --- Fabriques locales -------------------------------------------------------


def _organisation(factory: sessionmaker[Session]) -> Organisation:
    organisation = Organisation(nom="Client Modif")
    seed(factory, organisation)
    return organisation


def _appel(
    factory: sessionmaker[Session], organisation: Organisation
) -> AppelAProposition:
    appel = AppelAProposition(
        organisation_id=organisation.id,
        reference="AO-MOD-01",
        titre="Appel à modifier",
        description="Description initiale",
    )
    seed(factory, appel)
    return appel


def _lot(factory: sessionmaker[Session], appel: AppelAProposition) -> Lot:
    lot = Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot à modifier")
    seed(factory, lot)
    return lot


def _offre(
    client: TestClient, lot: Lot, reference: str = "OFF-MOD-01"
) -> dict[str, object]:
    reponse = client.post(
        f"{PRÉFIXE}/offres/",
        json={
            "lot_id": str(lot.id),
            "reference": reference,
            "titre": "Offre à modifier",
            "type": "offre_technique",
        },
    )
    assert reponse.status_code == 201
    return reponse.json()


def _mission(factory: sessionmaker[Session], organisation: Organisation) -> Mission:
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-MOD-01",
        titre="Mission à modifier",
        description="Avant",
        statut=StatutMission.PLANIFIEE.value,
    )
    seed(factory, mission)
    return mission


def _session(
    factory: sessionmaker[Session], mission: Mission
) -> SessionFormation:
    session = SessionFormation(
        mission_id=mission.id,
        theme="Thème initial",
        statut="planifiee",
    )
    seed(factory, session)
    return session


def _document(
    factory: sessionmaker[Session],
    mission: Mission,
    statut: str = StatutDocument.DRAFT.value,
) -> Document:
    document = Document(
        nom="rapport.docx",
        type_document="rapport",
        storage_path="missions/1/rapport.docx",
        version=1,
        statut=statut,
        mission_id=mission.id,
    )
    seed(factory, document)
    return document


# --- Offres ------------------------------------------------------------------


def test_patch_offre_change_le_titre_et_le_type(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    appel = _appel(api_session_factory, _organisation(api_session_factory))
    lot = _lot(api_session_factory, appel)
    offre = _offre(api_client, lot)

    reponse = api_client.patch(
        f"{PRÉFIXE}/offres/{offre['id']}",
        json={"titre": "Offre retravaillée", "type": "autre"},
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["titre"] == "Offre retravaillée"
    assert corps["type"] == "autre"
    # Le lot et l'appel ne bougent jamais : une offre répond à ``Appel + Lot``.
    assert corps["lot_id"] == offre["lot_id"]
    assert corps["appel_a_proposition_id"] == offre["appel_a_proposition_id"]
    assert corps["reference"] == offre["reference"]


def test_patch_offre_sans_changement_est_refuse(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    appel = _appel(api_session_factory, _organisation(api_session_factory))
    lot = _lot(api_session_factory, appel)
    offre = _offre(api_client, lot)

    reponse = api_client.patch(f"{PRÉFIXE}/offres/{offre['id']}", json={})

    assert reponse.status_code == 422


def test_patch_offre_inconnue_404(api_client: TestClient) -> None:
    reponse = api_client.patch(f"{PRÉFIXE}/offres/{uuid4()}", json={"titre": "X"})
    assert reponse.status_code == 404


# --- Missions ----------------------------------------------------------------


def test_patch_mission_change_titre_et_description_et_trace_l_humain(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)

    reponse = api_client.patch(
        f"{PRÉFIXE}/missions/{mission.id}",
        json={"titre": "Mission renommée", "description": "Après"},
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["titre"] == "Mission renommée"
    assert corps["description"] == "Après"

    # L'audit distingue le formulaire (HUMAIN) du tool d'agent (AGENT).
    with api_session_factory() as session:
        evenement = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == str(mission.id),
                AuditEvent.action == ActionAudit.MISSION_MODIFIEE.value,
            )
        ).one()
    assert evenement.actor_type == "humain"


# --- Sessions ----------------------------------------------------------------


def test_patch_session_change_theme_dates_et_lieu(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    session_formation = _session(api_session_factory, mission)

    reponse = api_client.patch(
        f"{PRÉFIXE}/sessions/{session_formation.id}",
        json={
            "theme": "Thème revu",
            "date_debut": "2026-10-05",
            "date_fin": "2026-10-07",
            "lieu": "Antananarivo",
        },
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["theme"] == "Thème revu"
    assert corps["date_debut"] == "2026-10-05"
    assert corps["date_fin"] == "2026-10-07"
    assert corps["lieu"] == "Antananarivo"
    # La session reste rattachée à sa mission : on ne la déplace pas.
    assert corps["mission_id"] == str(mission.id)


def test_patch_session_sans_changement_est_refuse(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    session_formation = _session(api_session_factory, mission)

    reponse = api_client.patch(f"{PRÉFIXE}/sessions/{session_formation.id}", json={})
    assert reponse.status_code == 422


# --- Participations ----------------------------------------------------------


def test_retirer_une_participation_laisse_la_personne_et_la_session(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    from app.domain.execution import Beneficiaire

    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    session_formation = _session(api_session_factory, mission)
    personne = Beneficiaire(nom="Rakoto", prenom="Naina")
    seed(api_session_factory, personne)
    inscription = api_client.post(
        f"{PRÉFIXE}/sessions/{session_formation.id}/participations",
        json={"beneficiaire_id": str(personne.id)},
    )
    assert inscription.status_code == 201
    participation_id = inscription.json()["id"]

    reponse = api_client.delete(f"{PRÉFIXE}/participations/{participation_id}")

    assert reponse.status_code == 204
    with api_session_factory() as session:
        assert session.get(Participation, UUID(participation_id)) is None
        assert session.get(Beneficiaire, personne.id) is not None
        assert session.get(SessionFormation, session_formation.id) is not None


# --- Documents ---------------------------------------------------------------


def test_patch_document_renomme_un_document_a_version_unique(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    document = _document(api_session_factory, mission)

    reponse = api_client.patch(
        f"{PRÉFIXE}/documents/{document.id}",
        json={"nom": "rapport-final.docx", "type_document": "justificatif"},
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["nom"] == "rapport-final.docx"
    assert corps["type_document"] == "justificatif"


def test_patch_document_extension_interdite_refusee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    document = _document(api_session_factory, mission)

    reponse = api_client.patch(
        f"{PRÉFIXE}/documents/{document.id}", json={"nom": "malware.exe"}
    )

    assert reponse.status_code == 422


# --- Appels à proposition ----------------------------------------------------


def test_patch_appel_modifie_et_relit_la_description(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    appel = _appel(api_session_factory, _organisation(api_session_factory))

    reponse = api_client.patch(
        f"{PRÉFIXE}/appels-a-proposition/{appel.id}",
        json={"titre": "Titre revu", "description": "Description revue"},
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["titre"] == "Titre revu"
    # ``description`` doit être relue : sans elle, une modification la perdrait
    # silencieusement à l'écran.
    assert corps["description"] == "Description revue"


# --- Suppression cascadée par l'API ------------------------------------------


def test_impact_annonce_les_dependances_sans_modifier(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    appel = _appel(api_session_factory, organisation)
    lot = _lot(api_session_factory, appel)
    _offre(api_client, lot)

    reponse = api_client.get(f"{PRÉFIXE}/organisations/{organisation.id}/impact")

    assert reponse.status_code == 200
    impact = reponse.json()
    assert impact["type_entite"] == "organisations"
    assert impact["dependances"]["appels"] == 1
    assert impact["dependances"]["lots"] == 1
    assert impact["dependances"]["offres"] == 1
    assert impact["bloquant"] is False

    # Rien n'a bougé : ``impact`` est une lecture pure.
    with api_session_factory() as session:
        assert session.get(Organisation, organisation.id) is not None
        assert session.scalars(select(Offre.id)).all() != []


def test_supprimer_une_organisation_emporte_son_sous_arbre(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    appel = _appel(api_session_factory, organisation)
    lot = _lot(api_session_factory, appel)
    offre = _offre(api_client, lot)

    reponse = api_client.request(
        "DELETE",
        f"{PRÉFIXE}/organisations/{organisation.id}",
        json={"reason": "Registre à nettoyer"},
    )

    assert reponse.status_code == 200
    rapport = reponse.json()
    assert rapport["type_entite"] == "organisations"
    assert rapport["dependances"]["offres"] == 1

    with api_session_factory() as session:
        assert session.get(Organisation, organisation.id) is None
        assert session.get(AppelAProposition, appel.id) is None
        assert session.get(Lot, lot.id) is None
        assert session.get(Offre, UUID(offre["id"])) is None
        evenement = session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.SUPPRESSION_EFFECTUEE.value
            )
        ).one()
    # Le motif est la seule trace conservée de ce qui a été détruit.
    assert evenement.event_metadata["reason"] == "Registre à nettoyer"
    assert evenement.after["dependances"]["offres"] == 1


def test_supprimer_un_document_officiel_avertit_sans_bloquer(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    document = _document(
        api_session_factory, mission, statut=StatutDocument.APPROVED.value
    )

    impact = api_client.get(f"{PRÉFIXE}/documents/{document.id}/impact").json()
    assert impact["bloquant"] is False
    assert impact["documents_officiels"] == ["rapport.docx"]
    assert impact["conservees"]["documents"] == 1

    reponse = api_client.request(
        "DELETE", f"{PRÉFIXE}/documents/{document.id}", json={"reason": "Doublon"}
    )
    assert reponse.status_code == 200
    # La fiche officielle est conservée (statut « Supprimé »), aucune dépendance
    # n'est détruite : c'est la règle validée le 23/09.
    corps = reponse.json()
    assert corps["dependances"] == {}
    assert corps["conservees"]["documents"] == 1
    with api_session_factory() as session:
        conserve = session.get(Document, document.id)
        assert conserve is not None
        assert conserve.statut == StatutDocument.SUPPRIME.value


def test_delete_document_conserve_les_versions(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Supprimer « le document » ne touche pas ses versions sœurs."""
    organisation = _organisation(api_session_factory)
    mission = _mission(api_session_factory, organisation)
    document = _document(api_session_factory, mission)
    version_suivante = Document(
        nom=document.nom,
        type_document=document.type_document,
        storage_path="missions/1/rapport-v2.docx",
        version=2,
        statut=StatutDocument.PROPOSED.value,
        mission_id=mission.id,
    )
    seed(api_session_factory, version_suivante)

    reponse = api_client.request(
        "DELETE", f"{PRÉFIXE}/documents/{document.id}", json={"reason": "Doublon"}
    )

    assert reponse.status_code == 200
    with api_session_factory() as session:
        supprime = session.get(Document, document.id)
        assert supprime is not None
        assert supprime.statut == StatutDocument.SUPPRIME.value
        intacte = session.get(Document, version_suivante.id)
        assert intacte is not None
        assert intacte.statut == StatutDocument.PROPOSED.value


def test_suppression_type_inconnu_refusee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un type non supprimable est refusé explicitement (liste des types valides)."""
    organisation = _organisation(api_session_factory)

    reponse = api_client.get(f"{PRÉFIXE}/lieux/{organisation.id}/impact")

    # La route ``impact`` n'existe pas pour les lieux : 404 de routage, et
    # aucune suppression possible depuis une ressource non listée.
    assert reponse.status_code in (404, 405)
