"""Partage de documents **par référence** dans le chat et les annonces (ADR 0013).

Le partage ne copie plus les octets : il écrit ``document_id`` sur le message
(chat) ou la pièce d'annonce. L'accès est ouvert **par le canal** (membres de
la conversation, annonces visibles de tous), sans élargir la visibilité dans
la liste Documents ; une fiche purgée ou en corbeille rend la pièce
introuvable — la fiche fait foi.

Tests sur l'API réelle (mode lab : un seul compte technique, suffisant ici —
les règles testées portent sur le canal, pas sur les rôles).
"""

import io
import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.ddl import CreateColumn

from app.core.config import get_settings
from app.domain.base import Base

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
def usine() -> Iterator[sessionmaker[Session]]:
    """Fabrique SQLite mémoire partagée — les routes voient les seeds."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client_partage(monkeypatch, tmp_path, usine) -> Iterator[TestClient]:
    """Client API redirigé sur la fabrique, stockage sous tmp_path."""
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    from app.infrastructure.database import get_session
    from app.main import app

    def _override() -> Iterator[Session]:
        session = usine()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    from app.main import app as application

    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


def _deposer(client: TestClient, nom: str = "support.png") -> dict:
    reponse = client.post(
        "/api/v1/documents",
        files={"file": (nom, io.BytesIO(_PNG), "image/png")},
        data={"type_document": "autre"},
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _conversation(usine: sessionmaker[Session]) -> str:
    """Conversation seedée : le membre appelant est l'utilisateur technique lab."""
    from app.api.deps import _LAB_USER_ID
    from app.domain.identity import Utilisateur
    from app.domain.social import Conversation, MembreConversation

    session = usine()
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


# --- Chat : partage par référence --------------------------------------------


def test_partage_chat_pointe_la_fiche_sans_la_copier(
    client_partage: TestClient, usine: sessionmaker[Session], tmp_path
) -> None:
    document = _deposer(client_partage)
    conversation_id = _conversation(usine)

    partage = client_partage.post(
        f"/api/v1/social/conversations/{conversation_id}/documents",
        json={"document_id": document["id"], "contenu": "Voici le support"},
    )
    assert partage.status_code == 201, partage.text
    message = partage.json()
    assert message["document_id"] == document["id"]
    assert message["piece_chemin"] is None  # aucun octet copié dans le scope chat
    assert message["piece_nom"] == "support.png"

    # Aucun fichier n'a été créé sous le scope chat : le dossier du fil ne
    # contient que ce que le téléversement direct y mettrait (rien ici).
    fichiers_chat = [
        p for p in tmp_path.rglob("*") if p.is_file() and "chat" in p.parts
    ]
    assert fichiers_chat == []

    # Le téléchargement par les membres sert le fichier **depuis la fiche**.
    piece = client_partage.get(f"/api/v1/social/pieces/{message['id']}")
    assert piece.status_code == 200
    assert piece.headers["content-type"].startswith("image/png")


def test_partage_chat_document_inconnu_404(
    client_partage: TestClient, usine: sessionmaker[Session]
) -> None:
    conversation_id = _conversation(usine)
    partage = client_partage.post(
        f"/api/v1/social/conversations/{conversation_id}/documents",
        json={"document_id": str(uuid4()), "contenu": "x"},
    )
    assert partage.status_code == 404


def test_piece_de_fiche_purgee_introuvable(
    client_partage: TestClient, usine: sessionmaker[Session]
) -> None:
    """La fiche fait foi : purgée, la pièce du partage ne se télécharge plus."""
    from app.domain.social import Conversation, Message
    from tests.conftest import seed

    document = _deposer(client_partage)
    conversation_id = _conversation(usine)

    partage = client_partage.post(
        f"/api/v1/social/conversations/{conversation_id}/documents",
        json={"document_id": document["id"], "contenu": "Support"},
    )
    assert partage.status_code == 201
    message_id = partage.json()["id"]

    # Suppression logique puis purge : la fiche disparaît.
    suppression = client_partage.request(
        "DELETE", f"/api/v1/documents/{document['id']}", json={"reason": "Plus utile"}
    )
    assert suppression.status_code == 200, suppression.text

    # En corbeille : plus téléchargeable depuis le partage.
    piece = client_partage.get(f"/api/v1/social/pieces/{message_id}")
    assert piece.status_code == 404


