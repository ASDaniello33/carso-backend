"""Phase 4 (instruction/11) — routes documentaires.

Tests HTTP sur la même infrastructure que ``test_api.py`` : session SQLite
redirigée (contrat transactionnel conservé) et **racine de stockage temporaire**
(la suite n'écrit jamais dans ``/storage/carso``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.document import AuditEvent, Document
from app.domain.enums import (
    ActionAudit,
    StatutDocument,
    StatutMission,
    TypeDocument,
    TypeOffre,
)
from app.domain.execution import Mission
from app.domain.organization import AppelAProposition, Lot, Offre, Organisation
from tests.conftest import seed
from tests.document_fixtures import build_docx, build_pdf

MISSION_TEXTE = "MISSION CARSO 2026"
PRÉFIXE = "/api/v1"


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirige la racine documentaire vers un répertoire temporaire."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def documents_client(api_client: TestClient, storage_root: Path) -> Iterator[TestClient]:
    """Client HTTP dont les documents se déposent dans ``storage_root``."""
    del storage_root
    yield api_client


@dataclass(frozen=True)
class Donnees:
    """Ancres métier disponibles pour les dépôts documentaires."""

    organisation: Organisation
    appel: AppelAProposition
    mission: Mission


@pytest.fixture
def donnees(api_session_factory: sessionmaker[Session]) -> Donnees:
    organisation = Organisation(nom="Client Documents")
    seed(api_session_factory, organisation)
    appel = AppelAProposition(
        organisation_id=organisation.id, reference="AO-DOC-01", titre="Formation"
    )
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-DOC-01",
        titre="Mission documents",
        statut=StatutMission.PLANIFIEE.value,
    )
    seed(api_session_factory, appel, mission)
    return Donnees(organisation=organisation, appel=appel, mission=mission)


def _upload(
    client: TestClient,
    *,
    nom: str = "Fiche technique.pdf",
    contenu: bytes | None = None,
    type_document: str = TypeDocument.FICHE_TECHNIQUE.value,
    **champs: str,
) -> httpx.Response:
    return client.post(
        f"{PRÉFIXE}/documents",
        files={"file": (nom, contenu or build_pdf(MISSION_TEXTE), "application/pdf")},
        data={"type_document": type_document, **champs},
    )


# --- Dépôt et consultation ------------------------------------------------------


def test_upload_puis_lecture(documents_client: TestClient, donnees: Donnees) -> None:
    mission = donnees.mission
    reponse = _upload(documents_client, mission_id=str(mission.id))

    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["statut"] == StatutDocument.DRAFT.value
    assert corps["version"] == 1
    assert corps["mime_type"] == "application/pdf"
    assert corps["storage_path"] == f"missions/{mission.id}/Fiche technique.pdf"
    assert corps["created_at"]

    lecture = documents_client.get(f"{PRÉFIXE}/documents/{corps['id']}")
    assert lecture.status_code == 200
    assert lecture.json()["nom"] == "Fiche technique.pdf"


def test_import_manuel_du_document_d_offre(
    documents_client: TestClient,
    donnees: Donnees,
    api_session_factory: sessionmaker[Session],
) -> None:
    """Contrat de l'import manuel : un fichier ancré à l'offre, en brouillon.

    Le document entre dans **le même cycle** qu'une production d'agent (soumission,
    approbation) puis suit la règle de suppression logique : disparu de la fiche
    offre, conservé dans le référentiel documentaire.
    """
    lot = Lot(appel_a_proposition_id=donnees.appel.id, numero="1", titre="Lot 1")
    seed(api_session_factory, lot)
    offre = Offre(
        organisation_id=donnees.organisation.id,
        appel_a_proposition_id=donnees.appel.id,
        lot_id=lot.id,
        type=TypeOffre.OFFRE_TECHNIQUE.value,
        reference="O-DOC-01",
        titre="Offre technique",
    )
    seed(api_session_factory, offre)

    reponse = _upload(
        documents_client,
        nom="Offre technique client.pdf",
        type_document=TypeDocument.OFFRE.value,
        offre_id=str(offre.id),
    )

    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    # Un dépôt humain n'est pas une validation : il naît en brouillon.
    assert corps["statut"] == StatutDocument.DRAFT.value
    assert corps["type_document"] == TypeDocument.OFFRE.value
    assert corps["offre_id"] == str(offre.id)
    assert corps["storage_path"] == f"offres/{offre.id}/Offre technique client.pdf"

    liste = documents_client.get(f"{PRÉFIXE}/documents", params={"offre_id": str(offre.id)})
    assert [document["id"] for document in liste.json()] == [corps["id"]]

    # Même cycle que pour un document d'agent.
    assert (
        documents_client.post(f"{PRÉFIXE}/documents/{corps['id']}/soumission").status_code == 200
    )
    approbation = documents_client.post(
        f"{PRÉFIXE}/documents/{corps['id']}/approbation", json={"reason": "Offre relue"}
    )
    assert approbation.status_code == 200
    assert approbation.json()["statut"] == StatutDocument.APPROVED.value

    # Suppression logique : la fiche offre ne propose plus de fichier absent.
    suppression = documents_client.request(
        "DELETE",
        f"{PRÉFIXE}/documents/{corps['id']}",
        json={"reason": "Fichier erroné"},
    )
    assert suppression.status_code == 200
    assert suppression.json()["conservees"]["documents"] == 1
    assert (
        documents_client.get(
            f"{PRÉFIXE}/documents", params={"offre_id": str(offre.id)}
        ).json()
        == []
    )


def test_upload_par_agent_reste_une_proposition(
    documents_client: TestClient, donnees: Donnees
) -> None:
    mission = donnees.mission
    reponse = _upload(
        documents_client, mission_id=str(mission.id), proposed_by_agent="AgentAssistantMission"
    )

    assert reponse.status_code == 201
    assert reponse.json()["statut"] == StatutDocument.PROPOSED.value


def test_upload_extension_interdite(
    documents_client: TestClient, donnees: Donnees
) -> None:
    mission = donnees.mission
    reponse = documents_client.post(
        f"{PRÉFIXE}/documents",
        files={"file": ("script.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"type_document": TypeDocument.AUTRE.value, "mission_id": str(mission.id)},
    )

    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"


def test_upload_sans_ancre_metier_est_non_classe(documents_client: TestClient) -> None:
    """Un document sans objet métier reste un document (règle confirmée).

    Aucune ancre ⇒ rangé sous ``generated/{id}/`` avec le type ``non_classe`` :
    l'agent peut toujours enregistrer ce que l'utilisateur demande.
    """
    reponse = _upload(documents_client)

    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["type_document"] == TypeDocument.NON_CLASSE.value
    assert corps["storage_path"].startswith("generated/")


def test_upload_ancre_inexistante_est_non_classe(documents_client: TestClient) -> None:
    """Ancre introuvable ⇒ repli ``generated/{id}/`` (aucune FK inventée)."""
    reponse = _upload(
        documents_client,
        mission_id="00000000-0000-0000-0000-000000000000",
    )

    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["type_document"] == TypeDocument.NON_CLASSE.value
    assert corps["mission_id"] is None
    assert corps["storage_path"].startswith("generated/")


def test_telechargement_retourne_les_octets(
    documents_client: TestClient, donnees: Donnees, storage_root: Path
) -> None:
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()

    reponse = documents_client.get(f"{PRÉFIXE}/documents/{document['id']}/contenu")

    assert reponse.status_code == 200
    assert reponse.content == build_pdf(MISSION_TEXTE)
    # Le nom voyage en RFC 5987 (filename*=utf-8''...) : on vérifie le nom décodé.
    assert "Fiche technique.pdf" in unquote(reponse.headers["content-disposition"])
    assert (storage_root / document["storage_path"]).is_file()


def test_telechargement_fichier_absent(
    documents_client: TestClient, donnees: Donnees, storage_root: Path
) -> None:
    """Le fichier disparu du disque donne un 404 explicite, jamais une 500."""
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()
    (storage_root / document["storage_path"]).unlink()

    reponse = documents_client.get(f"{PRÉFIXE}/documents/{document['id']}/contenu")

    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


# --- Versioning et décisions ----------------------------------------------------


def _cycle_validation(client: TestClient, document_id: str) -> None:
    """Brouillon → soumission → approbation (chemin humain complet)."""
    assert client.post(f"{PRÉFIXE}/documents/{document_id}/soumission").status_code == 200
    reponse = client.post(
        f"{PRÉFIXE}/documents/{document_id}/approbation",
        json={"reason": "Conforme"},
    )
    assert reponse.status_code == 200


def test_remplacement_soumission_approbation(
    documents_client: TestClient, donnees: Donnees
) -> None:
    mission = donnees.mission
    v1 = _upload(documents_client, mission_id=str(mission.id)).json()
    _cycle_validation(documents_client, v1["id"])

    remplacement = documents_client.post(
        f"{PRÉFIXE}/documents/{v1['id']}/remplacement",
        files={"file": ("Fiche technique.pdf", build_pdf("VERSION 2"), "application/pdf")},
        data={},
    )

    assert remplacement.status_code == 202
    v2 = remplacement.json()
    assert v2["version"] == 2
    assert v2["statut"] == StatutDocument.DRAFT.value
    assert v2["storage_path"].endswith("Fiche technique_v2.pdf")

    versions = documents_client.get(f"{PRÉFIXE}/documents/{v1['id']}/versions").json()
    assert [version["version"] for version in versions] == [2, 1]
    # La version officielle n'est pas encore remplacée.
    assert versions[1]["statut"] == StatutDocument.APPROVED.value

    _cycle_validation(documents_client, v2["id"])

    versions = documents_client.get(f"{PRÉFIXE}/documents/{v1['id']}/versions").json()
    assert versions[0]["statut"] == StatutDocument.APPROVED.value
    assert versions[1]["statut"] == StatutDocument.ARCHIVED.value


def test_remplacement_avec_un_fichier_de_nom_different_garde_le_nom_logique(
    documents_client: TestClient, donnees: Donnees
) -> None:
    """Remplacer un fichier n'est pas renommer le document (bug du 07/10).

    L'interface envoie le fichier choisi par l'utilisateur — nom libre, aucun
    champ ``nom`` de formulaire. Le remplacer échouait en « le nom du document
    ne peut pas changer en cours de versionnement » : le nom physique du dépôt
    était pris pour une intention de renommage. La version 2 doit naître sous le
    nom logique existant.
    """
    v1 = _upload(documents_client, mission_id=str(donnees.mission.id)).json()

    remplacement = documents_client.post(
        f"{PRÉFIXE}/documents/{v1['id']}/remplacement",
        files={
            "file": (
                "appel recu v2 corrige.pdf",
                build_pdf("VERSION 2"),
                "application/pdf",
            )
        },
        data={},
    )

    assert remplacement.status_code == 202
    v2 = remplacement.json()
    assert v2["version"] == 2
    assert v2["nom"] == v1["nom"] == "Fiche technique.pdf"
    assert v2["storage_path"].endswith("Fiche technique_v2.pdf")


def test_remplacement_avec_un_nom_explicite_different_reste_refuse(
    documents_client: TestClient, donnees: Donnees
) -> None:
    """Un ``nom`` explicite différent exprime un renommage : refusé, jamais muet."""
    v1 = _upload(documents_client, mission_id=str(donnees.mission.id)).json()

    reponse = documents_client.post(
        f"{PRÉFIXE}/documents/{v1['id']}/remplacement",
        files={"file": ("Fiche technique.pdf", build_pdf("VERSION 2"), "application/pdf")},
        data={"nom": "Autre nom.pdf"},
    )

    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"
    assert "ne peut pas changer" in reponse.json()["error"]["message"]


def test_approbation_depuis_brouillon_interdite(
    documents_client: TestClient, donnees: Donnees
) -> None:
    """`draft → approved` est interdit par la machine à états (409)."""
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()

    reponse = documents_client.post(
        f"{PRÉFIXE}/documents/{document['id']}/approbation",
        json={"decided_by": "responsable"},
    )

    assert reponse.status_code == 409
    assert reponse.json()["error"]["code"] == "conflict"


def test_refus_archive_la_version_proposee(
    documents_client: TestClient, donnees: Donnees
) -> None:
    mission = donnees.mission
    document = _upload(
        documents_client, mission_id=str(mission.id), proposed_by_agent="AgentRH"
    ).json()

    reponse = documents_client.post(
        f"{PRÉFIXE}/documents/{document['id']}/refus",
        json={"reason": "Version non conforme"},
    )

    assert reponse.status_code == 200
    assert reponse.json()["statut"] == StatutDocument.ARCHIVED.value


def test_archivage_puis_transition_interdite(
    documents_client: TestClient, donnees: Donnees, storage_root: Path
) -> None:
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()

    reponse = documents_client.post(
        f"{PRÉFIXE}/documents/{document['id']}/archivage",
        json={"reason": "Obsolète"},
    )

    assert reponse.status_code == 200
    assert reponse.json()["statut"] == StatutDocument.ARCHIVED.value
    # Archivage ≠ suppression : le fichier reste sur disque.
    assert (storage_root / document["storage_path"]).is_file()

    soumission = documents_client.post(f"{PRÉFIXE}/documents/{document['id']}/soumission")
    assert soumission.status_code == 409


# --- Extraction -----------------------------------------------------------------


def test_extraction_est_disponible_et_sans_mutation(
    documents_client: TestClient,
    donnees: Donnees,
    api_session_factory: sessionmaker[Session],
) -> None:
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()

    with api_session_factory() as session:
        evenements_avant = session.scalar(select(func.count()).select_from(AuditEvent))
        documents_avant = session.scalar(select(func.count()).select_from(Document))

    reponse = documents_client.get(f"{PRÉFIXE}/documents/{document['id']}/extraction")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert MISSION_TEXTE in corps["texte"]
    assert corps["adaptateur"] == "pypdf"
    assert corps["nb_pages"] == 1
    assert corps["tronque"] is False

    with api_session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(AuditEvent)) == evenements_avant
        )
        assert (
            session.scalar(select(func.count()).select_from(Document)) == documents_avant
        )


def test_extraction_docx_via_api(
    documents_client: TestClient, donnees: Donnees
) -> None:
    mission = donnees.mission
    contenu = build_docx(paragraphes=["Fiche technique CARSO", "Objectif : 20 agents"])
    document = documents_client.post(
        f"{PRÉFIXE}/documents",
        files={
            "file": (
                "Fiche.docx",
                contenu,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "type_document": TypeDocument.FICHE_TECHNIQUE.value,
            "mission_id": str(mission.id),
        },
    ).json()

    reponse = documents_client.get(f"{PRÉFIXE}/documents/{document['id']}/extraction")

    assert reponse.status_code == 200
    assert "Fiche technique CARSO" in reponse.json()["texte"]


# --- Listing --------------------------------------------------------------------


def test_listing_exige_un_filtre(documents_client: TestClient) -> None:
    reponse = documents_client.get(f"{PRÉFIXE}/documents")

    assert reponse.status_code == 422


def test_listing_par_mission(documents_client: TestClient, donnees: Donnees) -> None:
    mission = donnees.mission
    _upload(documents_client, mission_id=str(mission.id))

    reponse = documents_client.get(
        f"{PRÉFIXE}/documents", params={"mission_id": str(mission.id)}
    )

    assert reponse.status_code == 200
    assert len(reponse.json()) == 1


# --- Modèles de documents -------------------------------------------------------


def test_registre_des_modeles(
    documents_client: TestClient, donnees: Donnees
) -> None:
    organisation = donnees.organisation
    gabarit = documents_client.post(
        f"{PRÉFIXE}/documents",
        files={
            "file": (
                "Modele client.docx",
                build_docx(paragraphes=["Modèle CARSO"]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "type_document": TypeDocument.TEMPLATE.value,
            "organisation_id": str(organisation.id),
        },
    ).json()

    absent = documents_client.get(
        f"{PRÉFIXE}/organisations/{organisation.id}/modeles-documents/actif",
        params={"type_document": TypeDocument.OFFRE.value},
    )
    assert absent.status_code == 404
    assert "aucun modèle par défaut" in absent.json()["error"]["message"]

    creation = documents_client.post(
        f"{PRÉFIXE}/organisations/{organisation.id}/modeles-documents",
        json={
            "nom": "Modèle offre client",
            "type_document": TypeDocument.OFFRE.value,
            "document_template_id": gabarit["id"],
            "registered_by": "carso",
        },
    )
    assert creation.status_code == 201
    assert creation.json()["version"] == 1

    actif = documents_client.get(
        f"{PRÉFIXE}/organisations/{organisation.id}/modeles-documents/actif",
        params={"type_document": TypeDocument.OFFRE.value},
    )
    assert actif.status_code == 200
    assert actif.json()["document_template_id"] == gabarit["id"]


# --- Suppression (règle validée 18/09) -------------------------------------------


def test_suppression_logique_conserve_la_fiche(
    documents_client: TestClient,
    donnees: Donnees,
    api_session_factory: sessionmaker[Session],
    storage_root: Path,
) -> None:
    """Supprimer un document retire le fichier, jamais la fiche (règle 23/09).

    Aucune dépendance n'est détruite : le rapport le dit (``dependances`` vide,
    ``conservees`` rempli), la fiche reste en base avec le statut « Supprimé »,
    et seul le fichier quitte le disque.
    """
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()

    reponse = documents_client.request(
        "DELETE",
        f"{PRÉFIXE}/documents/{document['id']}",
        json={"reason": "Version de test devenue inutile"},
    )

    assert reponse.status_code == 200
    rapport = reponse.json()
    assert rapport["type_entite"] == "documents"
    assert rapport["identifiant"] == document["id"]
    assert rapport["dependances"] == {}
    assert rapport["conservees"]["documents"] == 1
    assert rapport["fichiers_supprimes"] == 1

    import uuid as _uuid

    with api_session_factory() as session:
        conserve = session.get(Document, _uuid.UUID(document["id"]))
        assert conserve is not None
        assert conserve.statut == StatutDocument.SUPPRIME.value
        evenement = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == document["id"],
                AuditEvent.action == ActionAudit.DOCUMENT_SUPPRIME.value,
            )
        ).one()
    assert evenement.event_metadata["reason"] == "Version de test devenue inutile"
    assert evenement.after["fichiers_retires"] == 1
    # Le fichier physique quitte le disque ; la référence, elle, reste écrite.
    assert not (storage_root / document["storage_path"]).exists()


def test_suppression_document_officiel_conservee(
    documents_client: TestClient, donnees: Donnees, storage_root: Path
) -> None:
    """Un document officiel avertit mais ne bloque plus (règle 23/09)."""
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()
    documents_client.post(f"{PRÉFIXE}/documents/{document['id']}/soumission")
    documents_client.post(
        f"{PRÉFIXE}/documents/{document['id']}/approbation", json={"reason": "Validé"}
    )

    impact = documents_client.get(f"{PRÉFIXE}/documents/{document['id']}/impact").json()
    assert impact["bloquant"] is False
    assert document["nom"] in impact["documents_officiels"]
    assert impact["conservees"]["documents"] == 1
    assert "Supprimé" in impact["message"]

    reponse = documents_client.request(
        "DELETE",
        f"{PRÉFIXE}/documents/{document['id']}",
        json={"reason": "Tentative de suppression d'un officiel"},
    )

    assert reponse.status_code == 200
    assert reponse.json()["conservees"]["documents"] == 1
    assert not (storage_root / document["storage_path"]).exists()


# --- Corbeille : échéance, restauration, purge (ADR 0006) ------------------------


def _supprimer(client: TestClient, document_id: str, motif: str = "Doublon") -> None:
    reponse = client.request(
        "DELETE", f"{PRÉFIXE}/documents/{document_id}", json={"reason": motif}
    )
    assert reponse.status_code == 200


def _brancher_admin(client: TestClient) -> None:
    """Force un administrateur sur les routes d'administration de la purge.

    Le porteur est remplacé **aussi** : en mode lab, la dépendance de session
    rendrait un collaborateur, jamais un administrateur.
    """
    from app.api.deps import get_current_user, require_admin
    from app.domain.enums import RoleUtilisateur
    from app.domain.identity import Utilisateur

    admin = Utilisateur(
        email="admin@carso.test",
        nom="Admin",
        prenom="Carso",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
    )
    client.app.dependency_overrides[require_admin] = lambda: admin
    client.app.dependency_overrides[get_current_user] = lambda: admin


def test_corbeille_http_expose_echeance_et_redepot(
    documents_client: TestClient, donnees: Donnees, storage_root: Path
) -> None:
    """La corbeille annonce l'échéance, et se vide quand la fiche est restaurée."""
    mission = donnees.mission
    document = _upload(documents_client, mission_id=str(mission.id)).json()
    _supprimer(documents_client, document["id"])

    reponse = documents_client.get(f"{PRÉFIXE}/documents/corbeille")
    assert reponse.status_code == 200
    fiche, = reponse.json()
    assert fiche["document_id"] == document["id"]
    assert fiche["nom"] == document["nom"]
    assert fiche["version"] == 1
    assert fiche["statut_avant"] == StatutDocument.DRAFT.value
    # Le motif et l'auteur viennent de la requête et du porteur : jamais inventés.
    assert fiche["supprime_par"]
    assert fiche["motif"] == "Doublon"
    assert fiche["supprime_le"] and fiche["expire_le"]
    assert fiche["retention_jours"] == 30
    assert fiche["expiree"] is False
    assert fiche["jours_restants"] == 30
    # Le fichier a quitté le disque : il faudra le redéposer.
    assert fiche["fichier_present"] is False
    assert fiche["restaurable"] is True
    assert fiche["blocage"] is None

    restauration = documents_client.post(
        f"{PRÉFIXE}/documents/{document['id']}/restauration",
        files={"file": (document["nom"], build_pdf("REDEPOT"), "application/pdf")},
        data={"reason": "Suppression par erreur"},
    )

    assert restauration.status_code == 200
    assert restauration.json()["statut"] == StatutDocument.DRAFT.value
    assert (storage_root / document["storage_path"]).is_file()
    assert documents_client.get(f"{PRÉFIXE}/documents/corbeille").json() == []


