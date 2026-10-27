"""Demandes du 30/09 : statuts de documents de session, partage dans le chat, retrait d'affectation.

Trois règles :
- un document de **session** n'est pas une offre : il naît ``genere`` (produit
  par l'assistant formateur / l'outil de génération) ou ``importe`` (dépôt
  manuel d'un formateur), jamais ``proposed``/``draft`` ;
- un document de la plateforme peut être **partagé** dans une conversation :
  le fichier est copié dans le scope ``chat`` et le message porteur est
  téléchargeable par tous les membres (et seulement eux) ;
- une affectation **refusée** peut être retirée de la liste — une proposition
  en attente ou une approuvée ne peuvent pas (409).
"""

from __future__ import annotations

import io
import uuid as uuid_module
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import StatutMission
from app.domain.execution import Mission
from app.domain.organization import Organisation
from app.domain.social import Message
from tests.conftest import seed

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


# --- aides ---------------------------------------------------------------------


def _mission(factory: sessionmaker[Session]) -> Mission:
    """Mission seedée (planifiée) pour les tests d'affectation."""
    org = Organisation(nom="Client 30/09")
    seed(factory, org)
    mission = Mission(
        organisation_id=org.id,
        reference="M-3009",
        titre="Mission demandes 30/09",
        date_debut=date(2026, 11, 2),
        date_fin=date(2026, 11, 30),
        statut=StatutMission.PLANIFIE.value,
    )
    seed(factory, mission)
    return mission


def _session_formation(client: TestClient, mission_id: str) -> str:
    cree = client.post(
        f"/api/v1/missions/{mission_id}/sessions",
        json={"theme": "S1", "date_debut": "2026-11-03", "date_fin": "2026-11-04"},
    )
    assert cree.status_code == 201, cree.text
    return cree.json()["id"]


