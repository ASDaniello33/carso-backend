"""Catalogue d'IA : défauts d'horodatage côté serveur (correctif de 0032).

La création des tables `fournisseurs_ia` / `modeles_ia` avait déclaré
`created_at`/`updated_at` **NOT NULL sans défaut**, alors que `TimestampMixin`
les laisse au serveur (`server_default=now()`), et que l'INSERT de l'ORM ne les
fournit pas. Résultat constaté : `NotNullViolation` au premier enregistrement
d'un fournisseur ou d'un modèle.

Cette migration pose le défaut manquant sur les deux tables (idempotente : elle
remplace un défaut éventuellement présent). `0032` porte désormais le même
défaut pour une installation neuve.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLES = ("fournisseurs_ia", "modeles_ia")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        op.alter_column(
            table,
            "updated_at",
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        op.alter_column(
            table,
            "updated_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
