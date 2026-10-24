"""Skills personnels : un guide court par compte, nommé `/nom` dans le chat.

Une table, pas un fichier : le contenu est écrit depuis l'interface, il
appartient à un compte, il ne voyage pas avec l'image et il ne doit jamais être
visible d'un autre compte. La contrainte unique porte sur
``(utilisateur_id, nom)`` — deux skills d'un même compte ne peuvent pas porter
le même ``/nom``.

Aucune donnée existante à réécrire : la fonctionnalité est nouvelle.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skills_personnels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("utilisateur_id", sa.Uuid(), nullable=False),
        sa.Column("nom", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
        # Horodatages gérés par le serveur (``TimestampMixin``) : sans défaut,
        # l'ORM n'envoie pas la colonne et PostgreSQL refuse l'INSERT.
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
        sa.ForeignKeyConstraint(
            ["utilisateur_id"], ["utilisateurs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_skills_personnels_utilisateur_nom",
        "skills_personnels",
        ["utilisateur_id", "nom"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_skills_personnels_utilisateur_nom", table_name="skills_personnels"
    )
    op.drop_table("skills_personnels")
