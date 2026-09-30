"""Configuration runtime des agents — table configurations_runtime_agent.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "configurations_runtime_agent",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column(
            "temperature", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_iterations", sa.Integer(), nullable=False, server_default=sa.text("25")
        ),
        sa.Column(
            "actif", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("modifie_par", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_configurations_runtime_agent"),
    )
    op.create_index(
        "ix_configurations_runtime_agent_actif",
        "configurations_runtime_agent",
        ["actif"],
    )
    op.create_index(
        "ix_configurations_runtime_agent_created_at",
        "configurations_runtime_agent",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_configurations_runtime_agent_created_at",
        table_name="configurations_runtime_agent",
    )
    op.drop_index(
        "ix_configurations_runtime_agent_actif",
        table_name="configurations_runtime_agent",
    )
    op.drop_table("configurations_runtime_agent")