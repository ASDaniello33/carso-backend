"""Paramètres d'administration : clé du provider chiffrée, surcharges d'agent, MCP.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22

Trois ajouts, un même besoin : l'administrateur doit pouvoir régler le système
depuis la page Paramètres sans redémarrer le backend ni écrire dans ``.env``.

1. ``configurations_runtime_agent.api_key_chiffree`` — la clé du provider peut
   être saisie par l'administrateur. Elle n'est **jamais** stockée en clair :
   colonne nullable, contient un jeton Fernet (``app.core.chiffrement``). La clé
   du ``.env`` reste le repli quand la colonne est vide.
2. ``surcharges_agent`` — réglages par agent : outils retirés de l'allow-list,
   instructions supplémentaires, skills ajoutés. Une seule ligne par agent.
3. ``serveurs_mcp`` — connecteurs MCP activables par agent, en-têtes
   d'authentification chiffrés.

Aucune donnée existante n'est modifiée ni reprise : les lignes de configuration
déjà présentes gardent ``api_key_chiffree = NULL`` et continuent de fonctionner
avec la clé du ``.env``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Clé du provider, chiffrée au repos ------------------------------
    op.add_column(
        "configurations_runtime_agent",
        sa.Column("api_key_chiffree", sa.String(length=1000), nullable=True),
    )

    # --- 2. Surcharges par agent --------------------------------------------
    op.create_table(
        "surcharges_agent",
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column(
            "outils_desactives",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("instructions_supplementaires", sa.Text(), nullable=True),
        sa.Column(
            "skills_ajoutes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "actif", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_surcharges_agent"),
    )
    op.create_index(
        "uq_surcharges_agent_agent_id", "surcharges_agent", ["agent_id"], unique=True
    )

    # --- 3. Connecteurs MCP --------------------------------------------------
    op.create_table(
        "serveurs_mcp",
        sa.Column("nom", sa.String(length=100), nullable=False),
        sa.Column(
            "transport",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'streamable_http'"),
        ),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("commande", sa.String(length=500), nullable=True),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "agents",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("en_tetes_chiffres", sa.Text(), nullable=True),
        sa.Column(
            "actif", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_serveurs_mcp"),
    )
    op.create_index("uq_serveurs_mcp_nom", "serveurs_mcp", ["nom"], unique=True)
    op.create_index("ix_serveurs_mcp_actif", "serveurs_mcp", ["actif"])


def downgrade() -> None:
    op.drop_index("ix_serveurs_mcp_actif", table_name="serveurs_mcp")
    op.drop_index("uq_serveurs_mcp_nom", table_name="serveurs_mcp")
    op.drop_table("serveurs_mcp")
    op.drop_index("uq_surcharges_agent_agent_id", table_name="surcharges_agent")
    op.drop_table("surcharges_agent")
    op.drop_column("configurations_runtime_agent", "api_key_chiffree")
