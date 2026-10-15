"""MCP personnel : un contexte de connecteurs par utilisateur (04/10).

Les connecteurs MCP etaient **globaux** : un seul ensemble administre par
CARSO, fige dans les graphes d'agents au demarrage du Runtime. La colonne
``utilisateur_id`` rend la configuration **scopee par compte** :

- ``NULL`` = connecteur **global**, administre par CARSO, visible de tous ;
- renseigne = connecteur **personnel**, charge dans le contexte MCP de ce seul
  compte, invisible des autres et sans effet sur le Runtime partage.

Un changement de MCP personnel ne reconstruit donc plus le Runtime global :
l'agent est memoise par empreinte de configuration effective.

L'unicite devient **par proprietaire** : deux comptes peuvent nommer leur
connecteur ``github`` sans se gener. Les globaux gardent leur unicite par un
index partiel — en PostgreSQL, deux ``NULL`` ne sont pas consideres egaux dans
un index unique, sans quoi deux globaux homonymes passeraient.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "serveurs_mcp",
        sa.Column("utilisateur_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_serveurs_mcp_utilisateur_id",
        "serveurs_mcp",
        ["utilisateur_id"],
    )
    op.create_foreign_key(
        "fk_serveurs_mcp_utilisateur",
        "serveurs_mcp",
        "utilisateurs",
        ["utilisateur_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # L'ancien index unique global tombe : il interdit a deux comptes de
    # nommer egalement leur connecteur.
    op.drop_index("uq_serveurs_mcp_nom", table_name="serveurs_mcp")
    op.create_index(
        "uq_serveurs_mcp_proprietaire_nom",
        "serveurs_mcp",
        ["utilisateur_id", "nom"],
        unique=True,
    )
    op.create_index(
        "uq_serveurs_mcp_nom_global",
        "serveurs_mcp",
        ["nom"],
        unique=True,
        postgresql_where=sa.text("utilisateur_id IS NULL"),
        sqlite_where=sa.text("utilisateur_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_serveurs_mcp_nom_global", table_name="serveurs_mcp")
    op.drop_index("uq_serveurs_mcp_proprietaire_nom", table_name="serveurs_mcp")
    op.create_index("uq_serveurs_mcp_nom", "serveurs_mcp", ["nom"], unique=True)
    op.drop_constraint(
        "fk_serveurs_mcp_utilisateur", "serveurs_mcp", type_="foreignkey"
    )
    op.drop_index("ix_serveurs_mcp_utilisateur_id", table_name="serveurs_mcp")
    op.drop_column("serveurs_mcp", "utilisateur_id")
