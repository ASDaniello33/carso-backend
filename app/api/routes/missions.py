"""Routes—missions (Lot C1, instruction/02 §F) et supports de formation.

Contrôleurs minces. Création depuis des offres approuvées (règle 5 [C], relation
N-N confirmée—technique + financière) ou prestation directe. La mission porte
son propre lieu (``lieu_id``) et ses supports de formation.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep, exiger_interne
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import (
    MissionDepuisAppelCreate,
    MissionDepuisOffresCreate,
    MissionDirecteCreate,
    MissionLieuUpdate,
    MissionRead,
    MissionStatutUpdate,
    MissionUpdate,
    SupportFormationCreate,
    SupportFormationRead,
)
from app.application.dto import (
    MissionDepuisAppelInput,
    MissionDepuisOffresInput,
    MissionDirecteInput,
    SupportFormationInput,
)
from app.application.services import MissionService, SupportFormationService
from app.domain.enums import ActorType
from app.domain.execution import Mission, SupportFormation

router = APIRouter(
    prefix="/missions",
    tags=["missions"],
    dependencies=[Depends(get_current_user)],
)

#: Suppression réelle cascadée (impact + exécution) — module partagé.
enregistrer_suppression(router, "missions")


@router.get("/", response_model=list[MissionRead])
def list_missions(
    session: DbSession,
    perimetre: PerimetreDep,
    statut: Annotated[
        str | None,
        Query(description="Filtre facultatif sur le statut (machine à états ``mission``)"),
    ] = None,
) -> list[Mission]:
    """Missions du périmètre, éventuellement filtrées par statut.

    Un formateur ne liste que les missions où il est affecté (une mission hors
    périmètre n'apparaît pas — le backend est la barrière, pas l'interface).
    """
    service = MissionService(session, perimetre=perimetre)
    if statut is None:
        return service.lister_toutes()
    return service.lister_par_statut(statut)


@router.get("/{mission_id}", response_model=MissionRead)
def get_mission(
    mission_id: UUID, session: DbSession, perimetre: PerimetreDep
) -> Mission:
    """Consultation d'une mission (404 si absente ou hors de son périmètre)."""
    return MissionService(session, perimetre=perimetre).obtenir(mission_id)


