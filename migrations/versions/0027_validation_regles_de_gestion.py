"""Validation globale des règles de gestion (ADR 0012 et 0013).

Une seule migration pour six changements qui forment un même corps de règles :

1. **Cycle de vie** — les statuts mission/session passent au vocabulaire validé
   avec CARSO : ``en_planification``, ``planifie``, ``en_cours``, ``cloture``,
   ``annule``. Les valeurs historiques sont remappées sans perte d'étape
   (``planifiee`` sans session → ``en_planification``, ``planifiee`` avec
   session → ``planifie``, ``en_preparation`` → ``planifie``).
2. **1 lot → 1 mission** — ``missions.lot_id`` (FK ``RESTRICT``) et unicité
   ``uq_missions_lot_id``. PostgreSQL autorise plusieurs ``NULL`` : une
   prestation directe ne consomme aucun lot.
3. **1 mission → 1 session** — unicité ``uq_sessions_mission_id``. Si des
   doublons existent, la migration **échoue avec un diagnostic** : elle ne
   supprime ni n'annule aucune session en silence.
4. **Fonction de travail libre** — ``equipes.fonction``.
5. **Propriété documentaire** — ``documents.proprietaire_id`` et
   ``documents.visibilite``. Reprise : ``proprietaire_id`` est dérivé de
   ``created_by`` quand la valeur est un UUID de compte existant ; sinon il
   reste ``NULL`` (document visible des seuls administrateurs).
6. **Partage par référence** — ``messages.document_id`` et
   ``annonces_pieces.document_id`` (FK ``SET NULL``).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import context, op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _offline() -> bool:
    """Vrai quand Alembic ne fait qu'émettre du SQL (``--sql``)."""
    return context.is_offline_mode()


def _doublons_de_sessions() -> list[tuple[object, int]]:
    """Missions portant plusieurs sessions — la contrainte ne peut pas être posée."""
    bind = op.get_bind()
    return list(
        bind.execute(
            sa.text(
                "SELECT mission_id, COUNT(*) AS nb FROM sessions "
                "GROUP BY mission_id HAVING COUNT(*) > 1 "
                "ORDER BY nb DESC, mission_id"
            )
        ).fetchall()
    )


