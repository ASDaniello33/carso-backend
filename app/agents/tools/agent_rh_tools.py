"""Tools de ``agent_rh`` (clinrules 03, refactor outils).

Lieu unique du périmètre outils de l'agent RH. Matrice d'accès (validée) :

- **Read/Write (écritures sous HITL)** : ``Document``, ``Equipe``, ``Mission`` ;
- **Lecture seule** : les autres tables.

Le rôle vit dans l'affectation, jamais sur la personne. Aucun diplôme,
langue ou disponibilité inventé : un score d'adéquation est explicable
(présence de profil / CV / rôle déjà tenu), jamais une précision scientifique.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.consultation import (
    rechercher_appels_a_proposition,
    rechercher_documents,
    rechercher_organisations,
)
from app.agents.toolkit import (
    AgentTools,
    OperationSpec,
    PageSearchInput,
    serialiser_entite,
)
from app.agents.tools.agent_generaliste_tools import (
    BeneficiaireSearchInput,
    LotSearchInput,
    OffreSearchInput,
    SessionSearchInput,
    rechercher_beneficiaires,
    rechercher_lots,
    rechercher_offres,
    rechercher_sessions,
)
from app.application.dto import AffectationProposee
from app.application.services import AffectationService, EquipeService, MissionService
from app.domain.enums import SourceAffectation, TypeDocument
from app.domain.execution import Mission
from app.infrastructure.repositories import AffectationEquipeRepository, DocumentRepository

AGENT_ID = "agent_rh"
TASK_TYPE = "propose_team_assignment"

NOMS_TOOLS: tuple[str, ...] = (
    "search_equipes",
    "get_equipe",
    "list_equipe_cv",
    "get_mission",
    "list_mission_assignments",
    "search_missions",
    "create_assignment_proposal",
    "propose_equipe_update",
    "propose_mission_update",
    "search_organisations",
    "search_appels_a_proposition",
    "search_lots",
    "search_offres",
    "search_sessions",
    "search_beneficiaires",
    "search_documents",
    "lire_document",
    "read_document",
    "get_document_structure",
    "generer_document",
    "generer_document_html",
)


class EquipeIdInput(BaseModel):
    equipe_id: UUID


class MissionIdInput(BaseModel):
    mission_id: UUID


class MissionSearchInput(PageSearchInput):
    """Missions par statut (lecture toutes missions, périmètre RH)."""

    statut: str | None = Field(default=None, max_length=50)


class AssignmentProposalInput(BaseModel):
    mission_id: UUID
    equipe_id: UUID
    role_dans_mission: str = Field(min_length=1, max_length=100)
    justification: str = Field(min_length=1, max_length=2000)


class EquipeUpdateInput(BaseModel):
    """Mise à jour du profil d'une personne du vivier : proposition (HITL)."""

    equipe_id: UUID
    profil: str | None = Field(default=None, max_length=4000)
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)


class MissionUpdateInput(BaseModel):
    """Mise à jour d'une mission : proposition (HITL).

    Le lieu d'exécution est une **référence** (``lieux``), pas un texte libre :
    le lot d'un appel n'est pas un lieu, et un lieu inventé ne doit jamais être
    écrit.
    """

    mission_id: UUID
    titre: str | None = Field(default=None, min_length=1, max_length=255)
    lieu_id: UUID | None = Field(
        default=None, description="Référence d'un lieu existant (jamais un texte libre)."
    )
    description: str | None = Field(default=None, max_length=4000)


_CHAMPS_MISSION = (
    "id",
    "reference",
    "titre",
    "statut",
    "lieu_id",
    "date_debut",
    "date_fin",
)


