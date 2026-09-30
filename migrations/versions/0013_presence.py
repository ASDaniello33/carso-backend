"""Présence du chat social : ``utilisateurs.derniere_activite_at`` (incrément 21).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-26

Le statut (en ligne / absent / hors ligne) est **calculé à la lecture** depuis
cette colonne — rien n'est stocké, rien n'expire. La colonne est nullable :
les comptes existants naissent « hors ligne » jusqu'à leur premier heartbeat.
Le downgrade retire la colonne (aucune donnée métier affectée).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "utilisateurs",
        sa.Column("derniere_activite_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_utilisateurs_derniere_activite",
        "utilisateurs",
        ["derniere_activite_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_utilisateurs_derniere_activite", table_name="utilisateurs")
    op.drop_column("utilisateurs", "derniere_activite_at")
