"""Images d'un livrable désignées par ``document_id`` ou par chemin logique.

Constat : l'agent devait coller un base64 dans le HTML. Une charge de plusieurs
centaines de kilo-octets est tronquée en cours d'émission, et le document
sortait avec une image absente (ou un cadre vide). Les deux tools de rédaction
acceptent donc une **référence** — document image déposé ou chemin logique —
que le serveur résout, vérifie et embarque lui-même.

Ce fichier verrouille :

- l'insertion réelle (média présent dans le DOCX) pour ``generer_document`` et
  ``generer_document_professionnel`` ;
- les deux formes de référence (``document_id`` et ``chemin``) ;
- le refus explicite d'une ancre sans déclaration, d'une image introuvable et
  d'une entrée à deux sources ;
- l'absence de base64 dans l'appel du modèle (le HTML ne porte que l'ancre).
"""

from __future__ import annotations

import base64
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.documents.schemas import ImageDocumentInput
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_assistant_formateur")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)

#: PNG 1×1 valide : la signature suffit, la taille n'a pas d'importance ici.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)


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
    return DocumentService(
        session,
        storage=storage,
        actor_id="test",
        perimetre=Perimetre(
            role="administrateur", utilisateur_id=uuid4(), est_complet=True
        ),
    )


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Formation")
    session.add(organisation)
    session.flush()
    return organisation


@pytest.fixture
def outils(service: DocumentService) -> dict[str, Any]:
    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=IDENTITE,
            policy=POLICY,
            service_factory=lambda: service,
        )
    }


@pytest.fixture
def image(service: DocumentService) -> Any:
    """Une image réellement déposée dans le stockage (document + fichier)."""
    return service.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom="plan-salle.png",
            stream=io.BytesIO(PNG),
            created_by="test",
        )
    )


def _medias_docx(octets: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(octets)) as archive:
        return [nom for nom in archive.namelist() if nom.startswith("word/media/")]


def _payload_document(organisation: Organisation, image: Any, **images: Any) -> dict[str, Any]:
    return {
        "format": "docx",
        "nom_fichier": "compte_rendu",
        "type_document": "rapport",
        "ancre": "organisation",
        "entity_id": str(organisation.id),
        "titre": "Compte rendu",
        "html": (
            "<div class='h1'>Compte rendu</div>"
            "<p class='p'>La salle est décrite ci-dessous.</p>"
            "<figure data-image='plan'></figure>"
        ),
        "images": images.get("images", []),
    }


def _payload_professionnel(image: Any, **images: Any) -> dict[str, Any]:
    return {
        "action": "generer",
        "contenu_html": (
            "<h1>Compte rendu</h1><p class='lead'>La salle est décrite ci-dessous.</p>"
            "<figure data-image='plan'></figure>"
        ),
        "images": images.get("images", []),
        "titre": "Compte rendu",
        "format": "docx",
        "type_document": "rapport",
    }


# --- Génération libre (generer_document) ------------------------------------


def test_generer_document_insere_une_image_par_document_id(
    service: DocumentService, organisation: Organisation, image: Any, outils: dict[str, Any]
) -> None:
    sortie = outils["generer_document"].invoke(
        _payload_document(
            organisation, image, images=[{"id": "plan", "document_id": str(image.id)}]
        )
    )

    assert sortie["ok"] is True, sortie
    assert sortie["images"] == 1
    document = service.obtenir(UUID(sortie["document_id"]))
    assert _medias_docx(service.storage.read(document.storage_path)) == [
        "word/media/image1.png"
    ]


def test_generer_document_insere_une_image_par_chemin(
    service: DocumentService, organisation: Organisation, image: Any, outils: dict[str, Any]
) -> None:
    sortie = outils["generer_document"].invoke(
        _payload_document(
            organisation, image, images=[{"id": "plan", "chemin": image.storage_path}]
        )
    )

    assert sortie["ok"] is True, sortie
    document = service.obtenir(UUID(sortie["document_id"]))
    assert _medias_docx(service.storage.read(document.storage_path))


