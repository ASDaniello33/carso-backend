"""Service — notifications in-app (incrément 19).

Deux responsabilités, strictement séparées :

1. **Émission** : ``emettre`` est appelé par les services métier au fil des
   événements validés (liste validée par CARSO). Écrit dans la transaction
   courante — si la transaction échoue, la notification disparaît avec elle :
   jamais de notification pour une écriture annulée.
2. **Lecture** : ``lister``, ``compter_non_lues``, ``marquer_lue``,
   ``marquer_tout_lu`` pour l'API et la cloche.

Diffusion in-app uniquement (décision validée) : pas d'email, pas de websocket.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.notification import Notification

#: Plafond de sécurité : un utilisateur ne consulte jamais plus que ça d'un coup.
PLAFOND_LISTE = 50


class NotificationService:
    """Émission (services métier) et lecture (API) des notifications."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Émission (côté services métier, transaction courante)
    # ------------------------------------------------------------------

    def emettre(
        self,
        *,
        destinataires: list[UUID],
        type_notification: str,
        titre: str,
        corps: str | None = None,
        objet_type: str | None = None,
        objet_id: UUID | None = None,
        href: str | None = None,
    ) -> list[Notification]:
        """Crée une notification par destinataire, dans la transaction courante.

        Aucun commit (contrat service) : la notification vit ou meurt avec
        l'écriture métier qui la déclenche. Un destinataire dupliqué ou un
        destinataire vide est ignoré — l'émission ne doit jamais faire
        échouer l'écriture métier qui la porte.
        """
        vus: set[UUID] = set()
        notifications: list[Notification] = []
        for destinataire_id in destinataires:
            if not destinataire_id or destinataire_id in vus:
                continue
            vus.add(destinataire_id)
            notifications.append(
                Notification(
                    destinataire_id=destinataire_id,
                    type=type_notification,
                    titre=titre,
                    corps=corps,
                    objet_type=objet_type,
                    objet_id=objet_id,
                    href=href,
                )
            )
        if notifications:
            self._session.add_all(notifications)
            self._session.flush()
        return notifications

    # ------------------------------------------------------------------
    # Lecture (côté API)
    # ------------------------------------------------------------------

    def lister(self, utilisateur_id: UUID) -> list[Notification]:
        """Les notifications de l'utilisateur, non lues d'abord, puis par date."""
        return list(
            self._session.scalars(
                select(Notification)
                .where(Notification.destinataire_id == utilisateur_id)
                .order_by(
                    Notification.lue_at.is_not(None).asc(),
                    Notification.created_at.desc(),
                )
                .limit(PLAFOND_LISTE)
            )
        )

    def compter_non_lues(self, utilisateur_id: UUID) -> int:
        """Badge de la cloche."""
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.destinataire_id == utilisateur_id,
                    Notification.lue_at.is_(None),
                )
            )
            or 0
        )

    def marquer_lue(self, notification_id: UUID, utilisateur_id: UUID) -> Notification:
        """Marque une notification lue (idempotent, propriété vérifiée).

        Raises:
            NotFoundError: notification inexistante ou d'un autre utilisateur.
        """
        from app.core.errors import NotFoundError

        notification = self._session.get(Notification, notification_id)
        if notification is None or notification.destinataire_id != utilisateur_id:
            raise NotFoundError(f"Notification {notification_id} introuvable")
        if notification.lue_at is None:
            notification.lue_at = datetime.now(UTC)
            self._session.flush()
        return notification

    def marquer_tout_lu(self, utilisateur_id: UUID) -> int:
        """Passe toutes les non-lues en lues ; renvoie le nombre traité."""
        notifications = list(
            self._session.scalars(
                select(Notification).where(
                    Notification.destinataire_id == utilisateur_id,
                    Notification.lue_at.is_(None),
                )
            )
        )
        maintenant = datetime.now(UTC)
        for notification in notifications:
            notification.lue_at = maintenant
        if notifications:
            self._session.flush()
        return len(notifications)


__all__ = ["NotificationService"]
