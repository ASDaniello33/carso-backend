"""Routes—appels à proposition (P1)—extraction proposée → décision humaine → lecture.

Contrôleurs minces (instruction/09 §3)—validation d'entrée, appel du service
dans la session transactionnelle de la requête, mapping de réponse. Aucune
logique métier ici.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep, exiger_interne
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import (
    AppelAPropositionCreate,
    AppelAPropositionRead,
    AppelAPropositionUpdate,
    DecisionCreate,
    ExtractionProposeeCreate,
)
from app.application.dto import AppelAPropositionInput, AppelUpdateInput
from app.application.services import AppelAPropositionService
from app.application.trace import Decision, demande_par
from app.domain.organization import AppelAProposition

router = APIRouter(
    prefix="/appels-a-proposition",
    tags=["appels-a-proposition"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/",
    response_model=list[AppelAPropositionRead],
    dependencies=[Depends(exiger_interne)],
)
def list_appels_a_proposition(session: DbSession) -> list[AppelAProposition]:
    """Référentiel des appels à proposition reçus."""
    return AppelAPropositionService(session).lister()


@router.post(
    "/",
    response_model=AppelAPropositionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_appel_a_proposition(
    payload: AppelAPropositionCreate,
    session: DbSession,
    user: CurrentUser,
) -> AppelAProposition:
    """Enregistre un appel reçu (statut ``recu``). Le document source se
    rattache ensuite via ``POST /documents`` (ancre ``appel_a_proposition_id``)."""
    return AppelAPropositionService(session, actor_id=str(user.id)).enregistrer(
        AppelAPropositionInput(
            organisation_id=payload.organisation_id,
            type=payload.type,
            reference=payload.reference,
            titre=payload.titre,
            description=payload.description,
            date_reception=payload.date_reception,
            date_limite=payload.date_limite,
        )
    )


@router.get("/{appel_a_proposition_id}", response_model=AppelAPropositionRead)
def get_appel_a_proposition(
    appel_a_proposition_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> AppelAProposition:
    """Consultation d'un appel (404 si absent ou hors du dossier de ses missions)."""
    return AppelAPropositionService(session, perimetre=perimetre).obtenir(
        appel_a_proposition_id
    )


@router.post(
    "/{appel_a_proposition_id}/extraction",
    response_model=AppelAPropositionRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(exiger_interne)],
)
def propose_extraction(
    appel_a_proposition_id: UUID,
    payload: ExtractionProposeeCreate,
    session: DbSession,
    user: CurrentUser,
) -> AppelAProposition:
    """Un agent soumet une extraction : mémorisée en zone *proposal* et
    soumise à décision humaine (jamais officielle) — 202 Accepted.

    L'événement reste celui de l'agent, mais le **demandeur** est l'utilisateur
    authentifié (métadonnée ``demande_par``) : le journal distingue l'agent qui
    a extrait et le compte qui a soumis le résultat.
    """
    with demande_par(user.id):
        return AppelAPropositionService(session).enregistrer_extraction(
            appel_a_proposition_id,
            payload.donnees_extraites,
            proposed_by_agent=payload.proposed_by_agent,
        )


@router.post(
    "/{appel_a_proposition_id}/proposition",
    response_model=AppelAPropositionRead,
    dependencies=[Depends(exiger_interne)],
)
def remettre_en_proposition(
    appel_a_proposition_id: UUID,
    payload: ExtractionProposeeCreate,
    session: DbSession,
    user: CurrentUser,
) -> AppelAProposition:
    """Remet un appel **déjà validé** en proposition, avec un nouveau contenu.

    Chemin de réparation : une validation qui n'a produit aucun lot officiel
    laissait l'appel figé sans matière exploitable — plus aucun lot ne pouvait
    naître et la proposition ne partait plus. Ici l'appel revient en
    ``propose``, prêt pour une nouvelle décision humaine ; l'enregistrement
    n'officialise rien. Refusé (409) dès qu'une offre existe : la proposition
    d'un appel qui a produit du concret n'est plus modifiable.
    """
    with demande_par(user.id):
        return AppelAPropositionService(session).enregistrer_proposition_corrigee(
            appel_a_proposition_id,
            payload.donnees_extraites,
            proposed_by_agent=payload.proposed_by_agent,
        )


@router.post(
    "/{appel_a_proposition_id}/validation",
    response_model=AppelAPropositionRead,
    dependencies=[Depends(exiger_interne)],
)
def decide_extraction(
    appel_a_proposition_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> AppelAProposition:
    """Décision humaine : ``decided_by`` = utilisateur authentifié."""
    decision = Decision(decided_by=str(user.id), reason=payload.reason)
    service = AppelAPropositionService(session, actor_id=str(user.id))
    if payload.decision == "approuve":
        return service.approuver_extraction(appel_a_proposition_id, decision)
    return service.refuser_extraction(appel_a_proposition_id, decision)


@router.patch(
    "/{appel_a_proposition_id}",
    response_model=AppelAPropositionRead,
    dependencies=[Depends(exiger_interne)],
)
def update_appel_a_proposition(
    appel_a_proposition_id: UUID,
    payload: AppelAPropositionUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> AppelAProposition:
    """Modification d'un appel reçu : identité, nature, dates.

    Référence et organisation émettrice ne sont modifiables que tant que l'appel
    n'est pas validé — les lots officiels en dépendent (contrôle du service).
    """
    return AppelAPropositionService(session, actor_id=str(utilisateur.id)).modifier(
        appel_a_proposition_id,
        AppelUpdateInput(
            reference=payload.reference,
            titre=payload.titre,
            description=payload.description,
            type=payload.type,
            date_reception=payload.date_reception,
            date_limite=payload.date_limite,
            organisation_id=payload.organisation_id,
        ),
    )


# --- Suppression cascadée (règle confirmée CARSO, 22/09) ----------------------
# Un appel emporte ses lots, offres, missions, sessions, budgets et documents.
# L'organisation émettrice, elle, survit.
enregistrer_suppression(router, "appels")
