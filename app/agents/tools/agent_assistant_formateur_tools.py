"""Tools de ``agent_assistant_formateur`` (clinrules 04, refactor outils).

Lieu unique du périmètre outils de l'assistant formateur. Matrice d'accès
(validée) :

- **Read/Write** : ``Beneficiaire`` (création/modification sous HITL),
  ``Document`` rattaché à une mission (génération sous HITL) ;
- **Lecture seule** : ``Equipe`` d'une mission (via affectations),
  ``Mission``, ``Lot`` d'une mission (via offre), ``Offre`` d'une mission.

Aperçu d'import Excel : aucune écriture bénéficiaire — l'aperçu distingue
lignes valides, incomplètes et ignorées ; l'import définitif est un tool
séparé soumis à HITL.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.toolkit import AgentTools, OperationSpec, serialiser_entite
from app.application.services import (
    DocumentService,
    ImportBeneficiairesService,
    MissionService,
)

AGENT_ID = "agent_assistant_formateur"
TASK_TYPE = "preparer_documents_mission"

NOMS_TOOLS: tuple[str, ...] = (
    "inspect_beneficiaires_xlsx",
    "preview_import_beneficiaires",
    "import_beneficiaires",
    "list_mission_documents",
    "get_document_structure",
    "read_document_range",
    "generer_document",
    "generer_document_html",
    "fill_template",
    "get_mission",
    "list_mission_sessions",
    "list_session_beneficiaires",
    "search_beneficiaires",
    "get_mission_offre",
    "get_mission_lots",
    "list_mission_equipe",
    "langsearch_web_search",
)


class DocumentIdInput(BaseModel):
    document_id: UUID


class PreviewImportInput(BaseModel):
    document_id: UUID
    sheet: str | None = None
    max_lignes: int = Field(default=50, ge=1, le=200)
    session_id: UUID | None = None


class ImportBeneficiairesInput(BaseModel):
    """Import définitif (HITL) : uniquement les lignes valides de l'aperçu.

    ``session_id`` est optionnel : sans lui, l'import enrichit seulement le
    référentiel des bénéficiaires. Avec lui, les personnes sont aussi inscrites
    à la session — c'est l'usage courant depuis la page Session.
    """

    document_id: UUID
    sheet: str | None = None
    max_lignes: int = Field(default=200, ge=1, le=500)
    session_id: UUID | None = None


class MissionDocumentsInput(BaseModel):
    mission_id: UUID


class MissionIdInput(BaseModel):
    mission_id: UUID


class SessionIdInput(BaseModel):
    session_id: UUID


class BeneficiaireSearchInput(BaseModel):
    recherche: str | None = Field(default=None, max_length=255)
    limite: int = Field(default=20, ge=1, le=100)


_CHAMPS_MISSION = ("id", "reference", "titre", "statut", "lieu", "date_debut", "date_fin")
_CHAMPS_SESSION = ("id", "mission_id", "date_debut", "date_fin", "lieu", "theme", "statut")
_CHAMPS_BENEFICIAIRE = ("id", "nom", "prenom", "organisation_origine", "identifiant_externe")


def _inspect_xlsx(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return ImportBeneficiairesService(session, actor_id=AGENT_ID).inspecter(
        UUID(str(payload["document_id"]))
    )


def _preview_import(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Aperçu uniquement : aucune écriture bénéficiaire.

    Délègue au service partagé — l'agent et la page Session appliquent donc
    exactement la même règle de classement des lignes.
    """
    entree = PreviewImportInput.model_validate(payload)
    apercu = ImportBeneficiairesService(session, actor_id=AGENT_ID).preparer(
        entree.document_id,
        sheet=entree.sheet,
        max_lignes=entree.max_lignes,
        session_id=entree.session_id,
    )
    return {
        **apercu.en_donnees(),
        "requires_approval": True,
        "message": "Aperçu seulement. Aucun bénéficiaire n'a été créé.",
    }


