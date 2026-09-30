"""Routes — affectations d'équipe : proposition → décision humaine.

Le classement des candidats est calculé par l'AgentRH à partir des seules
données enregistrées (profil, CV, rôle demandé) ; la mise en vigueur exige
la décision humaine (règle 6 [C]).
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.agents.agent_rh import _score as _agent_score
from app.agents.agent_rh import build_rh
from app.agents.consultation import rechercher_equipes as _rechercher_equipes
from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.schemas import (
    AffectationProposeeCreate,
    AffectationRead,
    AffectationUpdate,
    CandidatClasse,
    DecisionCreate,
    MissionAffectationsRead,
    PropositionAgentRhRead,
)
from app.application.dto import AffectationProposee
from app.application.services import AffectationService, MissionService
from app.application.trace import Decision
from app.core.errors import ConflictError
from app.domain.enums import StatutAffectation
from app.domain.execution import AffectationEquipe

router = APIRouter(tags=["affectations"], dependencies=[Depends(get_current_user)])


@router.post(
    "/missions/{mission_id}/affectations",
    response_model=AffectationRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def propose_affectation(
    mission_id: UUID,
    payload: AffectationProposeeCreate,
    user: CurrentUser,
    session: DbSession,
) -> AffectationEquipe:
    proposal = AffectationProposee(
        mission_id=payload.mission_id,
        equipe_id=payload.equipe_id,
        role_dans_mission=payload.role_dans_mission,
        proposed_by=str(user.id),
        source=payload.source,
        date_debut=payload.date_debut,
        date_fin=payload.date_fin,
    )
    if proposal.mission_id != mission_id:
        raise ConflictError("mission_id du chemin et du corps différents")
    return AffectationService(session).proposer(proposal)


@router.patch("/affectations/{affectation_id}", response_model=AffectationRead)
def update_affectation(
    affectation_id: UUID,
    payload: AffectationUpdate,
    session: DbSession,
) -> AffectationEquipe:
    """Corrige une affectation : rôle et/ou période (jamais la personne).

    Une affectation refusée est figée (409) ; approuvée, elle reste ajustable et
    la modification est tracée dans l'audit. Changer de membre passe par une
    nouvelle proposition — l'historique des décisions reste lisible.
    """
    return AffectationService(session).modifier(
        affectation_id,
        role_dans_mission=payload.role_dans_mission,
        date_debut=payload.date_debut,
        date_fin=payload.date_fin,
    )


@router.delete("/affectations/{affectation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_affectation(
    affectation_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> None:
    """Retire une affectation **refusée** de la liste (demande 30/09).

    Elle est sans effet et n'apporte que du bruit : l'utilisateur peut
    l'enlever. Toute autre statut est refusé (409) — une proposition en
    attente exige une décision, une approuvée reste l'historique.
    """
    AffectationService(session, actor_id=str(user.id)).supprimer(affectation_id)


@router.post("/affectations/{affectation_id}/approbation", response_model=AffectationRead)
def approve_affectation(
    affectation_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> AffectationEquipe:
    return AffectationService(session).approuver(
        affectation_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.post("/affectations/{affectation_id}/refus", response_model=AffectationRead)
def refuse_affectation(
    affectation_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> AffectationEquipe:
    return AffectationService(session).refuser(
        affectation_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.get(
    "/missions/{mission_id}/affectations",
    response_model=MissionAffectationsRead,
)
def list_affectations(
    mission_id: UUID,
    session: DbSession,
) -> MissionAffectationsRead:
    items = AffectationService(session).lister_pour_mission(mission_id)
    return MissionAffectationsRead(
        mission_id=mission_id,
        affectations=[AffectationRead.model_validate(a) for a in items],
    )


class _PropositionAgentRhInput(BaseModel):
    """Paramètres du questionnaire AgentRH.

    ``role_dans_mission`` est le rôle demandé (Q5 [P] : vocabulaire non figé).
    ``equipe_id`` permet de cibler une équipe précise (réponse HITL ``cibler``).
    """

    role_dans_mission: str = Field(min_length=1, max_length=100)
    equipe_id: UUID | None = None


@router.post(
    "/missions/{mission_id}/proposition-agent-rh",
    response_model=PropositionAgentRhRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def proposition_agent_rh(
    mission_id: UUID,
    payload: _PropositionAgentRhInput,
    session: DbSession,
) -> PropositionAgentRhRead:
    """Demande à l'AgentRH de proposer des affectations pour un rôle.

    L'agent classe le vivier (score explicable, données enregistrées
    uniquement) et crée les propositions en statut ``proposee`` ; rien
    n'entre en vigueur sans approbation humaine explicite.
    """
    mission = MissionService(session).obtenir(mission_id)
    role = payload.role_dans_mission

    agent = build_rh(session)

    # Candidats du vivier : ciblage explicite (réponse HITL ``cibler``)
    # ou recherche contrôlée sur tout le vivier.
    vivier = _rechercher_equipes(session, {"recherche": None})["equipes"]
    if payload.equipe_id is not None:
        vivier = [e for e in vivier if e["id"] == str(payload.equipe_id)]

    if not vivier:
        return PropositionAgentRhRead(
            mission_id=mission.id,
            role_dans_mission=role,
            propositions=[],
            candidats=[],
            message=(
                f"Aucun membre du vivier pour cibler le rôle « {role} ». "
                "Complétez d'abord les équipes."
            ),
        )

    # Classement déterministe : score AgentRH (profil renseigné, CV présent,
    # rôle dans profil), tie-break alphabétique. Aucune invention.
    affectations_existantes = {
        str(a.equipe_id)
        for a in AffectationService(session).lister_pour_mission(mission_id)
        if a.statut == StatutAffectation.APPROUVEE.value
    }

    classes: list[tuple[dict[str, Any], int, dict[str, bool]]] = []
    for membre in vivier:
        score_info = _agent_score(session, UUID(membre["id"]), role)
        classes.append((membre, score_info["score"], score_info["criteres"]))
    classes.sort(key=lambda c: (-c[1], c[0]["nom"].lower(), c[0]["prenom"].lower()))

    # Proposition HITL : le meilleur candidat uniquement — l'utilisateur
    # approuve, refuse ou redemande avec un ciblage différent.
    meilleur_id, meilleur_score = classes[0][0]["id"], classes[0][1]
    deja_proposee = any(
        str(a.equipe_id) == meilleur_id
        and a.role_dans_mission == role
        and a.statut == StatutAffectation.PROPOSEE.value
        for a in AffectationService(session).lister_pour_mission(mission_id)
    )
    propositions: list[AffectationEquipe] = []
    if not deja_proposee:
        resultat = agent.proposer(
            mission.id,
            UUID(meilleur_id),
            role,
            justification=(
                f"Meilleur classement du vivier pour « {role} » "
                f"(score {meilleur_score}/3). "
                "Score calculé sur les données enregistrées uniquement."
            ),
        )
        affectation_id = UUID(resultat["proposition"]["affectation_id"])
        propositions = [
            a
            for a in AffectationService(session).lister_pour_mission(mission_id)
            if a.id == affectation_id
        ]

    candidats = [
        CandidatClasse(
            equipe_id=UUID(membre["id"]),
            nom=membre["nom"],
            prenom=membre["prenom"],
            profil=membre.get("profil"),
            score=score,
            score_max=3,
            criteres=criteres,
            deja_affecte=membre["id"] in affectations_existantes,
        )
        for membre, score, criteres in classes
    ]

    message = (
        f"{len(propositions)} proposition(s) créée(s) — en attente de votre décision."
        if propositions
        else "Une proposition identique est déjà en attente de décision."
    )
    return PropositionAgentRhRead(
        mission_id=mission.id,
        role_dans_mission=role,
        propositions=[AffectationRead.model_validate(p) for p in propositions],
        candidats=candidats,
        message=message,
    )