def test_restauration_http_sans_fichier_refusee(
    documents_client: TestClient, donnees: Donnees
) -> None:
    """Sans redéposition, la restauration est refusée avec un message explicite."""
    document = _upload(documents_client, mission_id=str(donnees.mission.id)).json()
    _supprimer(documents_client, document["id"])

    reponse = documents_client.post(
        f"{PRÉFIXE}/documents/{document['id']}/restauration",
        data={"reason": "Sans fichier"},
    )

    assert reponse.status_code == 409
    assert "redéposez-le" in reponse.json()["error"]["message"]


def test_purge_reservee_aux_administrateurs(
    documents_client: TestClient,
    donnees: Donnees,
    api_session_factory: sessionmaker[Session],
    storage_root: Path,
) -> None:
    """Purge : 403 pour un collaborateur, effacement réel pour un administrateur."""
    document = _upload(documents_client, mission_id=str(donnees.mission.id)).json()
    _supprimer(documents_client, document["id"])
    url = f"{PRÉFIXE}/documents/{document['id']}/purge"

    refus = documents_client.post(url, json={"reason": "Nettoyage"})
    assert refus.status_code == 403

    _brancher_admin(documents_client)
    # Le motif est obligatoire : une purge muette ne serait pas auditable.
    assert documents_client.post(url, json={"reason": ""}).status_code == 422

    accepte = documents_client.post(url, json={"reason": "Fin de conservation"})
    assert accepte.status_code == 200
    assert accepte.json()["document_id"] == document["id"]

    import uuid as _uuid

    with api_session_factory() as session:
        assert session.get(Document, _uuid.UUID(document["id"])) is None
        evenement = session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == ActionAudit.DOCUMENT_PURGE.value,
                AuditEvent.entity_id == document["id"],
            )
        ).one()
    assert evenement.event_metadata["reason"] == "Fin de conservation"
    assert evenement.before["nom"] == document["nom"]
    assert documents_client.get(f"{PRÉFIXE}/documents/corbeille").json() == []
