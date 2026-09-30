"""Baseline migration — schema intentionally empty.

Business entities (organisations, appels_offre, lots, offres_formation, missions,
equipes, documents, ...) arrive in Phase 3 after validation of the domain model
(instruction/11, Phase 2/3). This revision establishes the alembic_version anchor.

Revision ID: 0001
Revises:
Create Date: 2026-09-14

"""
from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
