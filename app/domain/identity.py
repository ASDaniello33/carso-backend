"""Comptes utilisateurs (AUTH) — liés au vivier Equipe (incrément 24).

Décision validée : **un compte hors admin doit correspondre à une personne du
vivier**. ``equipe_id`` porte la liaison (unique) ; nom, prénom, email et rôle
du compte sont **synchronisés** depuis l'équipe (modification/archive d'une
personne ⇒ compte mis à jour/suspendu). L'admin n'est pas lié au vivier.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin
from app.domain.enums import RoleUtilisateur, StatutUtilisateur


class Utilisateur(Base, UuidPkMixin, TimestampMixin):
    """Compte de connexion. Un compte pending n'a aucun droit métier."""

    __tablename__ = "utilisateurs"
    __table_args__ = (
        UniqueConstraint("email", name="uq_utilisateurs_email"),
        Index("ix_utilisateurs_statut", "statut"),
        Index("ix_utilisateurs_derniere_activite", "derniere_activite_at"),
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    prenom: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=RoleUtilisateur.EN_ATTENTE.value,
        server_default=RoleUtilisateur.EN_ATTENTE.value,
    )
    statut: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=StatutUtilisateur.PENDING.value,
        server_default=StatutUtilisateur.PENDING.value,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column()
    #: Présence (chat social, incrément 21) : instant du dernier signal
    #: (heartbeat du frontend ou toute requête authentifiée). Le statut est
    #: **calculé** à la lecture — jamais stocké : en_ligne < 2 min,
    #: absent < 15 min ("En ligne il y a 3 min"), hors ligne sinon.
    derniere_activite_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Personne du vivier liée (unique) — null pour l'administrateur.
    equipe_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("equipes.id", ondelete="SET NULL")
    )
    #: Avatar personnel (chemin logique, scope ``chat``).
    photo_profil_chemin: Mapped[str | None] = mapped_column(String(1024))
