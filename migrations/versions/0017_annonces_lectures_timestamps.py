"""Correction : horodatages manquants sur annonces_lectures.

Le modèle ``AnnonceLecture`` hérite de ``TimestampMixin`` (created_at /
updated_at), mais la migration 0015 n'a créé ni l'un ni l'autre : tout INSERT
de l'ORM échouait en production PostgreSQL (``UndefinedColumn``) — d'où le
badge « annonces non lues » qui ne se vidait jamais (le POST
``/social/annonces/lecture-tout`` renvoyait 500).

Revision ID: 0017
Revises: 0015
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "annonces_lectures",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "annonces_lectures",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("annonces_lectures", "updated_at")
    op.drop_column("annonces_lectures", "created_at")
