"""Routes — personnes du vivier (Lot C1, instruction/02 §D).

Contrôleurs minces. Le CV se rattache via ``/documents`` (ancre ``equipe_id``),
jamais par une colonne ici (instruction/04 §equipes).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, get_current_user
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import EquipeCreate, EquipeRead, EquipeUpdate
from app.application.dto import EquipeInput, EquipeUpdateInput
from app.application.services import EquipeService
from app.domain.execution import Equipe

router = APIRouter(
    prefix="/equipes",
    tags=["equipes"],
    dependencies=[Depends(get_current_user)],
)

# --- Suppression cascadée (règle confirmée CARSO, 22/09) ----------------------
# Une personne du vivier emporte ses affectations, ses supports et ses documents
# (CV compris). Les missions et les sessions survivent.
enregistrer_suppression(router, "equipes")


@router.get("/", response_model=list[EquipeRead])
def list_equipes(
    session: DbSession,
    statut: Annotated[
        str | None, Query(description="Filtre facultatif : actif | archive")
    ] = None,
) -> list[Equipe]:
    """Vivier CARSO — complet, ou restreint à un statut (page Équipes).

    Sans filtre, la liste reste entière : les agents continuent de voir tout le
    vivier, archivés compris (l'archivage ne masque rien à l'analyse).
    """
    return EquipeService(session).lister(statut=statut)


@router.get("/{equipe_id}", response_model=EquipeRead)
def get_equipe(equipe_id: UUID, session: DbSession) -> Equipe:
    """Consultation d'une personne (404 si absente)."""
    return EquipeService(session).obtenir(equipe_id)


@router.post("/", response_model=EquipeRead, status_code=status.HTTP_201_CREATED)
def create_equipe(
    payload: EquipeCreate, session: DbSession
) -> Equipe:
    """Enregistre une personne du vivier."""
    return EquipeService(session).creer(
        EquipeInput(
            nom=payload.nom,
            prenom=payload.prenom,
            email=payload.email,
            telephone=payload.telephone,
            profil=payload.profil,
            statut=payload.statut,
            role_compte=payload.role_compte,
        )
    )


@router.patch("/{equipe_id}", response_model=EquipeRead)
def update_equipe(
    equipe_id: UUID, payload: EquipeUpdate, session: DbSession
) -> Equipe:
    """Modification partielle (seuls les champs fournis sont appliqués)."""
    return EquipeService(session).modifier(
        equipe_id,
        EquipeUpdateInput(
            nom=payload.nom,
            prenom=payload.prenom,
            email=payload.email,
            telephone=payload.telephone,
            profil=payload.profil,
            statut=payload.statut,
            role_compte=payload.role_compte,
        ),
    )


@router.post("/{equipe_id}/archivage", response_model=EquipeRead)
def archiver_equipe(equipe_id: UUID, session: DbSession) -> Equipe:
    """Archive la personne (réversible) : hors des listes actives, historique intact."""
    return EquipeService(session).archiver(equipe_id)


@router.post("/{equipe_id}/reactivation", response_model=EquipeRead)
def reactiver_equipe(equipe_id: UUID, session: DbSession) -> Equipe:
    """Remet une personne archivée dans le vivier actif."""
    return EquipeService(session).reactiver(equipe_id)
