"""Affectations : intitulé libre du rôle dans la mission (« Formateur en IA »).

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-19

Demande utilisateur du 19/11 : un rôle de mission doit pouvoir être précisé
(« Formateur en IA ») sans changer le rôle de compte de la personne. Le rôle de
**référence** reste ``role_dans_mission`` (vocabulaire ``RoleMission``) : c'est
lui que lisent les règles métier (``est_formateur``, supports de formation). La
précision libre vit dans une colonne distincte, facultative.

Colonne nullable : aucune ligne existante n'est réécrite, et une affectation
sans précision garde exactement le comportement d'avant.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "affectations_equipe",
        sa.Column("intitule_role", sa.String(length=150), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("affectations_equipe", "intitule_role")
