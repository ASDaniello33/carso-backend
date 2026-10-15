"""Mot de passe oublié : codes de réinitialisation à usage unique (11/11).

Le flux « mot de passe oublié » envoie un code à 6 chiffres par email. Le
code en clair ne vit **jamais** en base : seul son hash HMAC (clé =
AUTH_SECRET) est stocké, avec une durée de vie explicite, un instant de
consommation et un compteur d'essais. Un compte ne porte qu'un code actif :
chaque nouvelle demande invalide les précédents.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "codes_reinitialisation_mot_de_passe",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "utilisateur_id",
            sa.Uuid(),
            sa.ForeignKey(
                "utilisateurs.id",
                name=op.f("fk_codes_reinitialisation_mot_de_passe_utilisateur_id_utilisateurs"),
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consomme_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tentatives", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
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
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_codes_reinitialisation_mot_de_passe")
        ),
    )
    op.create_index(
        "ix_codes_reinit_expire",
        "codes_reinitialisation_mot_de_passe",
        ["expire_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_codes_reinit_expire", table_name="codes_reinitialisation_mot_de_passe"
    )
    op.drop_table("codes_reinitialisation_mot_de_passe")
