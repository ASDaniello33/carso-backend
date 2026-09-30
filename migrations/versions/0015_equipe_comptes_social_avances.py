"""Équipe ↔ comptes, replies, lectures d'annonces, photos de profil — incrément 24.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27

- ``equipes.role_compte`` : rôle de compte attribué au futur utilisateur
  (``collaborateur`` | ``formateur``) — « rôle équipe = rôle utilisateur ».
- ``utilisateurs.equipe_id`` : liaison unique compte ↔ personne du vivier ;
  nom/prenom/email/role du compte sont **synchronisés** depuis l'équipe.
- ``utilisateurs.photo_profil_chemin`` : avatar personnel (scope ``chat``).
- ``messages.reponse_a_id`` : réponse à un message spécifique (reply).
- ``annonces_lectures`` : lecture d'annonce par utilisateur (badge non-lus).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "equipes",
        sa.Column("role_compte", sa.String(50), nullable=True),
    )
    op.add_column(
        "utilisateurs",
        sa.Column(
            "equipe_id",
            sa.Uuid(),
            sa.ForeignKey("equipes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "utilisateurs",
        sa.Column("photo_profil_chemin", sa.String(1024), nullable=True),
    )
    op.create_index(
        "ix_utilisateurs_equipe", "utilisateurs", ["equipe_id"], unique=True
    )
    op.add_column(
        "messages",
        sa.Column(
            "reponse_a_id",
            sa.Uuid(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_messages_reponse_a", "messages", ["reponse_a_id"])
    op.create_table(
        "annonces_lectures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "annonce_id",
            sa.Uuid(),
            sa.ForeignKey("annonces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "utilisateur_id",
            sa.Uuid(),
            sa.ForeignKey("utilisateurs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lu_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("annonce_id", "utilisateur_id", name="uq_annonces_lectures_paire"),
        sa.Index("ix_annonces_lectures_utilisateur", "utilisateur_id"),
    )


def downgrade() -> None:
    op.drop_table("annonces_lectures")
    op.drop_index("ix_messages_reponse_a", table_name="messages")
    op.drop_column("messages", "reponse_a_id")
    op.drop_index("ix_utilisateurs_equipe", table_name="utilisateurs")
    op.drop_column("utilisateurs", "photo_profil_chemin")
    op.drop_column("utilisateurs", "equipe_id")
    op.drop_column("equipes", "role_compte")