def test_generer_document_spec_insere_une_image_par_document_id(
    service: DocumentService, image: Any, outils: dict[str, Any]
) -> None:
    """Voie structurée (héritage) : ``Image(document_id=…)`` était déjà résolue."""
    sortie = outils["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note_structuree",
            "type_document": "rapport",
            "valider": False,
            "document_spec": {
                "metadata": {"titre": "Note structurée", "type_document": "rapport"},
                "blocs": [
                    {"type": "paragraphe", "texte": "Schéma de la salle."},
                    {
                        "type": "image",
                        "document_id": str(image.id),
                        "largeur_mm": 40,
                        "legende": "Plan de salle",
                    },
                ],
            },
        }
    )

    assert sortie["ok"] is True, sortie
    document = service.obtenir(UUID(sortie["document_id"]))
    assert _medias_docx(service.storage.read(document.storage_path))


# --- Génération professionnelle ---------------------------------------------


def test_generer_document_professionnel_insere_une_image_par_document_id(
    service: DocumentService, image: Any, outils: dict[str, Any]
) -> None:
    sortie = outils["generer_document_professionnel"].invoke(
        _payload_professionnel(
            image, images=[{"id": "plan", "document_id": str(image.id)}]
        )
    )

    assert sortie["ok"] is True, sortie
    document = service.obtenir(UUID(sortie["documents"]["principal"]))
    assert _medias_docx(service.storage.read(document.storage_path))


def test_generer_document_professionnel_insere_une_image_par_chemin(
    service: DocumentService, image: Any, outils: dict[str, Any]
) -> None:
    sortie = outils["generer_document_professionnel"].invoke(
        _payload_professionnel(
            image, images=[{"id": "plan", "chemin": image.storage_path}]
        )
    )

    assert sortie["ok"] is True, sortie
    document = service.obtenir(UUID(sortie["documents"]["principal"]))
    assert _medias_docx(service.storage.read(document.storage_path))


# --- Refus explicites --------------------------------------------------------


def test_ancre_sans_declaration_refusee(
    organisation: Organisation, outils: dict[str, Any]
) -> None:
    sortie = outils["generer_document"].invoke(
        _payload_document(organisation, None, images=[])
    )

    assert sortie["ok"] is False
    assert "paramètre `images`" in sortie["error"]["message"]


def test_image_introuvable_refusee(organisation: Organisation, outils: dict[str, Any]) -> None:
    sortie = outils["generer_document"].invoke(
        _payload_document(
            organisation,
            None,
            images=[{"id": "plan", "document_id": str(uuid4())}],
        )
    )

    assert sortie["ok"] is False
    assert "introuvable" in sortie["error"]["message"]


def test_classeur_refuse_les_images_de_contenu(image: Any, outils: dict[str, Any]) -> None:
    """Pas d'image de contenu en XLSX : refus explicite, jamais un oubli muet."""
    payload = _payload_professionnel(
        image, images=[{"id": "plan", "document_id": str(image.id)}]
    )
    payload["format"] = "xlsx"
    sortie = outils["generer_document_professionnel"].invoke(payload)

    assert sortie["ok"] is False
    assert "n'insère pas d'image" in sortie["error"]["message"]


def test_entree_a_deux_sources_refusee() -> None:
    with pytest.raises(PydanticValidationError, match="Une seule source"):
        ImageDocumentInput(id="plan", document_id=uuid4(), chemin="missions/1/x.png")
    with pytest.raises(PydanticValidationError, match="Une seule source"):
        ImageDocumentInput(id="plan")


def test_chemin_absolu_refuse(organisation: Organisation, outils: dict[str, Any]) -> None:
    sortie = outils["generer_document"].invoke(
        _payload_document(
            organisation, None, images=[{"id": "plan", "chemin": "C:/Windows/logo.png"}]
        )
    )

    assert sortie["ok"] is False
    # Un chemin absolu est refusé avant même la lecture : le stockage n'accepte
    # que des chemins logiques sous un scope documentaire connu.
    assert "scope documentaire" in sortie["error"]["message"]
