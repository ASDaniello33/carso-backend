"""Routes—affectations d'équipe—proposition → décision humaine.

Le classement des candidats est calculé par l'AgentRH à partir des seules
données enregistrées (profil, CV, rôle demandé) ; la mise en vigueur exige
la décision humaine (règle 6 [C]).
"""

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.agents.agent_rh import _score as _agent_score
from app.agents.agent_rh import build_rh
from app.agents.consultation import rechercher_equipes as _rechercher_equipes
from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep, exiger_interne
from app.api.schemas import (
    AffectationProposeeCreate,
    AffectationRead,
    AffectationUpdate,
    CandidatClasse,
    ConflitDisponibilite,
    DecisionCreate,
    DisponibilitePersonne,
    MissionAffectationsRead,
    MissionDisponibilitesRead,
    PropositionAgentRhRead,
)
from app.application.dto import AffectationProposee
from app.application.services import AffectationService, MissionService
from app.application.trace import Decision, demande_par
from app.core.errors import ConflictError, NotFoundError
from app.domain.enums import StatutAffectation
from app.domain.execution import AffectationEquipe

router = APIRouter(tags=["affectations"], dependencies=[Depends(get_current_user)])


@router.post(
    "/missions/{mission_id}/affectations",
    response_model=AffectationRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(exiger_interne)],
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
    return AffectationService(session, actor_id=str(user.id)).proposer(proposal)


