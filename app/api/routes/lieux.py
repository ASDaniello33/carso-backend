"""Routes—lieux d'exécution (donnée distincte d'une mission).

Le lieu n'est jamais la nature d'un lot—il est créé ici puis référencé par une
mission (``lieu_id``).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import exiger_interne
from app.api.schemas import LieuCreate, LieuRead, LieuUpdate
from app.application.dto import LieuInput, LieuUpdateInput
from app.application.services import LieuService
from app.domain.execution import Lieu

router = APIRouter(
    prefix="/lieux",
    tags=["lieux"],
    # Référentiel d'équipe interne : le formulaire de mission y puise un lieu.
    dependencies=[Depends(get_current_user), Depends(exiger_interne)],
)


@router.get("/", response_model=list[LieuRead])
def list_lieux(session: DbSession) -> list[Lieu]:
    """Tous les lieux, ordre alphabétique (sélecteur de mission)."""
    return LieuService(session).lister()


@router.post("/", response_model=LieuRead, status_code=status.HTTP_201_CREATED)
def create_lieu(payload: LieuCreate, session: DbSession, user: CurrentUser) -> Lieu:
    """Crée un lieu (nom obligatoire, localisation libre)."""
    return LieuService(session, actor_id=str(user.id)).creer(
        LieuInput(
            nom=payload.nom,
            adresse=payload.adresse,
            ville=payload.ville,
            pays=payload.pays,
            zone=payload.zone,
        )
    )


@router.get("/{lieu_id}", response_model=LieuRead)
def get_lieu(lieu_id: UUID, session: DbSession) -> Lieu:
    """Consultation d'un lieu (404 si absent)."""
    return LieuService(session).obtenir(lieu_id)


@router.patch("/{lieu_id}", response_model=LieuRead)
def update_lieu(
    lieu_id: UUID, payload: LieuUpdate, session: DbSession, user: CurrentUser
) -> Lieu:
    """Modification partielle d'un lieu."""
    return LieuService(session, actor_id=str(user.id)).modifier(
        lieu_id,
        LieuUpdateInput(
            nom=payload.nom,
            adresse=payload.adresse,
            ville=payload.ville,
            pays=payload.pays,
            zone=payload.zone,
        ),
    )
