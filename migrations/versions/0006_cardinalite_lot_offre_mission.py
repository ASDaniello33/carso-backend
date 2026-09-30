"""Cardinalité confirmée : 1 lot → 1 offre → 1 mission.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-17

Ajoute les unicités ``offres_formation.lot_id`` et
``missions.offre_formation_id``. PostgreSQL autorise plusieurs NULL sur
cette dernière : les prestations directes (sans offre) restent possibles.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_offres_formation_lot_id", "offres_formation", ["lot_id"]
    )
    op.drop_index("ix_offres_formation_lot_id", table_name="offres_formation")
    op.create_unique_constraint(
        "uq_missions_offre_formation_id", "missions", ["offre_formation_id"]
    )
    op.drop_index("ix_missions_offre_formation_id", table_name="missions")


def downgrade() -> None:
    op.create_index(
        "ix_missions_offre_formation_id", "missions", ["offre_formation_id"]
    )
    op.drop_constraint(
        "uq_missions_offre_formation_id", "missions", type_="unique"
    )
    op.create_index(
        "ix_offres_formation_lot_id", "offres_formation", ["lot_id"]
    )
    op.drop_constraint(
        "uq_offres_formation_lot_id", "offres_formation", type_="unique"
    )
