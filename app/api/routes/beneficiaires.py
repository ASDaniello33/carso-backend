"""Routes—bénéficiaires (Lot C1, instruction/02 §G).

Contrôleurs minces. L'import Excel vit dans ``/imports/beneficiaires`` et
``/sessions/{id}/imports/beneficiaires``—la route et les outils d'agent
partagent le même service applicatif (AGENTS.md §2.5).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep, exiger_interne
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import BeneficiaireCreate, BeneficiaireRead, BeneficiaireUpdate
from app.application.dto import BeneficiaireInput, BeneficiaireUpdateInput
from app.application.services import BeneficiaireService
from app.domain.execution import Beneficiaire

router = APIRouter(
    prefix="/beneficiaires",
    tags=["beneficiaires"],
    dependencies=[Depends(get_current_user)],
)

# --- Suppression cascadée (règle confirmée CARSO, 22/09) ----------------------
# Supprimer une personne emporte ses participations ; les sessions, elles,
# survivent (une inscription n'est pas une donnée de la session).
enregistrer_suppression(router, "beneficiaires")


@router.get(
    "/", response_model=list[BeneficiaireRead], dependencies=[Depends(exiger_interne)]
)
def list_beneficiaires(
    session: DbSession,
    statut: Annotated[
        str | None, Query(description="Filtre facultatif : actif | archive")
    ] = None,
) -> list[Beneficiaire]:
    """Tous les bénéficiaires, toutes sessions confondues (page Bénéficiaires)."""
    return BeneficiaireService(session).lister(statut=statut)


@router.get("/{beneficiaire_id}", response_model=BeneficiaireRead)
def get_beneficiaire(
    beneficiaire_id: UUID, session: DbSession, perimetre: PerimetreDep
) -> Beneficiaire:
    """Consultation d'un bénéficiaire (404 si absent ou hors de ses sessions).

    Un formateur n'atteint que les personnes inscrites à une de ses sessions.
    """
    return BeneficiaireService(session, perimetre=perimetre).obtenir(beneficiaire_id)


@router.post(
    "/",
    response_model=BeneficiaireRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_beneficiaire(
    payload: BeneficiaireCreate, session: DbSession, utilisateur: InterneUser
) -> Beneficiaire:
    """Enregistre un bénéficiaire."""
    return BeneficiaireService(session, actor_id=str(utilisateur.id)).creer(
        BeneficiaireInput(
            nom=payload.nom,
            prenom=payload.prenom,
            contact=payload.contact,
            organisation_origine=payload.organisation_origine,
            identifiant_externe=payload.identifiant_externe,
        )
    )


@router.patch(
    "/{beneficiaire_id}",
    response_model=BeneficiaireRead,
    dependencies=[Depends(exiger_interne)],
)
def update_beneficiaire(
    beneficiaire_id: UUID,
    payload: BeneficiaireUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> Beneficiaire:
    """Modification partielle (seuls les champs fournis sont appliqués)."""
    return BeneficiaireService(session, actor_id=str(utilisateur.id)).modifier(
        beneficiaire_id,
        BeneficiaireUpdateInput(
            nom=payload.nom,
            prenom=payload.prenom,
            contact=payload.contact,
            organisation_origine=payload.organisation_origine,
            identifiant_externe=payload.identifiant_externe,
        ),
    )


@router.post(
    "/{beneficiaire_id}/archivage",
    response_model=BeneficiaireRead,
    dependencies=[Depends(exiger_interne)],
)
def archiver_beneficiaire(
    beneficiaire_id: UUID, session: DbSession, utilisateur: InterneUser
) -> Beneficiaire:
    """Archive le bénéficiaire (réversible) : participations passées conservées."""
    return BeneficiaireService(session, actor_id=str(utilisateur.id)).archiver(
        beneficiaire_id
    )


@router.post(
    "/{beneficiaire_id}/reactivation",
    response_model=BeneficiaireRead,
    dependencies=[Depends(exiger_interne)],
)
def reactiver_beneficiaire(
    beneficiaire_id: UUID, session: DbSession, utilisateur: InterneUser
) -> Beneficiaire:
    """Remet un bénéficiaire archivé dans les listes actives."""
    return BeneficiaireService(session, actor_id=str(utilisateur.id)).reactiver(
        beneficiaire_id
    )