def upgrade() -> None:
    # --- 1. Colonnes nouvelles -------------------------------------------------
    op.add_column("missions", sa.Column("lot_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_missions_lot_id",
        "missions",
        "lots",
        ["lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_missions_lot_id", "missions", ["lot_id"])

    op.add_column("equipes", sa.Column("fonction", sa.String(length=150), nullable=True))

    op.add_column("documents", sa.Column("proprietaire_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_documents_proprietaire_id",
        "documents",
        "utilisateurs",
        ["proprietaire_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_proprietaire_id", "documents", ["proprietaire_id"]
    )
    op.add_column(
        "documents",
        sa.Column(
            "visibilite",
            sa.String(length=20),
            nullable=False,
            server_default="prive",
        ),
    )

    op.add_column("messages", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_messages_document_id",
        "messages",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "annonces_pieces", sa.Column("document_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_annonces_pieces_document_id",
        "annonces_pieces",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- 2. Remappage des statuts ---------------------------------------------
    op.execute(
        sa.text(
            """
            UPDATE missions
               SET statut = CASE
                   WHEN statut = 'en_preparation' THEN 'planifie'
                   WHEN statut = 'cloturee' THEN 'cloture'
                   WHEN statut = 'planifiee' THEN CASE
                       WHEN EXISTS (
                           SELECT 1 FROM sessions s WHERE s.mission_id = missions.id
                       ) THEN 'planifie'
                       ELSE 'en_planification'
                   END
                   ELSE statut
               END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE sessions
               SET statut = CASE
                   WHEN statut IN ('planifiee', 'confirmee') THEN 'planifie'
                   WHEN statut = 'realisee' THEN 'cloture'
                   WHEN statut = 'annulee' THEN 'annule'
                   ELSE statut
               END
            """
        )
    )
    op.alter_column(
        "missions",
        "statut",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default="en_planification",
    )
    op.alter_column(
        "sessions",
        "statut",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default="planifie",
    )

    # --- 3. Reprise de propriété ----------------------------------------------
    if not _offline():
        bind = op.get_bind()
        lignes = bind.execute(
            sa.text(
                "SELECT id, created_by FROM documents "
                "WHERE proprietaire_id IS NULL AND created_by IS NOT NULL"
            )
        ).fetchall()
        for document_id, created_by in lignes:
            try:
                candidat = UUID(str(created_by))
            except (ValueError, AttributeError, TypeError):
                continue
            existe = bind.execute(
                sa.text("SELECT 1 FROM utilisateurs WHERE id = :identifiant"),
                {"identifiant": candidat},
            ).first()
            if existe is None:
                continue
            bind.execute(
                sa.text(
                    "UPDATE documents SET proprietaire_id = :proprietaire "
                    "WHERE id = :document"
                ),
                {"proprietaire": candidat, "document": document_id},
            )

    # --- 4. Unicité de la session ---------------------------------------------
    # Le contrôle est fait **avant** la contrainte : en cas de doublon, la
    # migration s'arrête avec la liste des missions, sans rien détruire.
    if not _offline():
        doublons = _doublons_de_sessions()
        if doublons:
            detail = ", ".join(
                f"{mission_id} ({nb} sessions)" for mission_id, nb in doublons
            )
            raise RuntimeError(
                "Migration 0027 interrompue : plusieurs sessions existent pour "
                "une même mission, la contrainte « 1 mission → 1 session » ne "
                "peut pas être posée. Aucune donnée n'a été supprimée. "
                f"Missions concernées : {detail}. "
                "Conservez la session de référence de chaque mission, archivez "
                "ou annulez les autres, puis relancez la migration."
            )
    op.create_unique_constraint("uq_sessions_mission_id", "sessions", ["mission_id"])


def downgrade() -> None:
    """Retour au schéma précédent.

    Le remappage inverse est **best-effort** : l'ancien vocabulaire n'avait pas
    d'état « annulé » pour une mission, et distinguait « planifiée » de
    « en préparation ». ``annule`` revient donc à ``planifiee`` et ``planifie``
    à ``en_preparation`` — l'information d'annulation n'existe plus dans
    l'ancien modèle. Aucune ligne n'est supprimée.
    """
    op.drop_constraint("uq_sessions_mission_id", "sessions", type_="unique")

    op.execute(
        sa.text(
            """
            UPDATE missions
               SET statut = CASE
                   WHEN statut = 'en_planification' THEN 'planifiee'
                   WHEN statut = 'planifie' THEN 'en_preparation'
                   WHEN statut = 'cloture' THEN 'cloturee'
                   WHEN statut = 'annule' THEN 'planifiee'
                   ELSE statut
               END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE sessions
               SET statut = CASE
                   WHEN statut IN ('en_planification', 'planifie') THEN 'planifiee'
                   WHEN statut = 'en_cours' THEN 'confirmee'
                   WHEN statut = 'cloture' THEN 'realisee'
                   WHEN statut = 'annule' THEN 'annulee'
                   ELSE statut
               END
            """
        )
    )
    op.alter_column(
        "missions",
        "statut",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default="planifiee",
    )
    op.alter_column(
        "sessions",
        "statut",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default="planifiee",
    )

    op.drop_constraint(
        "fk_annonces_pieces_document_id", "annonces_pieces", type_="foreignkey"
    )
    op.drop_column("annonces_pieces", "document_id")

    op.drop_constraint("fk_messages_document_id", "messages", type_="foreignkey")
    op.drop_column("messages", "document_id")

    op.drop_column("documents", "visibilite")
    op.drop_index("ix_documents_proprietaire_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_proprietaire_id", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "proprietaire_id")

    op.drop_column("equipes", "fonction")

    op.drop_constraint("uq_missions_lot_id", "missions", type_="unique")
    op.drop_constraint("fk_missions_lot_id", "missions", type_="foreignkey")
    op.drop_column("missions", "lot_id")
