"""Route — tableau de bord (lecture seule).

Contrôleur mince (AGENTS.md §2.4) : la logique d'agrégation vit entièrement
dans ``TableauBordService``. Lecture seule : la route ne mute rien.
"""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, get_current_user
from app.application.services.tableau_bord_service import TableauBordService

router = APIRouter(
    prefix="/tableau-bord",
    tags=["tableau-bord"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/")
def lire_tableau_bord(session: DbSession) -> dict[str, Any]:
    """Aperçu consolidé du dashboard : compteurs, alertes, activité récente."""
    return TableauBordService(session).apercu()
