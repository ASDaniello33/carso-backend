"""Annonces : pièces jointes multiples (incrément 33).

Une annonce peut désormais porter **plusieurs** images/documents. Une
``PieceAnnonce`` par fichier (nom, chemin logique, MIME, taille) — les
anciennes colonnes ``annonces.piece_*`` (pièce unique, incrément 20) sont
conservées et **copiées** dans la nouvelle table au premier lancement :
aucune pièce existante ne disparaît de l'interface.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "annonces_pieces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "annonce_id",
            sa.Uuid(),
            sa.ForeignKey("annonces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nom", sa.String(255), nullable=False),
        sa.Column("chemin", sa.String(1024), nullable=False),
        sa.Column("mime", sa.String(255)),
        sa.Column("taille", sa.Integer()),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_annonces_pieces_annonce", "annonces_pieces", ["annonce_id", "position"]
    )
    # Migration des pièces uniques existantes : l'ancienne annonce (si elle
    # porte encore une pièce) reçoit sa pièce dans le nouveau format.
    op.execute(
        """
        INSERT INTO annonces_pieces (id, annonce_id, nom, chemin, mime, taille, position)
        SELECT gen_random_uuid(), a.id, a.piece_nom, a.piece_chemin, a.piece_mime,
               a.piece_taille, 0
        FROM annonces a
        WHERE a.piece_chemin IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM annonces_pieces p WHERE p.annonce_id = a.id
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_annonces_pieces_annonce", table_name="annonces_pieces")
    op.drop_table("annonces_pieces")
