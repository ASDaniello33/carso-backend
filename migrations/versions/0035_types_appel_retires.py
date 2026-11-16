"""Types d'appel retires : une liste de decisions, pas une suppression de donnees.

Decision du 15/11/2026 : retirer un type d'appel le sort de la liste proposee au
formulaire, et **rien d'autre**. Les appels deja enregistres avec ce type gardent
leur valeur ; l'interface l'affiche comme supprime, et l'utilisateur peut le
corriger a tout moment.

La table ne stocke donc que la decision : quelle valeur ne plus proposer, et
pourquoi. Aucune colonne de ``appels`` n'est touchee, aucune ligne d'appel n'est
reecrite ou supprimee : c'est l'histoire du dossier qu'on ne reecrit pas.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "types_appel_retires",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type_appel", sa.String(length=50), nullable=False),
        sa.Column("motif", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_types_appel_retires"),
        sa.UniqueConstraint("type_appel", name="uq_types_appel_retires_valeur"),
    )


def downgrade() -> None:
    """Remet tous les types retires dans la liste proposee.

    Les appels n'ayant jamais perdu leur valeur, defaire cette migration ne
    restaure aucune donnee metier : elle efface seulement la liste des retraits.
    """
    op.drop_table("types_appel_retires")
