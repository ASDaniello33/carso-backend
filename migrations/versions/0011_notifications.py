"""Notifications in-app : table ``notifications`` (incrément 19).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26

Une notification est destinée à **un** utilisateur (FK ``utilisateurs``,
``ON DELETE CASCADE`` : supprimer un compte emporte ses notifications). Le
badge de la cloche compte les ``lue_at IS NULL`` — l'index composé
``(destinataire_id, lue_at)`` sert exactement cette requête. Diffusion in-app
seulement (décision validée) : pas d'email, pas de websocket.

Le downgrade supprime la table : les notifications sont un journal d'activité,
leur perte est assumée et sans effet sur les données métier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("destinataire_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=False),
        sa.Column("corps", sa.Text(), nullable=True),
        sa.Column("objet_type", sa.String(length=60), nullable=True),
        sa.Column("objet_id", sa.Uuid(), nullable=True),
        sa.Column("href", sa.String(length=255), nullable=True),
        sa.Column("lue_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["destinataire_id"],
            ["utilisateurs.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_notifications_destinataire_lue",
        "notifications",
        ["destinataire_id", "lue_at"],
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_destinataire_lue", table_name="notifications")
    op.drop_table("notifications")
