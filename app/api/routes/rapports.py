"""Route—rapports et indicateurs (lecture seule).

Contrôleur mince (AGENTS.md §2.4)—la logique d'agrégation vit dans
``RapportsService``. La route ne mute rien—c'est la même famille de lecture
que ``query_analytics``, servie à la page Rapports pour son tableau de bord
par défaut (l'agent statistique produit par ailleurs des dashboards générés).

**Ouvert aux comptes restreints** (décision du parcours formateur)—les
agrégations sont bornées au périmètre calculé du compte, donc un formateur lit
**ses** indicateurs—ceux de ses missions affectées—et non ceux de
l'entreprise. Une route GET n'est jamais ouverte sans ``calculer_perimetre``
(``tests/test_surface_permissions.py``).
"""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, get_current_user
from app.api.permissions import PerimetreDep
from app.application.services.rapports_service import RapportsService

router = APIRouter(
    prefix="/rapports",
    tags=["rapports"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/tableau-bord")
def lire_tableau_bord(session: DbSession, perimetre: PerimetreDep) -> dict[str, Any]:
    """Tableau de bord par défaut : agrégations déterministes en widgets.

    Compte interne : la vue d'ensemble de l'activité. Formateur : la même
    page, calculée sur son périmètre.
    """
    return RapportsService(session, perimetre=perimetre).tableau_bord_defaut()