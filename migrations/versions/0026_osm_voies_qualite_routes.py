"""Qualité des routes : cache des tags OpenStreetMap par voie.

OpenRouteService indique quelles voies un itinéraire emprunte (``osmium``) ;
les tags qui décrivent leur état (``smoothness``, ``tracktype``, ``access``…)
viennent d'Overpass. Une ligne par ``osm_id`` évite de redemander les mêmes
voies à chaque calcul, tout en laissant expirer une donnée qui peut vieillir.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "osm_voies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("osm_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "tags",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("expire_le", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_osm_voies_osm_id", "osm_voies", ["osm_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_osm_voies_osm_id", table_name="osm_voies")
    op.drop_table("osm_voies")