def _import_beneficiaires(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Import définitif des lignes **valides** (HITL) : nom + prénom requis.

    Délègue au même service que la route REST ; aucune déduplication
    silencieuse (règle clinrules 04) — les doublons sont signalés, pas fusionnés.
    """
    entree = ImportBeneficiairesInput.model_validate(payload)
    resultat = ImportBeneficiairesService(session, actor_id=AGENT_ID).importer(
        entree.document_id,
        session_id=entree.session_id,
        sheet=entree.sheet,
        max_lignes=entree.max_lignes,
    )
    return {
        **resultat.en_donnees(),
        "requires_approval": True,
        "message": (
            "Import exécuté sous votre approbation. Les doublons sont signalés, "
            "jamais fusionnés."
        ),
    }


def _list_mission_documents(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    service = DocumentService(session, actor_id=AGENT_ID)
    documents = service.lister_pour("mission_id", UUID(str(payload["mission_id"])))
    return {
        "documents": [
            {
                "document_id": str(doc.id),
                "nom": doc.nom,
                "type_document": doc.type_document,
                "version": doc.version,
                "statut": doc.statut,
            }
            for doc in documents
        ]
    }


def _get_mission(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    mission = MissionService(session).obtenir(UUID(str(payload["mission_id"])))
    return serialiser_entite(mission, *_CHAMPS_MISSION)


def _list_mission_sessions(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    from app.domain.execution import SessionFormation

    sessions = list(
        session.scalars(
            select(SessionFormation)
            .where(SessionFormation.mission_id == UUID(str(payload["mission_id"])))
            .order_by(SessionFormation.date_debut)
        )
    )
    return {"sessions": [serialiser_entite(s, *_CHAMPS_SESSION) for s in sessions]}


def _list_session_beneficiaires(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Bénéficiaires d'une session et leurs pointages **datés** (règle 24/09).

    La présence n'est plus une valeur unique par personne : une session de cinq
    jours se pointe cinq fois. L'outil rend donc la liste des pointages
    (``date``, ``presence``, heures), pas un statut — un agent qui voudrait
    résumer devrait choisir une date, et ce choix lui appartient.
    """
    from app.domain.execution import Beneficiaire, Participation, Presence

    session_id = UUID(str(payload["session_id"]))
    lignes = session.execute(
        select(Participation, Beneficiaire)
        .join(Beneficiaire, Participation.beneficiaire_id == Beneficiaire.id)
        .where(Participation.session_id == session_id)
    ).all()
    pointages = session.scalars(
        select(Presence)
        .join(Participation, Presence.participation_id == Participation.id)
        .where(Participation.session_id == session_id)
        .order_by(Presence.date)
    )
    par_participation: dict[Any, list[dict[str, Any]]] = {}
    for presence in pointages:
        par_participation.setdefault(presence.participation_id, []).append(
            {
                "date": presence.date.isoformat(),
                "presence": presence.presence,
                "heure_arrivee": (
                    presence.heure_arrivee.isoformat() if presence.heure_arrivee else None
                ),
                "heure_depart": (
                    presence.heure_depart.isoformat() if presence.heure_depart else None
                ),
            }
        )
    return {
        "beneficiaires": [
            {
                **serialiser_entite(b, *_CHAMPS_BENEFICIAIRE),
                "pointages": par_participation.get(p.id, []),
            }
            for p, b in lignes
        ]
    }


def _search_beneficiaires(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    from app.domain.execution import Beneficiaire

    filtres = BeneficiaireSearchInput.model_validate(payload)
    stmt = select(Beneficiaire).order_by(Beneficiaire.nom, Beneficiaire.prenom)
    if filtres.recherche:
        motif = f"%{filtres.recherche.strip()}%"
        stmt = stmt.where(Beneficiaire.nom.ilike(motif) | Beneficiaire.prenom.ilike(motif))
    beneficiaires = list(session.scalars(stmt.limit(filtres.limite)))
    return {
        "beneficiaires": [
            serialiser_entite(b, *_CHAMPS_BENEFICIAIRE) for b in beneficiaires
        ]
    }


def _get_mission_offre(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Offres d'une mission (lecture seule) : technique et/ou financière."""
    mission = MissionService(session).obtenir(UUID(str(payload["mission_id"])))
    if not mission.offres:
        return {
            "offres": [],
            "message": "Mission sans offre rattachée (prestation directe).",
        }
    return {
        "offres": [
            {
                "id": str(offre.id),
                "type": offre.type,
                "reference": offre.reference,
                "titre": offre.titre,
                "statut": offre.statut,
                "version": offre.version,
            }
            for offre in mission.offres
        ]
    }


def _get_mission_lots(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Lot(s) de la mission via ses offres (lecture seule)."""
    from app.domain.organization import Lot

    mission = MissionService(session).obtenir(UUID(str(payload["mission_id"])))
    lot_ids = [offre.lot_id for offre in mission.offres]
    if not lot_ids:
        return {"lots": [], "message": "Mission sans offre : aucun lot rattaché."}
    lots = session.scalars(select(Lot).where(Lot.id.in_(lot_ids))).all()
    return {
        "lots": [
            {"id": str(lot.id), "numero": lot.numero, "titre": lot.titre, "zone": lot.zone}
            for lot in lots
        ]
    }


def _list_mission_equipe(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Équipe d'une mission (via affectations, lecture seule)."""
    from app.domain.execution import AffectationEquipe, Equipe

    lignes = session.execute(
        select(AffectationEquipe, Equipe)
        .join(Equipe, AffectationEquipe.equipe_id == Equipe.id)
        .where(AffectationEquipe.mission_id == UUID(str(payload["mission_id"])))
    ).all()
    return {
        "equipe": [
            {
                "affectation_id": str(a.id),
                "equipe_id": str(e.id),
                "nom": e.nom,
                "prenom": e.prenom,
                "role_dans_mission": a.role_dans_mission,
                "statut": a.statut,
            }
            for a, e in lignes
        ]
    }


def construire_tools_assistant_formateur(
    definition: Any,
    session_factory: Any,
    *,
    extra_tools: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    """Tools de l'assistant formateur : bénéficiaires + documents + lectures mission."""
    from app.agents.outillage import tools_documents_pour, tools_scripts_pour

    kit = AgentTools(definition, session_factory=session_factory)
    kit.enregistrer(OperationSpec(
        name="inspect_beneficiaires_xlsx",
        description="Structure d'un classeur bénéficiaires.",
        input_schema=DocumentIdInput,
        run=_inspect_xlsx,
    ))
    kit.enregistrer(OperationSpec(
        name="preview_import_beneficiaires",
        description="Aperçu d'import (aucune écriture).",
        input_schema=PreviewImportInput,
        run=_preview_import,
    ))
    kit.enregistrer(OperationSpec(
        name="import_beneficiaires",
        description=(
            "Import définitif des lignes valides (nom+prénom requis) sous "
            "approbation humaine. Doublons signalés, jamais fusionnés."
        ),
        input_schema=ImportBeneficiairesInput,
        mutation=True,
        run=_import_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="list_mission_documents",
        description="Documents rattachés à une mission.",
        input_schema=MissionDocumentsInput,
        run=_list_mission_documents,
    ))
    kit.enregistrer(OperationSpec(
        name="get_mission",
        description="Fiche mission (dates, lieu, statut).",
        input_schema=MissionIdInput,
        run=_get_mission,
    ))
    kit.enregistrer(OperationSpec(
        name="list_mission_sessions",
        description="Sessions de la mission.",
        input_schema=MissionIdInput,
        run=_list_mission_sessions,
    ))
    kit.enregistrer(OperationSpec(
        name="list_session_beneficiaires",
        description="Bénéficiaires inscrits à une session (+ présence).",
        input_schema=SessionIdInput,
        run=_list_session_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="search_beneficiaires",
        description="Bénéficiaires par nom/prénom (données minimisées).",
        input_schema=BeneficiaireSearchInput,
        run=_search_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="get_mission_offre",
        description="Offre de formation rattachée à la mission (lecture).",
        input_schema=MissionIdInput,
        run=_get_mission_offre,
    ))
    kit.enregistrer(OperationSpec(
        name="get_mission_lots",
        description="Lot(s) de la mission via son offre (lecture).",
        input_schema=MissionIdInput,
        run=_get_mission_lots,
    ))
    kit.enregistrer(OperationSpec(
        name="list_mission_equipe",
        description="Équipe affectée à la mission (lecture).",
        input_schema=MissionIdInput,
        run=_list_mission_equipe,
    ))

    from app.tools.web import attacher_web_search

    extras = (
        tuple(extra_tools)
        + tools_documents_pour(definition, session_factory)
        + tools_scripts_pour(definition)
    )
    return attacher_web_search(definition, tuple(kit.construire()) + extras)


__all__ = [
    "AGENT_ID",
    "NOMS_TOOLS",
    "TASK_TYPE",
    "construire_tools_assistant_formateur",
]
