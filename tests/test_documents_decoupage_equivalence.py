"""Équivalence fonctionnelle des tools documentaires après découpage (phase 1).

Les lots 1 à 3 ont déplacé le code des tools (fichier unique devenu paquet,
fermetures d'infrastructure remplacées par un contexte, handlers répartis par
famille) sans changer leur comportement. Les empreintes de schéma prouvent que
la surface déclarée est identique ; elles ne prouvent pas que les corps
s'exécutent encore.

Ce module comble ce trou avec trois parcours réels, ceux qui traversent le plus
de familles à la fois :

- **dépôt** : ``generer_document`` produit des octets et les dépose en statut
  ``proposed`` (frontière HITL), avec un document réellement écrit en base ;
- **lecture** : ``lire_document`` relit ce document par le service et rend un
  aperçu de texte extrait du fichier ;
- **génération** : ``generer_document_professionnel`` passe par le moteur
  documentaire et dépose le livrable.

Chaque parcours vérifie un effet en base et non seulement un code de retour :
un handler qui renverrait ``ok`` sans écrire passerait un test trop faible.

Ce que ce module ne fait pas : il ne compare pas à une implémentation
antérieure (elle n'existe plus). L'équivalence de comportement repose sur deux
preuves distinctes : l'empreinte des 29 outils (lot 1 puis lot 2 puis lot 3,
identique à chaque fois) et ces parcours d'exécution.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generaliste")
POLICY_DOC = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
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


def _tools(service: DocumentService) -> dict[str, Any]:
    outils = build_document_tools(
        identite=IDENTITE,
        policy=POLICY_DOC,
        service_factory=lambda: service,
    )
    return {outil.name: outil for outil in outils}


def test_les_29_outils_sont_tous_construits(service: DocumentService) -> None:
    """Le découpage par famille n'a perdu aucun outil au passage.

    28 depuis la suppression de l'alias ``read_document`` : deux noms pour le
    même geste laissaient le modèle choisir au hasard (audit du 13/11).
    """
    outils = _tools(service)
    assert len(outils) == 28
    attendus = {
        "generer_document",
        "generer_document_professionnel",
        "lire_document",
        "lire_document",
        "analyze_document_reference",
        "valider_document",
        "chercher_dans_document",
        "lire_element_document",
        "lister_modeles_documents",
        "generer_image",
    }
    assert attendus <= set(outils)


def test_depot_ecrit_un_document_proposed(
    service: DocumentService, organisation: Organisation
) -> None:
    """Parcours de dépôt : les octets sont produits et la fiche est en base."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        {
            "format": "docx",
            "nom_fichier": "rapport_atelier",
            "type_document": "rapport",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": "agent_generaliste",
            "titre": "Rapport d'atelier",
            "paragraphes": ["Synthèse des travaux."],
        }
    )

    assert sortie["statut"] == "proposed"
    document = service.obtenir(UUID(sortie["document_id"]))
    assert document.statut == "proposed"
    assert document.nom.endswith(".docx")
    # le fichier existe réellement sur le stockage, pas seulement en base
    nom_fichier, chemin = service.telecharger(document.id)
    assert chemin.exists()
    assert chemin.stat().st_size > 0


def test_lecture_relit_le_document_depose(
    service: DocumentService, organisation: Organisation
) -> None:
    """Parcours de lecture : le texte est extrait du fichier écrit par le dépôt."""
    outils = _tools(service)
    depot = outils["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note_lecture",
            "type_document": "rapport",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": "agent_generaliste",
            "titre": "Note de lecture",
            "paragraphes": ["Contenu relu par l'outil de lecture."],
        }
    )
    assert depot.get("statut") == "proposed"

    lecture = outils["lire_document"].invoke({"document_id": depot["document_id"]})

    assert lecture["document_id"] == depot["document_id"]
    assert lecture["texte_lisible"] is True
    assert "Contenu relu" in lecture["apercu_texte"]
    assert lecture["version"] == 1


def test_generation_professionnelle_depose_un_livrable(
    service: DocumentService, organisation: Organisation
) -> None:
    """Parcours de génération : le moteur documentaire produit et dépose."""
    outil = _tools(service)["generer_document_professionnel"]
    sortie = outil.invoke(
        {
            "format": "docx",
            "nom_fichier": "compte_rendu",
            "type_document": "rapport",
            "titre": "Compte rendu de séance",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": "agent_generaliste",
            "contenu_html": (
                '<div class="h1">Compte rendu</div>'
                '<p class="p">Séance de travail du 11 novembre.</p>'
            ),
        }
    )

    assert sortie.get("statut") == "proposed", sortie
    document = service.obtenir(UUID(sortie["document_id"]))
    assert document.statut == "proposed"
    assert document.nom.endswith(".docx")