@router.post(
    "/depuis-appel",
    response_model=MissionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_mission_depuis_appel(
    payload: MissionDepuisAppelCreate, session: DbSession, utilisateur: InterneUser
) -> Mission:
    """Crée une mission depuis **un appel et son lot** (chemin UI unique).

    L'appel porte l'organisation (déduite, jamais fournie) et ses lots portent
    les offres : la mission référence les offres approuvées du lot. Le lot doit
    appartenir à l'appel fourni ; sans offre approuvée sur le lot, la création
    est refusée (422) — l'utilisateur termine d'abord le cycle des offres.
    """
    return MissionService(session, actor_id=str(utilisateur.id)).creer_depuis_appel(
        MissionDepuisAppelInput(
            appel_a_proposition_id=payload.appel_a_proposition_id,
            lot_id=payload.lot_id,
            reference=payload.reference,
            titre=payload.titre,
            description=payload.description,
            date_debut=payload.date_debut,
            date_fin=payload.date_fin,
            lieu_id=payload.lieu_id,
        )
    )


@router.post(
    "/depuis-offres",
    response_model=MissionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_mission_depuis_offres(
    payload: MissionDepuisOffresCreate, session: DbSession, utilisateur: InterneUser
) -> Mission:
    """Crée une mission à partir d'offres approuvées du même lot."""
    return MissionService(session, actor_id=str(utilisateur.id)).creer_depuis_offres(
        MissionDepuisOffresInput(
            offre_ids=tuple(payload.offre_ids),
            reference=payload.reference,
            titre=payload.titre,
            description=payload.description,
            date_debut=payload.date_debut,
            date_fin=payload.date_fin,
            lieu_id=payload.lieu_id,
        )
    )


@router.post(
    "/directe",
    response_model=MissionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_mission_directe(
    payload: MissionDirecteCreate, session: DbSession, utilisateur: InterneUser
) -> Mission:
    """Crée une prestation directe sans offre."""
    return MissionService(session, actor_id=str(utilisateur.id)).creer_directe(
        MissionDirecteInput(
            organisation_id=payload.organisation_id,
            reference=payload.reference,
            titre=payload.titre,
            description=payload.description,
            date_debut=payload.date_debut,
            date_fin=payload.date_fin,
            lieu_id=payload.lieu_id,
        )
    )


@router.post(
    "/{mission_id}/statut",
    response_model=MissionRead,
    dependencies=[Depends(exiger_interne)],
)
def change_mission_statut(
    mission_id: UUID,
    payload: MissionStatutUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> Mission:
    """Transition planifiee → en_preparation → en_cours → cloturee."""
    return MissionService(session, actor_id=str(utilisateur.id)).changer_statut(
        mission_id, payload.statut
    )


@router.patch(
    "/{mission_id}/lieu", response_model=MissionRead, dependencies=[Depends(exiger_interne)]
)
def change_mission_lieu(
    mission_id: UUID,
    payload: MissionLieuUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> Mission:
    """Rattache (ou retire avec ``lieu_id = null``) le lieu d'exécution.

    Le lieu est une donnée distincte de la mission et du lot : il est référencé
    par identifiant, jamais par texte libre. Un identifiant inconnu est refusé
    (404) ; le lieu lui-même n'est jamais créé implicitement ici.
    """
    return MissionService(session, actor_id=str(utilisateur.id)).changer_lieu(
        mission_id, payload.lieu_id
    )


@router.patch(
    "/{mission_id}", response_model=MissionRead, dependencies=[Depends(exiger_interne)]
)
def update_mission(
    mission_id: UUID,
    payload: MissionUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> Mission:
    """Modification partielle d'une mission : titre, description, lieu.

    Les offres rattachées ne bougent jamais : elles sont la réponse à un
    ``Appel + Lot``, une autre mission est une autre mission. L'audit est
    attribué à l'humain — le tool d'agent, lui, trace ``AGENT``.
    """
    return MissionService(session, actor_id=str(utilisateur.id)).modifier(
        mission_id,
        titre=payload.titre,
        lieu_id=payload.lieu_id,
        description=payload.description,
        acteur=ActorType.HUMAIN.value,
    )


# --- Supports de formation (Formateur ↔ Mission ↔ Document) ------------------


@router.get("/{mission_id}/supports-formation", response_model=list[SupportFormationRead])
def list_supports_formation(
    mission_id: UUID, session: DbSession, perimetre: PerimetreDep
) -> list[SupportFormation]:
    """Supports de formation d'une mission du périmètre (404 sinon)."""
    return SupportFormationService(session, perimetre=perimetre).lister_pour_mission(
        mission_id
    )


@router.post(
    "/{mission_id}/supports-formation",
    response_model=SupportFormationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_support_formation(
    mission_id: UUID,
    payload: SupportFormationCreate,
    session: DbSession,
    perimetre: PerimetreDep,
    utilisateur: CurrentUser,
) -> SupportFormation:
    """Rattache un document existant à un formateur de la mission.

    Le document reste l'entité documentaire unique ; le formateur doit être
    affecté à la mission avec un rôle qui vaut Formateur (un chef de mission est
    également formateur). Un formateur ne rattache que **ses** supports, dans
    une mission affectée.
    """
    return SupportFormationService(
        session, actor_id=str(utilisateur.id), perimetre=perimetre
    ).ajouter(
        SupportFormationInput(
            mission_id=mission_id,
            equipe_id=payload.equipe_id,
            document_id=payload.document_id,
            libelle=payload.libelle,
        )
    )


@router.delete(
    "/supports-formation/{support_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_support_formation(
    support_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    utilisateur: CurrentUser,
) -> None:
    """Retire un support de formation (aucun fichier n'est détruit)."""
    SupportFormationService(
        session, actor_id=str(utilisateur.id), perimetre=perimetre
    ).retirer(support_id)
