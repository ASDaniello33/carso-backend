"""Parcours réel qui bouclait : fiche de présence demandée depuis une session.

**Le parcours** (16/11/2026) : l'utilisateur ouvre une session et demande une
fiche de présence. L'agent ne reçoit qu'un ``session_id`` ; pour copier le
gabarit de l'organisation il lui faut la mission puis l'organisation. Sans
``get_session``, il n'avait aucun chemin et répétait le même raisonnement.

Ce test joue le parcours **de bout en bout, sur les outils réellement montés**
(``construire_tools_assistant_formateur``) et avec un vrai DOCX en stockage :

1. ``get_session(session_id)`` → ``mission_id`` et ``organisation`` ;
2. ``lister_modeles_documents(organisation_id)`` → les gabarits de cette
   organisation ;
3. ``copier_modele_document(ancre="session", entity_id, modele_id)`` → la copie
   déposée **sur la session**.

Il vérifie aussi le **repli** : quand l'organisation n'a pas de modèle du type
demandé, la copie est refusée (aucun document n'est créé, un gabarit d'offre
n'est jamais recyclé en fiche de présence) et le message renvoie vers
``generer_document_professionnel``.

Si l'agent perd le chemin d'identifiants (``get_session`` retiré du contrat ou
du montage), le premier test échoue : les étapes suivantes n'ont plus
d'``organisation_id``.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from uuid import UUID

import pytest
from docx import Document as DocxDocument
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.assistant_formateur import DEFINITION
from app.agents.tools.agent_assistant_formateur_tools import (
    construire_tools_assistant_formateur,
)
from app.application.dto import DocumentUploadInput
from app.application.services.document_service import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.core.config import get_settings
from app.domain.base import Base
from app.domain.document import Document
from app.domain.enums import StatutMission
from app.domain.execution import Mission, SessionFormation
from app.domain.organization import Organisation

#: Outils que le parcours emprunte, dans l'ordre. Le premier est celui dont
#: l'absence faisait boucler l'agent.
CHEMIN = ("get_session", "lister_modeles_documents", "copier_modele_document")


def _docx_modele() -> bytes:
    """DOCX minimal mais réel : en-tête, titre, une ligne à remplir."""
    document = DocxDocument()
    document.sections[0].header.paragraphs[0].text = "CARSO — Fiche de présence"
    document.add_heading("FICHE DE PRESENCE", level=0)
    document.add_paragraph("Nom / prénom : ")
    document.add_paragraph("Date / signature : ")
    tampon = io.BytesIO()
    document.save(tampon)
    return tampon.getvalue()


@pytest.fixture
def fabrique(tmp_path, monkeypatch) -> Iterator[sessionmaker]:
    """Base partagée entre sessions (le harnais ouvre la sienne par outil)."""
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path / "stockage"))
    moteur = create_engine(
        f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(moteur)
    yield sessionmaker(bind=moteur, expire_on_commit=False)
    moteur.dispose()


def _organisation_mission_session(fabrique: sessionmaker, nom: str):
    with fabrique() as session:
        organisation = Organisation(nom=nom)
        session.add(organisation)
        session.flush()
        mission = Mission(
            organisation_id=organisation.id,
            reference="M-PRESENCE-01",
            titre="Mission présence",
            statut=StatutMission.PLANIFIE.value,
        )
        session.add(mission)
        session.flush()
        session_formation = SessionFormation(mission_id=mission.id, theme="Jour 1")
        session.add(session_formation)
        session.commit()
        return organisation.id, mission.id, session_formation.id


def _deposer_modele(fabrique: sessionmaker, organisation_id, type_modele: str) -> str:
    """Dépose le DOCX en ``template`` et l'enregistre au registre des modèles."""
    with fabrique() as session:
        documents = DocumentService(session, actor_id="sonde")
        document = documents.enregistrer_document(
            DocumentUploadInput(
                type_document="template",
                nom=f"modele-{type_modele}.docx",
                stream=io.BytesIO(_docx_modele()),
                created_by="sonde",
                organisation_id=organisation_id,
            )
        )
        modele = ModeleDocumentService(session).enregistrer_modele(
            organisation_id,
            nom=f"modele-{type_modele}.docx",
            type_document=type_modele,
            document_template_id=document.id,
            registered_by="sonde",
        )
        session.commit()
        return str(modele.id)


