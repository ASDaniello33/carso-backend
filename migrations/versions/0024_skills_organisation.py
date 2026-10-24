"""Skills d'organisation : le guide commun, écrit par l'administration.

Le skill n'appartient plus à un compte. Il change la façon dont **tous** les
agents répondent, donc son écriture est réservée à l'administration (garde
``AdminUser`` sur les routes) : la table perd ``utilisateur_id`` et l'unicité
devient globale (``uq_skills_nom``).

Ce que fait cette révision :

1. retrait de l'index ``uq_skills_personnels_utilisateur_nom`` ;
2. retrait de la clé étrangère puis de la colonne ``utilisateur_id`` ;
3. renommage de la table ``skills_personnels`` → ``skills`` ;
4. unicité du nom, tous comptes confondus.

Les guides déjà écrits restent en place : ils deviennent des guides communs.
Le retour arrière recrée la table d'origine **vide** — la répartition par compte
n'est pas reconstituable, et une données d'audit ne se réécrit pas.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_skills_personnels_utilisateur_nom", table_name="skills_personnels"
    )
    # Nom posé par la convention de nommage de ``Base`` (``fk_<table>_<col>_<cible>``),
    # pas le nom implicite de PostgreSQL.
    op.drop_constraint(
        "fk_skills_personnels_utilisateur_id_utilisateurs",
        "skills_personnels",
        type_="foreignkey",
    )
    op.drop_column("skills_personnels", "utilisateur_id")
    op.rename_table("skills_personnels", "skills")
    op.create_index("uq_skills_nom", "skills", ["nom"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_skills_nom", table_name="skills")
    op.drop_table("skills")
    op.create_table(
        "skills_personnels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("utilisateur_id", sa.Uuid(), nullable=False),
        sa.Column("nom", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
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