def test_piece_de_fiche_supprimee_introuvable_pour_les_membres(
    client_partage: TestClient, usine: sessionmaker[Session]
) -> None:
    """ADR 0013 : un document en corbeille reste lisible de son propriétaire et
    de l'administration — pas des destinataires d'un partage.

    Le compte technique est ici le déposant : sa propre lecture reste ouverte,
    le canal, lui, ne sert plus la pièce.
    """
    document = _deposer(client_partage, nom="interne.png")
    conversation_id = _conversation(usine)
    partage = client_partage.post(
        f"/api/v1/social/conversations/{conversation_id}/documents",
        json={"document_id": document["id"], "contenu": "Pièce"},
    )
    message_id = partage.json()["id"]

    suppression = client_partage.request(
        "DELETE", f"/api/v1/documents/{document['id']}", json={"reason": "Plus utile"}
    )
    assert suppression.status_code == 200, suppression.text

    piece = client_partage.get(f"/api/v1/social/pieces/{message_id}")
    assert piece.status_code == 404
    # La fiche, elle, reste lisible du déposant (corbeille ≠ purge).
    fiche = client_partage.get(f"/api/v1/documents/{document['id']}")
    assert fiche.status_code == 200
    assert fiche.json()["statut"] == "supprime"


# --- Annonces : fiches jointes par référence ----------------------------------


def test_annonce_joint_des_fiches_par_reference(
    client_partage: TestClient, usine: sessionmaker[Session], tmp_path
) -> None:
    document = _deposer(client_partage, nom="guide.png")

    annonce = client_partage.post(
        "/api/v1/social/annonces",
        json={
            "titre": "Guide de la formation",
            "contenu": "Voici le guide",
            "document_ids": [document["id"]],
        },
    )
    assert annonce.status_code == 201, annonce.text
    pieces = annonce.json()["pieces"]
    assert len(pieces) == 1
    assert pieces[0]["document_id"] == document["id"]
    assert pieces[0]["nom"] == "guide.png"

    # Aucun octet copié : le téléversement d'annonce crée des fichiers sous
    # chat/annonce-*, une référence n'en crée aucun.
    fichiers_annonce = [
        p for p in tmp_path.rglob("*") if p.is_file() and "annonce" in p.name
    ]
    assert fichiers_annonce == []

    # Le téléchargement sert le fichier depuis la fiche.
    piece = client_partage.get(
        f"/api/v1/social/annonces/{annonce.json()['id']}/pieces/{pieces[0]['id']}"
    )
    assert piece.status_code == 200
    assert piece.headers["content-type"].startswith("image/png")


def test_annonce_document_inconnu_404(
    client_partage: TestClient, usine: sessionmaker[Session]
) -> None:
    annonce = client_partage.post(
        "/api/v1/social/annonces",
        json={"titre": "Sans fiche", "document_ids": [str(uuid4())]},
    )
    assert annonce.status_code == 404


def test_annonce_reference_et_televersement_coexistent(client_partage: TestClient) -> None:
    """Les deux chemins restent valides côte à côte : une fiche référencée et
    une pièce téléversée dans la même annonce."""
    document = _deposer(client_partage, nom="fiche.png")

    televers = client_partage.post(
        "/api/v1/social/annonces/pieces",
        files={"file": ("brouillon.png", io.BytesIO(_PNG), "image/png")},
    )
    assert televers.status_code == 201, televers.text

    annonce = client_partage.post(
        "/api/v1/social/annonces",
        json={
            "titre": "Deux pièces",
            "pieces": [
                {
                    "chemin": televers.json()["chemin"],
                    "nom": "brouillon.png",
                    "mime": "image/png",
                    "taille": televers.json()["taille"],
                }
            ],
            "document_ids": [document["id"]],
        },
    )
    assert annonce.status_code == 201, annonce.text
    pieces = annonce.json()["pieces"]
    assert len(pieces) == 2
    par_reference = [p for p in pieces if p.get("document_id")]
    par_televersement = [p for p in pieces if not p.get("document_id")]
    assert len(par_reference) == 1
    assert len(par_televersement) == 1

    # Le champ historique (pièce unique) reste vide : il ne porte que les
    # téléversements, et le premier élément est ici un téléversement.
    donnees = annonce.json()
    assert donnees["pieces"][0]["nom"] == "brouillon.png"
