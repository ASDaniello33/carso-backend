"""Fenêtre de contexte définie manuellement pour un modèle qui ne la publie pas.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-23

Demande utilisateur du 23/11 : quand le fournisseur ne publie pas
``context_length``, l'administrateur doit pouvoir déclarer la fenêtre lui-même ;
sans valeur déclarée, le repli ``AGENT_MAX_INPUT_TOKENS`` (200 000) reste
appliqué comme avant.

Colonne nullable : aucune ligne existante n'est réécrite, et une configuration
sans valeur garde exactement le comportement d'avant. Une valeur publiée par le
fournisseur reste prioritaire — cette colonne ne comble qu'une absence.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "configurations_runtime_agent",
        sa.Column("fenetre_contexte_jetons", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("configurations_runtime_agent", "fenetre_contexte_jetons")
