"""`corriger_document` / `valider_document` sur un `document_id`.

Ces deux outils transportaient le `DocumentSpec` complet dans leur schéma (le
vocabulaire du spec y était resérialisé : ~80 k caractères de définitions
dupliquées, envoyées au modèle à chaque tour). Le spec est désormais conservé
avec la source technique du document déposé : les outils le relisent, et
l'agent n'envoie qu'un identifiant.

Ce fichier vérifie le comportement, pas la taille (l'audit s'en charge) :

- génération structurée → le spec est conservé ;
- validation par `document_id` seulement ;
- correction ciblée par `document_id` seulement, puis nouvelle version qui
  conserve elle aussi le spec (deuxième correction sans jamais le renvoyer) ;
- erreur actionnable quand le document n'a pas de spec (livrable professionnel).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.documents.spec import DocumentSpec, Metadonnees, Paragraphe, Tableau, Titre
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset(
        {"document_read", "document_propose"}
    )
)


def _spec() -> DocumentSpec:
    return DocumentSpec(
        metadata=Metadonnees(titre="Offre technique — Lot 3", reference="OT-2026-014"),
        blocs=[
            Titre(niveau=1, texte="1. Budget"),
            Tableau(
                titre_tableau="Budget prévisionnel",
                entete=["Poste", "Quantité", "Montant"],
                lignes=[["Formation", "10", "4 000 000"], ["TOTAL", "", "4 000 000"]],
                total_ligne=1,
            ),
            Paragraphe(texte="Le budget couvre la totalité du lot."),
        ],
    )


@pytest.fixture
def session() -> Iterator[Session]:
    moteur = create_engine("sqlite://")
    Base.metadata.create_all(moteur)
    fabrique = sessionmaker(bind=moteur)
    with fabrique() as active:
        yield active
    moteur.dispose()


@pytest.fixture
def outils(session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Outils documentaires, stockage et sources techniques isolés dans tmp."""
    service = DocumentService(
        session,
        storage=LocalDocumentStorage(tmp_path / "carso", max_bytes=8 * 1024 * 1024),
        actor_id="test",
    )
    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=IDENTITE,
            policy=POLICY,
            service_factory=lambda: service,
            dossier_sources=tmp_path / "sources",
        )
    }


@pytest.fixture
def organisation(session: Session) -> Organisation:
    org = Organisation(nom="Client Correction")
    session.add(org)
    session.flush()
    return org


def _generer(outils: dict[str, Any], organisation: Organisation) -> dict[str, Any]:
    sortie = outils["generer_document"].invoke(
        {
            "document_spec": _spec().model_dump(mode="json"),
            "format": "docx",
            "nom_fichier": "offre_lot3",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
        }
    )
    assert sortie["ok"] is True, sortie
    return sortie


class TestSpecConserve:
    def test_valider_par_document_id_seul(
        self, outils: dict[str, Any], organisation: Organisation
    ) -> None:
        depot = _generer(outils, organisation)

        sortie = outils["valider_document"].invoke(
            {"document_id": depot["document_id"], "profil": "offre_technique"}
        )

        # Le spec a bien été relu : la validation rend un verdict structuré
        # (ici non conforme — le spec de test n'a pas toutes les sections).
        assert sortie["document_id"] == depot["document_id"]
        assert set(sortie["validation"]) >= {"ok", "structure", "contenu", "style"}
        assert sortie["findings"], "la validation doit rendre ses constats"

    def test_correction_par_document_id_seul_cree_une_version(
        self, outils: dict[str, Any], organisation: Organisation
    ) -> None:
        depot = _generer(outils, organisation)

        sortie = outils["corriger_document"].invoke(
            {
                "document_id": depot["document_id"],
                "operations": [
                    {
                        "type": "cellule",
                        "bloc": 1,
                        "ligne": 2,
                        "colonne": 2,
                        "cellule": "4 200 000",
                    }
                ],
                "propose_par": IDENTITE.agent_id,
            }
        )

        assert sortie["ok"] is True, sortie
        assert UUID(sortie["document_id"]) != UUID(depot["document_id"])
        assert sortie["version"] == depot["version"] + 1
        assert sortie["operations_appliquees"]

        # La nouvelle version porte son propre spec : deuxième correction, sans
        # jamais renvoyer le document entier.
        encore = outils["corriger_document"].invoke(
            {
                "document_id": sortie["document_id"],
                "operations": [
                    {
                        "type": "cellule",
                        "bloc": 1,
                        "ligne": 2,
                        "colonne": 2,
                        "cellule": "4 300 000",
                    }
                ],
                "propose_par": IDENTITE.agent_id,
            }
        )
        assert encore["ok"] is True, encore
        assert encore["version"] == sortie["version"] + 1

    def test_le_spec_complet_reste_accepte(
        self, outils: dict[str, Any], organisation: Organisation
    ) -> None:
        """Voie historique : un document jamais déposé garde son spec inline."""
        sortie = outils["corriger_document"].invoke(
            {
                "document_spec": _spec().model_dump(mode="json"),
                "operations": [
                    {
                        "type": "cellule",
                        "bloc": 1,
                        "ligne": 2,
                        "colonne": 2,
                        "cellule": "4 500 000",
                    }
                ],
                "format": "docx",
                "nom_fichier": "offre_sans_depot",
                "type_document": "offre",
                "ancre": "organisation",
                "entity_id": str(organisation.id),
                "propose_par": IDENTITE.agent_id,
            }
        )
        assert sortie["ok"] is True, sortie

    def test_ni_id_ni_spec_donne_une_erreur_actionnable(self, outils: dict[str, Any]) -> None:
        sortie = outils["valider_document"].invoke({})

        assert sortie["ok"] is False
        assert "document_id" in sortie["error"]["message"]
        assert "document_spec" in sortie["error"]["message"]

    def test_un_livrable_sans_spec_est_refuse_clairement(
        self, outils: dict[str, Any], organisation: Organisation
    ) -> None:
        """Un document professionnel n'a pas de spec : le message dit la voie."""
        document = outils["generer_document_professionnel"].invoke(
            {
                "titre": "Note de cadrage",
                "contenu_html": "<h1>Note</h1><p>Contenu.</p>",
                "format": "docx",
                "nom_fichier": "note_cadrage",
                "type_document": "rapport",
                "ancre": "organisation",
                "entity_id": str(organisation.id),
                "propose_par": IDENTITE.agent_id,
            }
        )
        assert document["ok"] is True, document

        sortie = outils["corriger_document"].invoke(
            {
                "document_id": document["document_id"],
                "operations": [
                    {"type": "document", "champs": {"titre": "Note corrigée"}}
                ],
                "propose_par": IDENTITE.agent_id,
            }
        )

        assert sortie["ok"] is False
        assert "document_spec" in sortie["error"]["message"]
        assert "modifier_section_document" in sortie["error"]["message"]