def _deposer_document_session(
    client: TestClient, session_id: str, *, agent: bool = False
) -> dict:
    corps: dict[str, object] = {
        "type_document": "checklist",
        "session_id": session_id,
    }
    if agent:
        corps["proposed_by_agent"] = "agent_assistant_formateur"
    reponse = client.post(
        "/api/v1/documents",
        files={"file": ("checklist-session.png", io.BytesIO(_PNG), "image/png")},
        data=corps,
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _conversation(api_session_factory: sessionmaker[Session]) -> str:
    """Conversation seedée : le membre appelant est l'utilisateur technique lab."""
    from app.api.deps import _LAB_USER_ID
    from app.domain.identity import Utilisateur
    from app.domain.social import Conversation, MembreConversation

    session = api_session_factory()
    try:
        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(lab)
        session.flush()
        conversation = Conversation(
            type="conference", titre="Partage documents", creee_par_id=lab.id
        )
        session.add(conversation)
        session.flush()
        session.add(
            MembreConversation(
                conversation_id=conversation.id, utilisateur_id=lab.id, role="membre"
            )
        )
        session.commit()
        return str(conversation.id)
    finally:
        session.close()


# --- 1. Statut de naissance des documents de session ---------------------------


def test_document_session_agent_est_genere(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Un document de session produit par l'assistant naît ``genere`` (30/09)."""
    monkeypatch.setattr(get_settings_safe(), "storage_root", str(tmp_path))
    mission = _mission(api_session_factory)
    session_id = _session_formation(api_client, str(mission.id))

    document = _deposer_document_session(api_client, session_id, agent=True)
    assert document["statut"] == "genere"
    assert document["session_id"] == session_id


def test_document_session_humain_est_importe(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Un document de session déposé manuellement naît ``importe`` (30/09)."""
    monkeypatch.setattr(get_settings_safe(), "storage_root", str(tmp_path))
    mission = _mission(api_session_factory)
    session_id = _session_formation(api_client, str(mission.id))

    document = _deposer_document_session(api_client, session_id)
    assert document["statut"] == "importe"

    # Hors session, le cycle d'offre reste le même qu'avant.
    hors_session = api_client.post(
        "/api/v1/documents",
        files={"file": ("autre.png", io.BytesIO(_PNG), "image/png")},
        data={"type_document": "autre"},
    )
    assert hors_session.status_code == 201
    assert hors_session.json()["statut"] == "draft"


def test_document_session_supprimable(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Un document ``genere``/``importe`` reste supprimable (machine à états)."""
    monkeypatch.setattr(get_settings_safe(), "storage_root", str(tmp_path))
    mission = _mission(api_session_factory)
    session_id = _session_formation(api_client, str(mission.id))

    document = _deposer_document_session(api_client, session_id, agent=True)
    suppression = api_client.request(
        "DELETE",
        f"/api/v1/documents/{document['id']}",
        json={"reason": "Retrait test"},
    )
    assert suppression.status_code == 200, suppression.text

    fiche = api_client.get(f"/api/v1/documents/{document['id']}")
    assert fiche.json()["statut"] == "supprime"


# --- 2. Partage d'un document plateforme dans le chat --------------------------


def test_importer_document_plateforme_dans_chat(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Partage par **référence** (ADR 0013) : la fiche est pointée, jamais copiée.

    Le message porte ``document_id`` ; aucun fichier ne quitte le dossier
    métier de la fiche, et le téléchargement se sert depuis la fiche.
    """
    monkeypatch.setattr(get_settings_safe(), "storage_root", str(tmp_path))

    depose = api_client.post(
        "/api/v1/documents",
        files={"file": ("support-formation.png", io.BytesIO(_PNG), "image/png")},
        data={"type_document": "autre"},
    )
    assert depose.status_code == 201
    document = depose.json()

    conversation_id = _conversation(api_session_factory)
    partage = api_client.post(
        f"/api/v1/social/conversations/{conversation_id}/documents",
        json={"document_id": document["id"], "contenu": "Voici le support"},
    )
    assert partage.status_code == 201, partage.text
    message = partage.json()
    assert message["document_id"] == document["id"]
    assert message["piece_nom"] == "support-formation.png"
    assert message["piece_chemin"] is None  # aucun octet copié dans le scope chat

    # Le message porteur est en base avec sa référence (visible par les membres).
    with api_session_factory() as db:
        ligne = db.get(Message, uuid_module.UUID(message["id"]))
        assert ligne is not None
        assert ligne.document_id == uuid_module.UUID(document["id"])
        assert ligne.piece_chemin is None

    # Le téléchargement par les membres sert le fichier **depuis la fiche**.
    piece = api_client.get(f"/api/v1/social/pieces/{message['id']}")
    assert piece.status_code == 200
    assert piece.headers["content-type"].startswith("image/png")

    # L'original reste intact et téléchargeable par le référentiel documentaire.
    telechargement = api_client.get(f"/api/v1/documents/{document['id']}/contenu")
    assert telechargement.status_code == 200


def test_importer_document_conversation_inconnue_404(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Conversation inconnue : jamais d'importation silencieuse."""
    monkeypatch.setattr(get_settings_safe(), "storage_root", str(tmp_path))
    reponse = api_client.post(
        f"/api/v1/social/conversations/{uuid4()}/documents",
        json={"document_id": str(uuid4())},
    )
    assert reponse.status_code in {403, 404}


# --- 3. Retrait d'une affectation refusée --------------------------------------


def test_supprimer_affectation_refusee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Refusée → retrait 204 ; la liste ne la contient plus."""
    mission = _mission(api_session_factory)
    equipe_id = _equipe(api_client)

    proposition = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": equipe_id,
            "role_dans_mission": "formateur",
        },
    )
    assert proposition.status_code == 202, proposition.text
    affectation = proposition.json()

    refus = api_client.post(
        f"/api/v1/affectations/{affectation['id']}/refus",
        json={"decision": "rejete", "reason": "Non disponible"},
    )
    assert refus.status_code == 200

    retrait = api_client.delete(f"/api/v1/affectations/{affectation['id']}")
    assert retrait.status_code == 204, retrait.text

    liste = api_client.get(f"/api/v1/missions/{mission.id}/affectations").json()
    affectations = liste.get("affectations", liste)
    assert all(a["id"] != affectation["id"] for a in affectations)


def test_supprimer_affectation_non_refusee_refuse(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Proposée ou approuvée → 409 (décision exigée / historique conservé)."""
    mission = _mission(api_session_factory)
    equipe_id = _equipe(api_client)

    proposition = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": equipe_id,
            "role_dans_mission": "formateur",
        },
    )
    assert proposition.status_code == 202
    affectation = proposition.json()

    # Proposée : pas de retrait possible.
    en_attente = api_client.delete(f"/api/v1/affectations/{affectation['id']}")
    assert en_attente.status_code == 409

    # Approuvée : pas davantage.
    approbation = api_client.post(
        f"/api/v1/affectations/{affectation['id']}/approbation",
        json={"decision": "approuve", "reason": "OK"},
    )
    assert approbation.status_code == 200
    approuvee = api_client.delete(f"/api/v1/affectations/{affectation['id']}")
    assert approuvee.status_code == 409


def _equipe(api_client: TestClient) -> str:
    reponse = api_client.post(
        "/api/v1/equipes",
        json={"nom": "Formateur", "prenom": "Test"},
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["id"]


def get_settings_safe():
    from app.core.config import get_settings

    return get_settings()
