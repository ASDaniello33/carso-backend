"""Fenêtre de contexte déclarée pour chaque modèle du catalogue.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-23

Demande utilisateur du 23/11 : le catalogue des fournisseurs et modèles doit
afficher la fenêtre de contexte de **chaque** modèle et permettre de la déclarer
là aussi, pas seulement pour le modèle appliqué au runtime.

Colonne nullable : aucune ligne existante n'est réécrite, et un modèle sans
valeur garde le comportement d'avant (valeur publiée par le fournisseur, sinon
repli ``AGENT_MAX_INPUT_TOKENS``). Une valeur publiée reste prioritaire.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "modeles_ia",
        sa.Column("fenetre_contexte_jetons", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("modeles_ia", "fenetre_contexte_jetons")
