"""Route — recherche globale (lecture seule).

Contrôleur mince (AGENTS.md §2.4) : la sélection des familles et le plafond
vivent dans ``RechercheService``. Les bénéficiaires, données sensibles, ne
sont recherchés que pour les rôles qui les gèrent déjà (matrice RBAC :
``beneficiaire.gerer`` → administrateur et collaborateur) — décision validée.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.application.services.recherche_service import RechercheService
from app.domain.enums import RoleUtilisateur

router = APIRouter(
    prefix="/recherche",
    tags=["recherche"],
    dependencies=[Depends(get_current_user)],
)

_ROLES_BENEFICIAIRES = {RoleUtilisateur.ADMINISTRATEUR.value, RoleUtilisateur.COLLABORATEUR.value}


@router.get("/")
def rechercher(
    q: Annotated[str, Query(max_length=100, description="Terme recherché (2 caractères minimum).")],
    session: DbSession,
    utilisateur: CurrentUser,
) -> dict[str, Any]:
    """Recherche bornée : 5 résultats par famille, groupes avec URLs."""
    return RechercheService(
        session,
        peut_voir_beneficiaires=utilisateur.role in _ROLES_BENEFICIAIRES,
    ).rechercher(q)
