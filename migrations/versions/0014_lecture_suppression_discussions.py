"""Lecture et suppression de discussions (chat social) — incrément 23.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-27

- ``membres_conversation.derniere_lecture_at`` : badge « messages non lus »
  (null = tout ce qui précède compte comme non lu).
- ``membres_conversation.archive_at`` : une directe supprimée sort de **la
  liste de ce membre** seulement (l'autre conserve ; un message la fait
  réapparaître).
- ``conversations.archive_at`` : une conférence supprimée par son animateur
  sort des listes de tous les membres (messages conservés en archive).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "membres_conversation",
        sa.Column("derniere_lecture_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "membres_conversation",
        sa.Column("archive_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("archive_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_membres_conversation_archive",
        "membres_conversation",
        ["archive_at"],
    )
    op.create_index(
        "ix_conversations_archive",
        "conversations",
        ["archive_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_archive", table_name="conversations")
    op.drop_index("ix_membres_conversation_archive", table_name="membres_conversation")
    op.drop_column("conversations", "archive_at")
    op.drop_column("membres_conversation", "archive_at")
    op.drop_column("membres_conversation", "derniere_lecture_at")
