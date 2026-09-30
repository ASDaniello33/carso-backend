"""Routes — registre des modèles de documents par organisation cliente (instruction/06 §7).

Contrôleurs minces sur ``ModeleDocumentService``. La résolution du modèle actif
échoue explicitement (404) lorsqu'aucun modèle n'est enregistré : aucun modèle
par défaut n'est appliqué en silence.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.schemas import ModeleDocumentCreate, ModeleDocumentRead
from app.application.services import ModeleDocumentService
from app.domain.execution import ModeleDocument

router = APIRouter(
    prefix="/organisations/{organisation_id}/modeles-documents",
    tags=["modeles-documents"],
    dependencies=[Depends(get_current_user)],
)


@router.post("", response_model=ModeleDocumentRead, status_code=status.HTTP_201_CREATED)
def create_modele(
    organisation_id: UUID,
    payload: ModeleDocumentCreate,
    session: DbSession,
    user: CurrentUser,
) -> ModeleDocument:
    """Enregistre une nouvelle version de modèle pour (organisation, type)."""
    return ModeleDocumentService(session).enregistrer_modele(
        organisation_id,
        nom=payload.nom,
        type_document=payload.type_document,
        document_template_id=payload.document_template_id,
        registered_by=str(user.id),
    )


@router.get("", response_model=list[ModeleDocumentRead])
def list_modeles(
    organisation_id: UUID,
    session: DbSession,
) -> list[ModeleDocument]:
    """Modèles enregistrés pour une organisation (toutes versions)."""
    return ModeleDocumentService(session).lister(organisation_id)


@router.get("/actif", response_model=ModeleDocumentRead)
def get_modele_actif(
    organisation_id: UUID,
    type_document: Annotated[str, Query(description="Type de document ciblé")],
    session: DbSession,
) -> ModeleDocument:
    """Modèle de plus haute version pour ce type — 404 si aucun n'est enregistré."""
    return ModeleDocumentService(session).resoudre_modele_actif(
        organisation_id, type_document
    )
