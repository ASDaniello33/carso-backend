"""Agent personnel : configuration par compte et connecteurs personnels (10/10).

Deux changements, un même objectif : l'agent personnel devient le lieu des
réglages et des outils **propres à un compte**, sans jamais toucher aux agents
partagés de CARSO.

- ``configurations_agent_personnel`` — une ligne par compte (instructions,
  outils retirés). Elle n'est pas une ``surcharge_agent`` (une ligne par agent,
  appliquée à tous) : un compte règle *son* agent, pas celui des autres.
- **Normalisation des connecteurs personnels** : un connecteur déclaré par un
  compte ne charge plus ses outils dans un agent partagé (``agent_statistique``,
  ``agent_rh``…). Ses outils distants, ses pannes et ses jetons restent dans
  ``agent_personnel``. Les lignes existantes sont réécrites : sans cette
  migration, un connecteur personnel resterait attaché à un agent partagé et
  continuerait de charger ses outils dans le graphe de cet agent.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-10
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Seul agent qui charge un connecteur personnel (règle applicative, cf.
#: ``app.domain.mcp.AGENT_CONNECTEURS_PERSONNELS``).
AGENT_PERSONNEL = "agent_personnel"


def _reecrire_agents_personnels() -> None:
    """Réattache les connecteurs personnels à l'agent personnel.

    Passage par des mises à jour ligne à ligne plutôt qu'un ``UPDATE`` global
    avec littéral JSONB : la migration reste exécutable sur un moteur où la
    colonne ``agents`` n'est pas un JSONB typé (SQLite des tests, par exemple),
    et la valeur écrite reste exactement celle que produit l'application.

    En mode **offline** (``alembic upgrade --sql``), aucune base n'est connectée :
    il n'y a pas de lignes à lire ni de SQL à émettre pour elles (chaque ligne
    est parcourue). Le DDL de la table est bien émis ; la réécriture des données
    appartient à l'exécution réelle.
    """
    if op.get_context().as_sql:
        return
    liaison = op.get_bind()
    lignes = liaison.execute(
        sa.text("SELECT id, agents FROM serveurs_mcp WHERE utilisateur_id IS NOT NULL")
    ).fetchall()
    for identifiant, agents in lignes:
        if isinstance(agents, str):
            try:
                agents = json.loads(agents)
            except (TypeError, ValueError):
                agents = []
        if list(agents or []) == [AGENT_PERSONNEL]:
            continue
        liaison.execute(
            sa.text(
                "UPDATE serveurs_mcp SET agents = :agents WHERE id = :identifiant"
            ),
            {"agents": json.dumps([AGENT_PERSONNEL]), "identifiant": identifiant},
        )


def upgrade() -> None:
    op.create_table(
        "configurations_agent_personnel",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("utilisateur_id", sa.Uuid(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column(
            "outils_desactives",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "actif", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
        # Horodatages gérés par le serveur, comme partout ailleurs
        # (``TimestampMixin``, migration 0009) : sans ce défaut, l'ORM n'envoie
        # pas ces colonnes à l'INSERT et PostgreSQL refuse la ligne
        # (``NotNullViolation`` sur ``created_at``).
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
        "uq_configurations_agent_personnel_utilisateur",
        "configurations_agent_personnel",
        ["utilisateur_id"],
        unique=True,
    )
    _reecrire_agents_personnels()


def downgrade() -> None:
    # La migration de données n'est pas annulée : l'ancien rattachement d'un
    # connecteur personnel à un agent partagé n'est pas mémorisé (et le
    # recréer serait décider à la place de l'utilisateur). Seule la table de
    # configuration est retirée.
    op.drop_index(
        "uq_configurations_agent_personnel_utilisateur",
        table_name="configurations_agent_personnel",
    )
    op.drop_table("configurations_agent_personnel")
