"""Renommage AppelOffre → AppelAProposition (réversible, sans perte de données).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17

Conserve les identifiants et les lignes existantes. Seuls les noms de table,
colonnes, contraintes et index changent. Les valeurs persistées (statuts,
``donnees_extraites``) restent intactes.

Le type documentaire historique ``appel_offre`` n'est pas migré ici : le
vocabulaire Python (``TypeDocument``) distingue désormais
``appel_proposition`` / ``appel_a_proposition``. Les fichiers déjà classés
sous ``appel_offre/{id}/`` restent lisibles via le scope historique.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- table principale -----------------------------------------------------
    op.rename_table("appels_offre", "appels_a_proposition")
    op.execute(
        "ALTER TABLE appels_a_proposition "
        "RENAME CONSTRAINT pk_appels_offre TO pk_appels_a_proposition"
    )
    op.execute(
        "ALTER TABLE appels_a_proposition "
        "RENAME CONSTRAINT uq_appels_offre_reference "
        "TO uq_appels_a_proposition_reference"
    )
    op.execute(
        "ALTER TABLE appels_a_proposition "
        "RENAME CONSTRAINT fk_appels_offre_organisation_id_organisations "
        "TO fk_appels_a_proposition_organisation_id_organisations"
    )
    op.execute(
        "ALTER INDEX ix_appels_offre_organisation_id "
        "RENAME TO ix_appels_a_proposition_organisation_id"
    )
    op.execute(
        "ALTER INDEX ix_appels_offre_statut RENAME TO ix_appels_a_proposition_statut"
    )

    # --- lots -----------------------------------------------------------------
    op.alter_column("lots", "appel_offre_id", new_column_name="appel_a_proposition_id")
    op.execute(
        "ALTER TABLE lots "
        "RENAME CONSTRAINT fk_lots_appel_offre_id_appels_offre "
        "TO fk_lots_appel_a_proposition_id_appels_a_proposition"
    )
    op.execute(
        "ALTER TABLE lots "
        "RENAME CONSTRAINT uq_lots_appel_offre_id_numero "
        "TO uq_lots_appel_a_proposition_id_numero"
    )
    op.execute(
        "ALTER INDEX ix_lots_appel_offre_id "
        "RENAME TO ix_lots_appel_a_proposition_id"
    )

    # --- documents ------------------------------------------------------------
    op.alter_column(
        "documents", "appel_offre_id", new_column_name="appel_a_proposition_id"
    )
    op.execute(
        "ALTER TABLE documents "
        "RENAME CONSTRAINT fk_documents_appel_offre_id_appels_offre "
        "TO fk_documents_appel_a_proposition_id_appels_a_proposition"
    )
    op.execute(
        "ALTER INDEX ix_documents_appel_offre_id "
        "RENAME TO ix_documents_appel_a_proposition_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_documents_appel_a_proposition_id "
        "RENAME TO ix_documents_appel_offre_id"
    )
    op.execute(
        "ALTER TABLE documents "
        "RENAME CONSTRAINT fk_documents_appel_a_proposition_id_appels_a_proposition "
        "TO fk_documents_appel_offre_id_appels_offre"
    )
    op.alter_column(
        "documents", "appel_a_proposition_id", new_column_name="appel_offre_id"
    )

    op.execute(
        "ALTER INDEX ix_lots_appel_a_proposition_id RENAME TO ix_lots_appel_offre_id"
    )
    op.execute(
        "ALTER TABLE lots "
        "RENAME CONSTRAINT uq_lots_appel_a_proposition_id_numero "
        "TO uq_lots_appel_offre_id_numero"
    )
    op.execute(
        "ALTER TABLE lots "
        "RENAME CONSTRAINT fk_lots_appel_a_proposition_id_appels_a_proposition "
        "TO fk_lots_appel_offre_id_appels_offre"
    )
    op.alter_column("lots", "appel_a_proposition_id", new_column_name="appel_offre_id")

    op.execute(
        "ALTER INDEX ix_appels_a_proposition_statut RENAME TO ix_appels_offre_statut"
    )
    op.execute(
        "ALTER INDEX ix_appels_a_proposition_organisation_id "
        "RENAME TO ix_appels_offre_organisation_id"
    )
    op.execute(
        "ALTER TABLE appels_a_proposition "
        "RENAME CONSTRAINT fk_appels_a_proposition_organisation_id_organisations "
        "TO fk_appels_offre_organisation_id_organisations"
    )
    op.execute(
        "ALTER TABLE appels_a_proposition "
        "RENAME CONSTRAINT uq_appels_a_proposition_reference "
        "TO uq_appels_offre_reference"
    )
    op.execute(
        "ALTER TABLE appels_a_proposition "
        "RENAME CONSTRAINT pk_appels_a_proposition TO pk_appels_offre"
    )
    op.rename_table("appels_a_proposition", "appels_offre")
