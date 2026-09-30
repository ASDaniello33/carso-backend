"""Routes — bénéficiaires (Lot C1, instruction/02 §G).

Contrôleurs minces. L'import Excel vit dans ``/imports/beneficiaires`` et
``/sessions/{id}/imports/beneficiaires`` : la route et les outils d'agent
partagent le même service applicatif (AGENTS.md §2.5).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, get_current_user
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


@router.get("/", response_model=list[BeneficiaireRead])
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
    beneficiaire_id: UUID, session: DbSession
) -> Beneficiaire:
    """Consultation d'un bénéficiaire (404 si absent)."""
    return BeneficiaireService(session).obtenir(beneficiaire_id)


@router.post("/", response_model=BeneficiaireRead, status_code=status.HTTP_201_CREATED)
def create_beneficiaire(
    payload: BeneficiaireCreate, session: DbSession
) -> Beneficiaire:
    """Enregistre un bénéficiaire."""
    return BeneficiaireService(session).creer(
        BeneficiaireInput(
            nom=payload.nom,
            prenom=payload.prenom,
            contact=payload.contact,
            organisation_origine=payload.organisation_origine,
            identifiant_externe=payload.identifiant_externe,
        )
    )


@router.patch("/{beneficiaire_id}", response_model=BeneficiaireRead)
def update_beneficiaire(
    beneficiaire_id: UUID,
    payload: BeneficiaireUpdate,
    session: DbSession,
) -> Beneficiaire:
    """Modification partielle (seuls les champs fournis sont appliqués)."""
    return BeneficiaireService(session).modifier(
        beneficiaire_id,
        BeneficiaireUpdateInput(
            nom=payload.nom,
            prenom=payload.prenom,
            contact=payload.contact,
            organisation_origine=payload.organisation_origine,
            identifiant_externe=payload.identifiant_externe,
        ),
    )


@router.post("/{beneficiaire_id}/archivage", response_model=BeneficiaireRead)
def archiver_beneficiaire(beneficiaire_id: UUID, session: DbSession) -> Beneficiaire:
    """Archive le bénéficiaire (réversible) : participations passées conservées."""
    return BeneficiaireService(session).archiver(beneficiaire_id)


@router.post("/{beneficiaire_id}/reactivation", response_model=BeneficiaireRead)
def reactiver_beneficiaire(beneficiaire_id: UUID, session: DbSession) -> Beneficiaire:
    """Remet un bénéficiaire archivé dans les listes actives."""
    return BeneficiaireService(session).reactiver(beneficiaire_id)