@router.patch(
    "/affectations/{affectation_id}",
    response_model=AffectationRead,
    dependencies=[Depends(exiger_interne)],
)
def update_affectation(
    affectation_id: UUID,
    payload: AffectationUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> AffectationEquipe:
    """Corrige une affectation : rôle et/ou période (jamais la personne).

    Une affectation refusée est figée (409) ; approuvée, elle reste ajustable et
    la modification est tracée dans l'audit. Changer de membre passe par une
    nouvelle proposition — l'historique des décisions reste lisible.
    """
    return AffectationService(session, actor_id=str(utilisateur.id)).modifier(
        affectation_id,
        role_dans_mission=payload.role_dans_mission,
        date_debut=payload.date_debut,
        date_fin=payload.date_fin,
    )


@router.delete(
    "/affectations/{affectation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(exiger_interne)],
)
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


@router.post(
    "/affectations/{affectation_id}/annulation",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(exiger_interne)],
)
def annuler_affectation(
    affectation_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> None:
    """Annule une proposition d'affectation **encore en attente** (17/10).

    Le geste est celui qui a proposé : la carte de l'AgentRH peut ainsi revenir
    sur une proposition non décidée. Une affectation approuvée ou refusée est
    figée (409) — elle porte une décision, pas une intention.

    Distinct du refus : l'annulation retire la proposition de la liste, le refus
    la conserve comme décision tracée.
    """
    AffectationService(session, actor_id=str(user.id)).annuler(affectation_id)


@router.post(
    "/affectations/{affectation_id}/approbation",
    response_model=AffectationRead,
    dependencies=[Depends(exiger_interne)],
)
def approve_affectation(
    affectation_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> AffectationEquipe:
    return AffectationService(session, actor_id=str(user.id)).approuver(
        affectation_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.post(
    "/affectations/{affectation_id}/refus",
    response_model=AffectationRead,
    dependencies=[Depends(exiger_interne)],
)
def refuse_affectation(
    affectation_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> AffectationEquipe:
    return AffectationService(session, actor_id=str(user.id)).refuser(
        affectation_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.get(
    "/missions/{mission_id}/affectations",
    response_model=MissionAffectationsRead,
)
def list_affectations(
    mission_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> MissionAffectationsRead:
    """Équipe d'une mission du périmètre (404 sinon, comme la mission)."""
    if perimetre.est_restreint and not perimetre.mission_autorisee(mission_id):
        raise NotFoundError(f"Mission {mission_id} introuvable")
    items = AffectationService(session).lister_pour_mission(mission_id)
    return MissionAffectationsRead(
        mission_id=mission_id,
        affectations=[AffectationRead.model_validate(a) for a in items],
    )


@router.get(
    "/missions/{mission_id}/disponibilites",
    response_model=MissionDisponibilitesRead,
    dependencies=[Depends(exiger_interne)],
)
def lire_disponibilites(
    mission_id: UUID,
    session: DbSession,
    equipe_ids: str | None = Query(
        default=None,
        description="Identifiants séparés par des virgules ; absent = personne non évaluée.",
    ),
) -> MissionDisponibilitesRead:
    """Disponibilité **calculée** de personnes sur les dates d'une mission.

    Lecture seule, même règle que le refus de proposition (ADR 0012). La carte
    de l'agent s'en sert pour marquer une personne en conflit et **désactiver**
    la proposition : l'affichage ne dépend pas de ce que le modèle a écrit.

    Une mission sans ses deux dates n'est pas évaluable : ``evaluable`` est
    faux et aucune personne n'est déclarée indisponible.
    """
    mission = MissionService(session).obtenir(mission_id)
    demandees: list[UUID] = []
    for brut in (equipe_ids or "").split(","):
        brut = brut.strip()
        if not brut:
            continue
        try:
            demandees.append(UUID(brut))
        except ValueError as exc:
            raise ConflictError(f"Identifiant d'équipe invalide : {brut}") from exc

    evaluable = mission.date_debut is not None and mission.date_fin is not None
    personnes: list[DisponibilitePersonne] = []
    if evaluable:
        service = AffectationService(session)
        for equipe_id in demandees:
            conflits = service.conflits_disponibilite(
                equipe_id,
                mission.date_debut,
                mission.date_fin,
                mission_id=mission.id,
            )
            personnes.append(
                DisponibilitePersonne(
                    equipe_id=equipe_id,
                    disponible=not conflits,
                    conflits=[
                        ConflitDisponibilite(
                            reference=c["reference"],
                            titre=c["titre"],
                            date_debut=date.fromisoformat(c["date_debut"]),
                            date_fin=date.fromisoformat(c["date_fin"]),
                        )
                        for c in conflits
                    ],
                )
            )
    return MissionDisponibilitesRead(
        mission_id=mission.id,
        evaluable=evaluable,
        date_debut=mission.date_debut,
        date_fin=mission.date_fin,
        personnes=personnes,
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
    dependencies=[Depends(exiger_interne)],
)
def proposition_agent_rh(
    mission_id: UUID,
    payload: _PropositionAgentRhInput,
    session: DbSession,
    user: CurrentUser,
) -> PropositionAgentRhRead:
    """Demande à l'AgentRH de proposer des affectations pour un rôle.

    L'agent classe le vivier (score explicable, données enregistrées
    uniquement) et crée les propositions en statut ``proposee`` ; rien
    n'entre en vigueur sans approbation humaine explicite. Le geste est celui
    de l'agent : le compte qui l'a demandé est écrit dans la trace
    (``demande_par``).
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

    service = AffectationService(session)
    affectations_existantes = {
        str(a.equipe_id)
        for a in service.lister_pour_mission(mission_id)
        if a.statut == StatutAffectation.APPROUVEE.value
    }

    # Disponibilité **calculée** (ADR 0012) : la même règle que celle qui
    # refuse une proposition. Une personne approuvée sur une mission qui
    # chevauche celle-ci est écartée du classement — sinon la proposition
    # serait refusée à coup sûr. Une mission sans dates n'est pas évaluable :
    # aucun candidat n'est alors écarté (cas documenté, aucun droit inventé).
    conflits_par_equipe: dict[str, list[str]] = {}
    if mission.date_debut is not None and mission.date_fin is not None:
        for membre in vivier:
            conflits = service.conflits_disponibilite(
                UUID(membre["id"]),
                mission.date_debut,
                mission.date_fin,
                mission_id=mission.id,
            )
            if conflits:
                conflits_par_equipe[membre["id"]] = [
                    f"{c['reference']} ({c['date_debut']} → {c['date_fin']})"
                    for c in conflits
                ]

    # Classement déterministe : personnes **disponibles** d'abord, puis score
    # AgentRH (profil renseigné, CV présent, rôle dans profil), tie-break
    # alphabétique. Aucune invention.
    classes: list[tuple[dict[str, Any], int, dict[str, bool]]] = []
    for membre in vivier:
        score_info = _agent_score(session, UUID(membre["id"]), role)
        classes.append((membre, score_info["score"], score_info["criteres"]))
    classes.sort(
        key=lambda c: (
            c[0]["id"] in conflits_par_equipe,
            -c[1],
            c[0]["nom"].lower(),
            c[0]["prenom"].lower(),
        )
    )

    # Proposition HITL : le meilleur candidat **disponible** uniquement —
    # l'utilisateur approuve, refuse ou redemande avec un ciblage différent.
    propositions: list[AffectationEquipe] = []
    retenu = next((c for c in classes if c[0]["id"] not in conflits_par_equipe), None)
    if retenu is None:
        message = (
            "Aucun membre du vivier n'est disponible sur les dates de cette mission : "
            "chaque candidat est déjà affecté (approuvé) sur une mission qui chevauche. "
            "Aucune proposition n'est créée."
        )
    else:
        meilleur_id, meilleur_score = retenu[0]["id"], retenu[1]
        deja_proposee = any(
            str(a.equipe_id) == meilleur_id
            and a.role_dans_mission == role
            and a.statut == StatutAffectation.PROPOSEE.value
            for a in service.lister_pour_mission(mission_id)
        )
        if not deja_proposee:
            with demande_par(user.id):
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
                a for a in service.lister_pour_mission(mission_id) if a.id == affectation_id
            ]
        message = (
            f"{len(propositions)} proposition(s) créée(s), en attente de votre décision."
            if propositions
            else "Une proposition identique est déjà en attente de décision."
        )

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
            disponible=membre["id"] not in conflits_par_equipe,
            conflits=conflits_par_equipe.get(membre["id"], []),
        )
        for membre, score, criteres in classes
    ]
    return PropositionAgentRhRead(
        mission_id=mission.id,
        role_dans_mission=role,
        propositions=[AffectationRead.model_validate(p) for p in propositions],
        candidats=candidats,
        message=message,
    )
