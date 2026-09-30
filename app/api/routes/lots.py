"""Routes — lots d'un appel à proposition (Lot C1, instruction/02 §B).

Deux routeurs, volontairement :

- ``router`` (``/appels-a-proposition/{id}/lots``) porte le cycle de vie d'un lot
  **dans le contexte de son appel** (liste, création, modification) ;
- ``router_lot`` (``/lots``) porte la lecture et la suppression **par identifiant
  de lot**. Un lot circule hors du contexte d'un appel (une offre référence son
  ``lot_id``) : sans cette lecture plate, un écran qui ne connaît que le lot ne
  peut pas le résoudre.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, get_current_user
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import LotCreate, LotRead, LotUpdate
from app.application.dto import LotInput, LotUpdateInput
from app.application.services import LotService
from app.domain.organization import Lot

router = APIRouter(
    prefix="/appels-a-proposition/{appel_a_proposition_id}/lots",
    tags=["lots"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/", response_model=list[LotRead])
def list_lots(appel_a_proposition_id: UUID, session: DbSession) -> list[Lot]:
    """Lots d'un appel à proposition (404 si l'appel est absent)."""
    return LotService(session).lister_pour_appel_a_proposition(appel_a_proposition_id)


@router.post("/", response_model=LotRead, status_code=status.HTTP_201_CREATED)
def create_lot(
    appel_a_proposition_id: UUID, payload: LotCreate, session: DbSession
) -> Lot:
    """Crée un lot rattaché à l'appel (409 si le numéro existe déjà)."""
    return LotService(session).ajouter(
        LotInput(
            appel_a_proposition_id=appel_a_proposition_id,
            numero=payload.numero,
            titre=payload.titre,
            zone=payload.zone,
            objectifs=payload.objectifs,
            resultats_attendus=payload.resultats_attendus,
            mission_description=payload.mission_description,
            partenariat=payload.partenariat,
            date_fin=payload.date_fin,
            donnees_source=payload.donnees_source,
        )
    )


@router.get("/{lot_id}", response_model=LotRead)
def get_lot(appel_a_proposition_id: UUID, lot_id: UUID, session: DbSession) -> Lot:
    """Consultation d'un lot (404 si absent)."""
    return LotService(session).obtenir(lot_id)


@router.patch("/{lot_id}", response_model=LotRead)
def update_lot(
    appel_a_proposition_id: UUID,
    lot_id: UUID,
    payload: LotUpdate,
    session: DbSession,
) -> Lot:
    """Modification partielle (``appel_a_proposition_id`` jamais modifiable)."""
    return LotService(session).modifier(
        lot_id,
        LotUpdateInput(
            numero=payload.numero,
            titre=payload.titre,
            zone=payload.zone,
            objectifs=payload.objectifs,
            resultats_attendus=payload.resultats_attendus,
            mission_description=payload.mission_description,
            partenariat=payload.partenariat,
            date_fin=payload.date_fin,
            donnees_source=payload.donnees_source,
        ),
    )


# --- Lecture et suppression par identifiant de lot ---------------------------

router_lot = APIRouter(
    prefix="/lots",
    tags=["lots"],
    dependencies=[Depends(get_current_user)],
)


@router_lot.get("/{lot_id}", response_model=LotRead)
def get_lot_par_identifiant(lot_id: UUID, session: DbSession) -> Lot:
    """Consultation d'un lot par son identifiant, hors contexte d'appel."""
    return LotService(session).obtenir(lot_id)


# Suppression cascadée (règle confirmée CARSO, 22/09) : un lot emporte ses offres,
# donc les missions qui en découlent, leurs sessions, participations, budgets et
# documents. L'appel, lui, survit.
enregistrer_suppression(router_lot, "lots")
