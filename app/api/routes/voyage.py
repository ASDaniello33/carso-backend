"""Routes—paramètres de voyage (origine par défaut, coût du carburant).

La lecture est ouverte à tout compte connecté : le formulaire d'itinéraire a
besoin des valeurs par défaut pour les proposer, sans exposer autre chose que
ces réglages. La modification est réservée à l'administration — l'origine du
siège et le prix du litre sont des décisions d'organisation.
"""

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.schemas import ParametresVoyageRead, ParametresVoyageUpdate
from app.application.services.parametres_voyage_service import (
    ParametresVoyageMiseAJour,
    ParametresVoyageService,
)

router = APIRouter(
    prefix="/parametres",
    tags=["parametres"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/voyage", response_model=ParametresVoyageRead)
def lire_parametres_voyage(session: DbSession) -> ParametresVoyageRead:
    """Valeurs par défaut du calcul d'itinéraire (origine, carburant)."""
    return ParametresVoyageRead.model_validate(
        ParametresVoyageService(session).lire()
    )


@router.put("/voyage", response_model=ParametresVoyageRead)
def modifier_parametres_voyage(
    payload: ParametresVoyageUpdate,
    session: DbSession,
    user: AdminUser,
) -> ParametresVoyageRead:
    """Met à jour les réglages (champ absent conservé, origine complète à null = retirée)."""
    ligne = ParametresVoyageService(session, actor_id=str(user.id)).modifier(
        ParametresVoyageMiseAJour(
            origine_libelle=payload.origine_libelle,
            origine_latitude=payload.origine_latitude,
            origine_longitude=payload.origine_longitude,
            consommation_l_100km=payload.consommation_l_100km,
            prix_litre=payload.prix_litre,
            devise=payload.devise,
        )
    )
    return ParametresVoyageRead.model_validate(ligne)
