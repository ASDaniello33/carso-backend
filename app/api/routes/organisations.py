"""Routes — organisations clientes (Lot C1, instruction/02 §A).

Contrôleurs minces : validation d'entrée, appel du service dans la session
transactionnelle de la requête. Aucune logique métier ici.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, get_current_user
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import OrganisationCreate, OrganisationRead, OrganisationUpdate
from app.application.dto import OrganisationInput, OrganisationUpdateInput
from app.application.services import OrganisationService
from app.domain.organization import Organisation

router = APIRouter(
    prefix="/organisations",
    tags=["organisations"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/", response_model=list[OrganisationRead])
def list_organisations(session: DbSession) -> list[Organisation]:
    """Référentiel des organisations clientes."""
    return OrganisationService(session).lister()


@router.get("/{organisation_id}", response_model=OrganisationRead)
def get_organisation(
    organisation_id: UUID, session: DbSession
) -> Organisation:
    """Consultation d'une organisation (404 si absente)."""
    return OrganisationService(session).obtenir(organisation_id)


@router.post("/", response_model=OrganisationRead, status_code=status.HTTP_201_CREATED)
def create_organisation(
    payload: OrganisationCreate, session: DbSession
) -> Organisation:
    """Enregistre une organisation cliente."""
    return OrganisationService(session).creer(
        OrganisationInput(
            nom=payload.nom,
            type=payload.type,
            adresse=payload.adresse,
            email=payload.email,
            telephone=payload.telephone,
            statut=payload.statut,
        )
    )


@router.patch("/{organisation_id}", response_model=OrganisationRead)
def update_organisation(
    organisation_id: UUID,
    payload: OrganisationUpdate,
    session: DbSession,
) -> Organisation:
    """Modification partielle (seuls les champs fournis sont appliqués)."""
    return OrganisationService(session).modifier(
        organisation_id,
        OrganisationUpdateInput(
            nom=payload.nom,
            type=payload.type,
            adresse=payload.adresse,
            email=payload.email,
            telephone=payload.telephone,
            statut=payload.statut,
        ),
    )


# --- Suppression cascadée (règle confirmée CARSO, 22/09) ----------------------
# Une organisation emporte ses appels, lots, offres, missions, sessions,
# participations, affectations, supports, budgets et documents. Les personnes
# (bénéficiaires, vivier) ne sont pas des dépendances : elles survivent.
enregistrer_suppression(router, "organisations")
