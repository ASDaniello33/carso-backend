"""Catalogue des fournisseurs et modèles d'IA (INC-146).

Deux tables nouvelles, sans modification des tables existantes :

- ``fournisseurs_ia`` : la **connexion** (protocole, URL de base, clé chiffrée) ;
- ``modeles_ia`` : les modèles enregistrés chez un fournisseur, uniques par
  ``(fournisseur_id, nom_modele)``.

**Reprise de l'existant.** Si une configuration runtime est active au moment de
la migration, elle est **importée** dans le catalogue (un fournisseur
« Configuration actuelle » et son modèle) : l'administrateur retrouve dans la
liste ce qui tourne déjà, sans ressaisir la clé — elle est recopiée telle
quelle (déjà chiffrée par Fernet). L'import ne s'exécute que si le catalogue est
vide et ne supprime rien.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fournisseurs_ia",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nom", sa.String(length=120), nullable=False),
        sa.Column("protocole", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("api_key_chiffree", sa.String(length=1000), nullable=True),
        sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
        # Mêmes horodatages que ``TimestampMixin`` : gérés par le serveur.
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
    op.create_index("uq_fournisseurs_ia_nom", "fournisseurs_ia", ["nom"], unique=True)

    op.create_table(
        "modeles_ia",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "fournisseur_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fournisseurs_ia.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nom_modele", sa.String(length=150), nullable=False),
        sa.Column("libelle", sa.String(length=150), nullable=True),
        sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
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
        "uq_modeles_ia_fournisseur_nom",
        "modeles_ia",
        ["fournisseur_id", "nom_modele"],
        unique=True,
    )

    # Reprise de la configuration appliquée : le catalogue démarre avec ce qui
    # tourne déjà (aucune ressaisie, clé chiffrée recopiée).
    op.execute(
        """
        INSERT INTO fournisseurs_ia
            (id, nom, protocole, base_url, api_key_chiffree, actif,
             modifie_par, created_at, updated_at)
        SELECT gen_random_uuid(), 'Configuration actuelle', provider, base_url,
               api_key_chiffree, true, modifie_par, now(), now()
        FROM configurations_runtime_agent
        WHERE actif IS TRUE
          AND NOT EXISTS (SELECT 1 FROM fournisseurs_ia)
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    op.execute(
        """
        INSERT INTO modeles_ia
            (id, fournisseur_id, nom_modele, libelle, actif, modifie_par,
             created_at, updated_at)
        SELECT gen_random_uuid(), f.id, c.model, NULL, true, c.modifie_par, now(), now()
        FROM configurations_runtime_agent c
        JOIN fournisseurs_ia f ON f.protocole = c.provider
        WHERE c.actif IS TRUE
          AND NOT EXISTS (SELECT 1 FROM modeles_ia)
        ORDER BY c.created_at DESC
        LIMIT 1
        """
    )


def downgrade() -> None:
    op.drop_index("uq_modeles_ia_fournisseur_nom", table_name="modeles_ia")
    op.drop_table("modeles_ia")
    op.drop_index("uq_fournisseurs_ia_nom", table_name="fournisseurs_ia")
    op.drop_table("fournisseurs_ia")
