"""Routes—affectations d'équipe—proposition → décision humaine.

Le classement des candidats est calculé par l'AgentRH à partir des seules
données enregistrées (profil, CV, rôle demandé) ; la mise en vigueur exige
la décision humaine (règle 6 [C]).
"""

from datetime import date
from typing import Annotated, Any
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
    ClassementAffectationRead,
    ConflitDisponibilite,
    DecisionCreate,
    DisponibilitePersonne,
    MembreEnConflit,
    MissionAffectationsRead,
    MissionConflitsEquipeRead,
    MissionDisponibilitesRead,
    PropositionAgentRhRead,
)
from app.application.dto import AffectationProposee
from app.application.services import AffectationService, MissionService
from app.application.services.affectation_service import INTITULE_NON_MODIFIE
from app.application.trace import Decision, demande_par
from app.core.errors import ConflictError, NotFoundError
from app.domain.enums import StatutAffectation, StatutMission
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
        intitule_role=payload.intitule_role,
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
        intitule_role=(
            payload.intitule_role
            if "intitule_role" in payload.model_fields_set
            else INTITULE_NON_MODIFIE
        ),
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
    attente exige une décision, une affectation **approuvée** se retire par
    l'annulation (``POST …/annulation``), qui trace la décision reprise.
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
    """Annule **et retire** une affectation en attente ou déjà approuvée.

    - Proposition ``proposee`` (17/10) : le geste est celui qui a proposé — la
      carte de l'AgentRH revient sur une proposition non décidée.
    - Affectation ``approuvee`` (demande du 24/11) : l'administrateur ou le
      collaborateur reprend la décision. La personne sort de l'équipe, redevient
      disponible sur ces dates et perd le périmètre ouvert par l'affectation.

    Une affectation **refusée** reste du ressort de ``DELETE`` (son propre geste,
    avec sa trace) : 409 ici.
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


@router.get(
    "/missions/{mission_id}/conflits-equipe",
    response_model=MissionConflitsEquipeRead,
    dependencies=[Depends(exiger_interne)],
)
def lire_conflits_equipe(
    mission_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    date_debut: Annotated[
        date | None,
        Query(
            description=(
                "Début d'une période hypothétique à évaluer (dates saisies avant "
                "enregistrement d'un report) ; absent = dates de la mission."
            )
        ),
    ] = None,
    date_fin: Annotated[
        date | None,
        Query(description="Fin de la période hypothétique ; absente = dates de la mission."),
    ] = None,
) -> MissionConflitsEquipeRead:
    """Chevauchements de l'équipe **approuvée** sur les dates de la mission.

    Lecture seule de la même règle que le refus d'affectation (ADR 0012),
    agrégée par membre : après un report de dates (ADR 0015) ou une
    affectation, l'écran signale les personnes déjà engagées ailleurs sur la
    période. L'alerte **informe** ; elle ne modifie ni ne bloque rien.

    ``date_debut``/``date_fin`` évaluent une période **hypothétique** — les
    dates saisies dans le formulaire, avant enregistrement du report — sans
    rien écrire. Absentes, la période évaluée est celle de la mission.

    Une période sans ses deux bornes, ou une mission annulée, n'est pas
    évaluable : ``evaluable`` est faux et aucun membre n'est signalé.
    """
    mission = MissionService(session, perimetre=perimetre).obtenir(mission_id)
    debut = date_debut if date_debut is not None else mission.date_debut
    fin = date_fin if date_fin is not None else mission.date_fin
    evaluable = (
        debut is not None
        and fin is not None
        and mission.statut != StatutMission.ANNULE.value
    )
    conflits = (
        AffectationService(session).conflits_mission(
            mission.id, date_debut=debut, date_fin=fin
        )
        if evaluable
        else []
    )
    return MissionConflitsEquipeRead(
        mission_id=mission.id,
        evaluable=evaluable,
        date_debut=debut,
        date_fin=fin,
        membres=[
            MembreEnConflit(
                equipe_id=c["equipe_id"],
                nom=c["nom"],
                prenom=c["prenom"],
                missions_en_conflit=[
                    ConflitDisponibilite(
                        reference=m["reference"],
                        titre=m["titre"],
                        date_debut=date.fromisoformat(m["date_debut"]),
                        date_fin=date.fromisoformat(m["date_fin"]),
                    )
                    for m in c["missions_en_conflit"]
                ],
            )
            for c in conflits
        ],
    )


def _classer_vivier(
    session: Any,
    mission: Any,
    role: str,
    *,
    equipe_id: UUID | None = None,
) -> list[CandidatClasse]:
    """Classement déterministe du vivier pour **un poste** — aucune écriture.

    Trois niveaux, dans l'ordre : disponibilité **calculée** (une mission
    approuvée qui chevauche écarte la personne), score AgentRH (profil
    renseigné, CV présent, rôle dans le profil), puis ordre alphabétique.

    Le même calcul sert à l'affichage (``GET /classement-affectation``) et à la
    proposition de l'agent : l'écran ne montre jamais un score que le serveur
    n'a pas produit, et ce qu'il montre est ce que la proposition appliquera.

    Returns:
        Les candidats classés ; liste vide si le vivier est vide (ou si
        ``equipe_id`` ne désigne personne).
    """
    vivier = _rechercher_equipes(session, {"recherche": None})["equipes"]
    if equipe_id is not None:
        vivier = [membre for membre in vivier if membre["id"] == str(equipe_id)]
    if not vivier:
        return []

    service = AffectationService(session)
    affectations_existantes = {
        str(a.equipe_id)
        for a in service.lister_pour_mission(mission.id)
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
    # AgentRH, tie-break alphabétique. Aucune invention.
    classes: list[tuple[dict[str, Any], int, int, dict[str, bool]]] = []
    for membre in vivier:
        score_info = _agent_score(session, UUID(membre["id"]), role)
        classes.append(
            (membre, score_info["score"], score_info["score_max"], score_info["criteres"])
        )
    classes.sort(
        key=lambda c: (
            c[0]["id"] in conflits_par_equipe,
            -c[1],
            c[0]["nom"].lower(),
            c[0]["prenom"].lower(),
        )
    )

    return [
        CandidatClasse(
            equipe_id=UUID(membre["id"]),
            nom=membre["nom"],
            prenom=membre["prenom"],
            profil=membre.get("profil"),
            score=score,
            score_max=score_max,
            criteres=criteres,
            deja_affecte=membre["id"] in affectations_existantes,
            disponible=membre["id"] not in conflits_par_equipe,
            conflits=conflits_par_equipe.get(membre["id"], []),
        )
        for membre, score, score_max, criteres in classes
    ]


@router.get(
    "/missions/{mission_id}/classement-affectation",
    response_model=ClassementAffectationRead,
    dependencies=[Depends(exiger_interne)],
)
def classement_affectation(
    mission_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    role_dans_mission: Annotated[
        str,
        Query(
            min_length=1,
            max_length=100,
            description="Poste à pourvoir : le score dépend du rôle demandé.",
        ),
    ],
) -> ClassementAffectationRead:
    """Score de chaque personne du vivier pour un poste, **sans écrire**.

    La carte de l'AgentRH lit ce classement avant toute décision : afficher des
    scores ne doit créer aucune proposition. Le calcul est exactement celui de
    ``POST /proposition-agent-rh`` (``_classer_vivier``), donc les scores
    affichés sont ceux que la proposition appliquera.
    """
    mission = MissionService(session, perimetre=perimetre).obtenir(mission_id)
    return ClassementAffectationRead(
        mission_id=mission.id,
        role_dans_mission=role_dans_mission,
        candidats=_classer_vivier(session, mission, role_dans_mission),
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

    # Classement déterministe du vivier (le même que celui affiché par la
    # carte) : ciblage explicite (réponse HITL ``cibler``) ou vivier entier.
    candidats = _classer_vivier(session, mission, role, equipe_id=payload.equipe_id)

    if not candidats:
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

    # Proposition HITL : le meilleur candidat **disponible** uniquement —
    # l'utilisateur approuve, refuse ou redemande avec un ciblage différent.
    propositions: list[AffectationEquipe] = []
    retenu = next((c for c in candidats if c.disponible), None)
    if retenu is None:
        message = (
            "Aucun membre du vivier n'est disponible sur les dates de cette mission : "
            "chaque candidat est déjà affecté (approuvé) sur une mission qui chevauche. "
            "Aucune proposition n'est créée."
        )
    else:
        meilleur_id, meilleur_score, note_max = (
            str(retenu.equipe_id),
            retenu.score,
            retenu.score_max,
        )
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
                        f"(note {meilleur_score}/{note_max}). "
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

    return PropositionAgentRhRead(
        mission_id=mission.id,
        role_dans_mission=role,
        propositions=[AffectationRead.model_validate(p) for p in propositions],
        candidats=candidats,
        message=message,
    )