def _outils(fabrique: sessionmaker) -> dict[str, object]:
    return {
        outil.name: outil
        for outil in construire_tools_assistant_formateur(DEFINITION, fabrique)
    }


def test_le_chemin_d_identifiants_est_monte() -> None:
    """Sans ``get_session``, la session ne mène ni à la mission ni au modèle."""
    moteur = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(moteur)
    try:
        outils = _outils(sessionmaker(bind=moteur, expire_on_commit=False))
    finally:
        moteur.dispose()

    manquants = [nom for nom in CHEMIN if nom not in outils]
    assert not manquants, f"outils absents du montage : {manquants}"
    assert "get_session" in DEFINITION.tools


def test_parcours_session_vers_copie_du_modele(fabrique: sessionmaker) -> None:
    """Session → get_session → organisation → modèles → copie ancrée."""
    organisation_id, mission_id, session_id = _organisation_mission_session(
        fabrique, "Client Présence"
    )
    modele_id = _deposer_modele(fabrique, organisation_id, "fiche_presence")
    outils = _outils(fabrique)

    # 1. L'agent ne reçoit que le session_id : la fiche donne le reste.
    lu = outils["get_session"].invoke({"session_id": str(session_id)})
    assert lu["ok"] is True, lu
    assert lu["mission_id"] == str(mission_id)
    assert lu["mission"]["id"] == str(mission_id)
    assert lu["organisation"] == {"id": str(organisation_id), "nom": "Client Présence"}

    # 2. Le catalogue de l'organisation — l'étape que le prompt n'impose plus
    #    avant la copie, mais qui reste utile pour choisir un modèle précis.
    liste = outils["lister_modeles_documents"].invoke(
        {"organisation_id": lu["organisation"]["id"]}
    )
    assert liste["ok"] is True, liste
    assert [(m["modele_id"], m["type_document"]) for m in liste["modeles"]] == [
        (modele_id, "fiche_presence")
    ]

    # 3. La copie, ancrée sur la session (le moteur y résout l'organisation).
    copie = outils["copier_modele_document"].invoke(
        {
            "ancre": "session",
            "entity_id": str(session_id),
            "modele_id": liste["modeles"][0]["modele_id"],
            "type_document": "fiche_presence",
        }
    )
    assert copie["ok"] is True, copie
    assert copie["modele"]["modele_id"] == modele_id

    # Le document de travail est bien rattaché à la session, prêt à être édité.
    with fabrique() as session:
        document = session.get(Document, UUID(str(copie["document_id"])))
        assert document is not None
        assert document.session_id == session_id
        # Le type de dépôt demandé est celui de la pièce attendue sur la session.
        assert document.type_document == "fiche_presence"


def test_repli_sans_modele_de_fiche_de_presence(fabrique: sessionmaker) -> None:
    """Organisation avec un gabarit d'offre seulement : refus explicite.

    Un gabarit d'offre ne devient jamais une fiche de présence : la copie
    échoue, aucun document n'est déposé sur la session, et le message renvoie
    vers la génération professionnelle.
    """
    organisation_id, _mission_id, session_id = _organisation_mission_session(
        fabrique, "Client Offres"
    )
    _deposer_modele(fabrique, organisation_id, "modele_offre_technique")
    outils = _outils(fabrique)

    lu = outils["get_session"].invoke({"session_id": str(session_id)})
    liste = outils["lister_modeles_documents"].invoke(
        {"organisation_id": lu["organisation"]["id"]}
    )
    assert [m["type_document"] for m in liste["modeles"]] == ["modele_offre_technique"]

    repli = outils["copier_modele_document"].invoke(
        {
            "ancre": "session",
            "entity_id": str(session_id),
            "type_document": "fiche_presence",
        }
    )

    assert repli["ok"] is False, repli
    message = repli["error"]["message"]
    assert "generer_document_professionnel" in message
    assert "fiche_presence" in message
    assert "ne s'applique pas" in message  # un type ne se recycle pas

    # Rien n'a été copié : la session n'a aucun document de travail.
    with fabrique() as session:
        assert (
            session.query(Document).filter(Document.session_id == session_id).count() == 0
        )
