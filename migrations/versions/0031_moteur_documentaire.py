"""Moteur documentaire — analyse, configuration et copie ancrée (INC-128).

Trois colonnes de données et trois colonnes d'état sur ``modeles_documents``,
plus une référence vers la copie technique instrumentée. Toutes sont
**nullables** ou portent une valeur par défaut : aucun modèle existant n'est
invalidé, aucune donnée n'est transformée.

Le fichier d'origine reste un ``Document`` de type ``template`` référencé par
``document_template_id`` ; la copie instrumentée est un **autre** Document
(``document_instrumente_id``). Une nouvelle version du modèle crée une nouvelle
ligne : les livrables déjà générés gardent leur configuration.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "modeles_documents",
        sa.Column("analyse", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "modeles_documents",
        sa.Column("configuration", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "modeles_documents",
        sa.Column("schema_generation", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "modeles_documents",
        sa.Column("document_instrumente_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "modeles_documents",
        sa.Column(
            "statut_analyse",
            sa.String(length=30),
            nullable=False,
            server_default="non_analyse",
        ),
    )
    op.add_column(
        "modeles_documents",
        sa.Column(
            "statut_validation",
            sa.String(length=30),
            nullable=False,
            server_default="brouillon",
        ),
    )
    op.create_foreign_key(
        "fk_modeles_documents_document_instrumente_id",
        "modeles_documents",
        "documents",
        ["document_instrumente_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_modeles_documents_document_instrumente_id",
        "modeles_documents",
        type_="foreignkey",
    )
    for colonne in (
        "statut_validation",
        "statut_analyse",
        "document_instrumente_id",
        "schema_generation",
        "configuration",
        "analyse",
    ):
        op.drop_column("modeles_documents", colonne)
