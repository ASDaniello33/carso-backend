"""MCP OAuth : autorisation déléguée et état observable du connecteur (05/10).

Trois manques comblés, constatés par vérification
(``docs/validation/VERIFICATION-MCP-OAUTH.md``) :

- le connecteur ne savait porter qu'une authentification **statique** (en-têtes
  chiffrés) : un serveur MCP exigeant OAuth était enregistrable, mais jamais
  authentifiable. ``mode_authentification`` rend le mode explicite
  (``aucune`` | ``en_tetes`` | ``oauth``) et ``oauth_client_id`` accepte le
  client **pré-enregistré** — indispensable quand le serveur refuse
  l'enregistrement dynamique (cas de Meta, mesuré : allowlist d'URI de
  redirection).
- les métadonnées de client et les jetons vivent dans ``oauth_etat_chiffre``,
  chiffrés au repos comme les en-têtes ; ``oauth_statut`` et
  ``oauth_derniere_erreur`` donnent l'état **sans jamais exposer un secret**.
- ``derniere_sonde`` mémorise le résultat de la dernière connexion réelle
  (outils chargés, erreur dépliée, horodatage) : l'interface peut dire pourquoi
  un connecteur ne sert rien, au lieu de le laisser paraître « actif ».

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "serveurs_mcp",
        sa.Column(
            "mode_authentification",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'aucune'"),
        ),
    )
    op.add_column(
        "serveurs_mcp",
        sa.Column("oauth_client_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "serveurs_mcp",
        sa.Column("oauth_etat_chiffre", sa.Text(), nullable=True),
    )
    op.add_column(
        "serveurs_mcp",
        sa.Column(
            "oauth_statut",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'non_requis'"),
        ),
    )
    op.add_column(
        "serveurs_mcp",
        sa.Column("oauth_derniere_erreur", sa.Text(), nullable=True),
    )
    op.add_column(
        "serveurs_mcp",
        sa.Column("derniere_sonde", sa.Text(), nullable=True),
    )
    # Le comportement d'avant la migration est *transcrit*, jamais deviné : un
    # connecteur qui portait des en-têtes reste en mode « en-tetes ».
    op.execute(
        "UPDATE serveurs_mcp SET mode_authentification = 'en_tetes' "
        "WHERE en_tetes_chiffres IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("serveurs_mcp", "derniere_sonde")
    op.drop_column("serveurs_mcp", "oauth_derniere_erreur")
    op.drop_column("serveurs_mcp", "oauth_statut")
    op.drop_column("serveurs_mcp", "oauth_etat_chiffre")
    op.drop_column("serveurs_mcp", "oauth_client_id")
    op.drop_column("serveurs_mcp", "mode_authentification")
