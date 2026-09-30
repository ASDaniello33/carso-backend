"""Routes — notifications in-app (incrément 19).

Contrôleurs minces. Chaque utilisateur ne voit que **ses** notifications :
l'identifiant vient du JWT (``get_current_user``), jamais de la requête.
Lecture par polling léger (cloche : compte non-lues ; page : liste).
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.application.services.notification_service import NotificationService
from app.domain.notification import Notification

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)


def _lire_notification(notification: Notification) -> dict[str, Any]:
    return {
        "id": str(notification.id),
        "type": notification.type,
        "titre": notification.titre,
        "corps": notification.corps,
        "objet_type": notification.objet_type,
        "objet_id": str(notification.objet_id) if notification.objet_id else None,
        "href": notification.href,
        "lue": notification.lue_at is not None,
        "created_at": notification.created_at.isoformat(),
    }


@router.get("/")
def lister_notifications(
    session: DbSession, utilisateur: CurrentUser
) -> dict[str, Any]:
    """Notifications de l'utilisateur + compteur non-lues (badge cloche)."""
    service = NotificationService(session)
    notifications = service.lister(utilisateur.id)
    return {
        "notifications": [_lire_notification(n) for n in notifications],
        "non_lues": service.compter_non_lues(utilisateur.id),
    }


@router.post("/{notification_id}/lecture", status_code=status.HTTP_204_NO_CONTENT)
def marquer_lue(notification_id: UUID, session: DbSession, utilisateur: CurrentUser) -> None:
    """Marque une notification lue (idempotent, propriété vérifiée)."""
    NotificationService(session).marquer_lue(notification_id, utilisateur.id)


@router.post("/lecture-totale", status_code=status.HTTP_204_NO_CONTENT)
def marquer_tout_lu(session: DbSession, utilisateur: CurrentUser) -> None:
    """Marque toutes les notifications de l'utilisateur comme lues."""
    NotificationService(session).marquer_tout_lu(utilisateur.id)
