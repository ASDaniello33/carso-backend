"""Modèle — notifications in-app (incrément 19).

Une notification est un message **destiné à un utilisateur précis**, émis par
la couche service au fil des événements métier validés (jamais par les agents,
jamais par les routes). Diffusion in-app seulement (décision validée) : pas
d'email, pas de websocket — lecture par polling léger.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin


class Notification(Base, UuidPkMixin, TimestampMixin):
    """Notification in-app d'un utilisateur : type, titre, objet métier lié."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_destinataire_lue", "destinataire_id", "lue_at"),
        Index("ix_notifications_created_at", "created_at"),
    )

    destinataire_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False
    )
    #: Type métier stable (ex. ``offre_a_valider``, ``session_annulee``) —
    #: sert à l'icône et au libellé côté client, jamais interprété en logique.
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    corps: Mapped[str | None] = mapped_column(Text)
    #: Objet métier lié, pour la navigation (ex. ``offre`` + l'id).
    objet_type: Mapped[str | None] = mapped_column(String(60))
    objet_id: Mapped[UUID | None] = mapped_column()
    href: Mapped[str | None] = mapped_column(String(255))
    #: Null = non lue ; instant de lecture sinon.
    lue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["Notification"]