def _get_equipe(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    equipe = EquipeService(session).obtenir(UUID(str(payload["equipe_id"])))
    return {
        "id": str(equipe.id),
        "nom": equipe.nom,
        "prenom": equipe.prenom,
        "profil": equipe.profil,
        "statut": equipe.statut,
        "email": equipe.email,
    }


def _list_cv(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    docs = DocumentRepository(session).list_for_equipe(UUID(str(payload["equipe_id"])))
    cvs = [d for d in docs if d.type_document == TypeDocument.CV.value]
    return {
        "equipe_id": str(payload["equipe_id"]),
        "cv": [
            {
                "document_id": str(d.id),
                "nom": d.nom,
                "version": d.version,
                "statut": d.statut,
            }
            for d in cvs
        ],
        "nb_cv": len(cvs),
    }


def _get_mission(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    mission = MissionService(session).obtenir(UUID(str(payload["mission_id"])))
    return serialiser_entite(mission, *_CHAMPS_MISSION)


def _search_missions(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    filtres = MissionSearchInput.model_validate(payload)
    stmt = select(Mission).order_by(Mission.date_debut.desc())
    if filtres.statut is not None:
        stmt = stmt.where(Mission.statut == filtres.statut)
    if filtres.recherche:
        motif = f"%{filtres.recherche.strip()}%"
        stmt = stmt.where(Mission.titre.ilike(motif) | Mission.reference.ilike(motif))
    missions = list(session.scalars(stmt.limit(filtres.limite)))
    return {"missions": [serialiser_entite(m, *_CHAMPS_MISSION) for m in missions]}


def _list_assignments(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    items = AffectationEquipeRepository(session).list_for_mission(
        UUID(str(payload["mission_id"]))
    )
    return {
        "affectations": [
            {
                "id": str(a.id),
                "equipe_id": str(a.equipe_id),
                "role_dans_mission": a.role_dans_mission,
                "statut": a.statut,
                "source": a.source_affectation,
            }
            for a in items
        ]
    }


def _score(session: Session, equipe_id: UUID, role: str) -> dict[str, Any]:
    """Score explicable, déterministe, sans invention."""
    equipe = EquipeService(session).obtenir(equipe_id)
    cvs = [
        d
        for d in DocumentRepository(session).list_for_equipe(equipe_id)
        if d.type_document == TypeDocument.CV.value
    ]
    criteres = {
        "profil_renseigne": bool((equipe.profil or "").strip()),
        "cv_present": bool(cvs),
        "role_dans_profil": role.lower() in (equipe.profil or "").lower(),
    }
    points = sum(1 for v in criteres.values() if v)
    return {
        "score": points,
        "score_max": 3,
        "criteres": criteres,
        "limite": "Score indicatif à partir des données enregistrées uniquement.",
    }


def _create_proposal(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    entree = AssignmentProposalInput.model_validate(payload)
    score = _score(session, entree.equipe_id, entree.role_dans_mission)
    affectation = AffectationService(session, actor_id=AGENT_ID).proposer(
        AffectationProposee(
            mission_id=entree.mission_id,
            equipe_id=entree.equipe_id,
            role_dans_mission=entree.role_dans_mission,
            proposed_by=AGENT_ID,
            source=SourceAffectation.AI_PROPOSAL.value,
        )
    )
    return {
        "affectation_id": str(affectation.id),
        "statut": affectation.statut,
        "requires_approval": True,
        "justification": entree.justification,
        **score,
        "message": "Proposition enregistrée. Non officielle tant qu'elle n'est pas approuvée.",
    }


def _propose_equipe_update(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique une mise à jour du vivier après HITL."""
    from app.application.dto import EquipeUpdateInput as EquipeUpdateDto

    entree = EquipeUpdateInput.model_validate(payload)
    equipe = EquipeService(session, actor_id=AGENT_ID).modifier(
        entree.equipe_id,
        EquipeUpdateDto(
            profil=entree.profil,
            email=entree.email,
            telephone=entree.telephone,
        ),
    )
    return {
        "equipe_id": str(equipe.id),
        "profil": equipe.profil,
        "email": equipe.email,
        "telephone": equipe.telephone,
        "requires_approval": True,
        "message": "Fiche vivier mise à jour après votre approbation.",
    }


def _propose_mission_update(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique une mise à jour de mission après HITL."""
    entree = MissionUpdateInput.model_validate(payload)
    mission = MissionService(session, actor_id=AGENT_ID).modifier(
        entree.mission_id,
        titre=entree.titre,
        lieu_id=entree.lieu_id,
        description=entree.description,
    )
    return {
        "mission_id": str(mission.id),
        "titre": mission.titre,
        "lieu_id": str(mission.lieu_id) if mission.lieu_id else None,
        "description": mission.description,
        "requires_approval": True,
        "message": "Mission mise à jour après votre approbation.",
    }


def construire_tools_rh(
    definition: Any,
    session_factory: Any,
    *,
    extra_tools: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    """Tools du RH : vivier + missions + propositions (écritures HITL)."""
    kit = AgentTools(definition, session_factory=session_factory)
    kit.enregistrer(OperationSpec(
        name="search_equipes",
        description="Recherche dans le vivier (nom, prénom, profil).",
        input_schema=PageSearchInput,
        run=_rechercher_equipes_rh,
    ))
    kit.enregistrer(OperationSpec(
        name="get_equipe",
        description="Fiche personne (sans inventer de compétences).",
        input_schema=EquipeIdInput,
        run=_get_equipe,
    ))
    kit.enregistrer(OperationSpec(
        name="list_equipe_cv",
        description="CV rattachés (métadonnées uniquement).",
        input_schema=EquipeIdInput,
        run=_list_cv,
    ))
    kit.enregistrer(OperationSpec(
        name="get_mission",
        description="Fiche mission (exigences, dates, lieu).",
        input_schema=MissionIdInput,
        run=_get_mission,
    ))
    kit.enregistrer(OperationSpec(
        name="search_missions",
        description="Missions par statut ou texte (cibler l'analyse).",
        input_schema=MissionSearchInput,
        run=_search_missions,
    ))
    kit.enregistrer(OperationSpec(
        name="list_mission_assignments",
        description="Affectations déjà posées sur la mission.",
        input_schema=MissionIdInput,
        run=_list_assignments,
    ))
    kit.enregistrer(OperationSpec(
        name="create_assignment_proposal",
        description="Crée une affectation proposed (HITL).",
        input_schema=AssignmentProposalInput,
        mutation=True,
        run=_create_proposal,
    ))
    kit.enregistrer(OperationSpec(
        name="propose_equipe_update",
        description=(
            "Propose une mise à jour de fiche vivier (profil, email, téléphone) : "
            "aucune écriture directe, approbation humaine requise."
        ),
        input_schema=EquipeUpdateInput,
        mutation=True,
        run=_propose_equipe_update,
    ))
    kit.enregistrer(OperationSpec(
        name="propose_mission_update",
        description=(
            "Propose une mise à jour de mission (titre, lieu d'exécution, "
            "description) : lieu_id doit référencer un lieu existant. Aucune "
            "écriture directe, approbation humaine requise."
        ),
        input_schema=MissionUpdateInput,
        mutation=True,
        run=_propose_mission_update,
    ))
    # Autres tables : lecture seule (matrice validée).
    kit.enregistrer(OperationSpec(
        name="search_organisations",
        description="Organisations clientes par nom (lecture seule).",
        input_schema=PageSearchInput,
        run=rechercher_organisations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_appels_a_proposition",
        description="Appels à proposition (organisation, statut, texte) + lots.",
        input_schema=PageSearchInput,
        run=rechercher_appels_a_proposition,
    ))
    kit.enregistrer(OperationSpec(
        name="search_lots",
        description="Lots d'un appel (ou tous) : numéro, titre, zone.",
        input_schema=LotSearchInput,
        run=rechercher_lots,
    ))
    kit.enregistrer(OperationSpec(
        name="search_offres",
        description="Offres de formation (statut, organisation) — lecture seule.",
        input_schema=OffreSearchInput,
        run=rechercher_offres,
    ))
    kit.enregistrer(OperationSpec(
        name="search_sessions",
        description="Sessions de formation — lecture seule.",
        input_schema=SessionSearchInput,
        run=rechercher_sessions,
    ))
    kit.enregistrer(OperationSpec(
        name="search_beneficiaires",
        description="Bénéficiaires par nom/prénom (données minimisées).",
        input_schema=BeneficiaireSearchInput,
        run=rechercher_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="search_documents",
        description="Documents (une ancre métier, type). Métadonnées seules.",
        input_schema=PageSearchInput,
        run=rechercher_documents,
    ))

    from app.tools.web import attacher_web_search

    return attacher_web_search(definition, tuple(kit.construire()) + extra_tools)


def _rechercher_equipes_rh(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Vivier RH : nom/prénom/profil + nb d'affectations (comme consultation)."""
    from app.domain.execution import Equipe as _Equipe

    terme = (payload.get("recherche") or "").strip()
    stmt = select(_Equipe).order_by(_Equipe.nom, _Equipe.prenom)
    if terme:
        motif = f"%{terme}%"
        stmt = stmt.where(
            _Equipe.nom.ilike(motif) | _Equipe.prenom.ilike(motif) | _Equipe.profil.ilike(motif)
        )
    equipes = list(session.scalars(stmt.limit(100)))
    return {
        "equipes": [
            {
                **serialiser_entite(equipe, "id", "nom", "prenom", "email", "profil", "statut"),
                "nb_affectations": len(equipe.affectations),
            }
            for equipe in equipes
        ]
    }


__all__ = [
    "AGENT_ID",
    "NOMS_TOOLS",
    "TASK_TYPE",
    "AssignmentProposalInput",
    "construire_tools_rh",
]
