"""Phase 3 — domain schema: 17 confirmed entities (instruction/04 §2, Phase 2 [C]).

Hand-written for reviewability (no autogenerate diff on the initial schema).
Tables are created in FK-dependency order. Pending business rules ([P]) are
deliberately absent: no 1-lot→1-offre uniqueness, no frozen role vocabulary,
no beneficiary columns beyond the confirmed minimum.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # --- organisations ----------------------------------------------------
    op.create_table(
        "organisations",
        _uuid_pk(),
        sa.Column("nom", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=100)),
        sa.Column("adresse", sa.Text()),
        sa.Column("email", sa.String(length=255)),
        sa.Column("telephone", sa.String(length=50)),
        sa.Column("statut", sa.String(length=50)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisations")),
    )

    # --- equipes (vivier) --------------------------------------------------
    op.create_table(
        "equipes",
        _uuid_pk(),
        sa.Column("nom", sa.String(length=100), nullable=False),
        sa.Column("prenom", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255)),
        sa.Column("telephone", sa.String(length=50)),
        sa.Column("profil", sa.Text()),
        sa.Column("statut", sa.String(length=50)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_equipes")),
    )

    # --- beneficiaires ------------------------------------------------------
    op.create_table(
        "beneficiaires",
        _uuid_pk(),
        sa.Column("nom", sa.String(length=100), nullable=False),
        sa.Column("prenom", sa.String(length=100), nullable=False),
        sa.Column("contact", sa.String(length=255)),
        sa.Column("organisation_origine", sa.String(length=255)),
        sa.Column("identifiant_externe", sa.String(length=100)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_beneficiaires")),
    )

    # --- appels_offre -------------------------------------------------------
    op.create_table(
        "appels_offre",
        _uuid_pk(),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("date_reception", sa.Date()),
        sa.Column("date_limite", sa.Date()),
        sa.Column("statut", sa.String(length=50), server_default="recu", nullable=False),
        sa.Column("donnees_extraites", postgresql.JSONB()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_appels_offre_organisation_id_organisations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appels_offre")),
        sa.UniqueConstraint("reference", name="uq_appels_offre_reference"),
    )
    op.create_index(
        op.f("ix_appels_offre_organisation_id"), "appels_offre", ["organisation_id"]
    )
    op.create_index(op.f("ix_appels_offre_statut"), "appels_offre", ["statut"])

    # --- lots ----------------------------------------------------------------
    op.create_table(
        "lots",
        _uuid_pk(),
        sa.Column("appel_offre_id", sa.Uuid(), nullable=False),
        sa.Column("numero", sa.String(length=50), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=False),
        sa.Column("zone", sa.String(length=255)),
        sa.Column("objectifs", sa.Text()),
        sa.Column("resultats_attendus", sa.Text()),
        sa.Column("mission_description", sa.Text()),
        sa.Column("partenariat", sa.Text()),
        sa.Column("date_fin", sa.Date()),
        sa.Column("donnees_source", postgresql.JSONB()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["appel_offre_id"],
            ["appels_offre.id"],
            name=op.f("fk_lots_appel_offre_id_appels_offre"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lots")),
        sa.UniqueConstraint("appel_offre_id", "numero", name="uq_lots_appel_offre_id_numero"),
    )
    op.create_index(op.f("ix_lots_appel_offre_id"), "lots", ["appel_offre_id"])

    # --- offres_formation ----------------------------------------------------
    op.create_table(
        "offres_formation",
        _uuid_pk(),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=False),
        sa.Column("date_debut_prevue", sa.Date()),
        sa.Column("date_fin_prevue", sa.Date()),
        sa.Column("statut", sa.String(length=50), server_default="brouillon", nullable=False),
        sa.Column("modele_document_id", sa.Uuid()),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_offres_formation_organisation_id_organisations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["lots.id"],
            name=op.f("fk_offres_formation_lot_id_lots"),
            ondelete="RESTRICT",
        ),
        # NOTE: la FK ``modele_document_id -> documents`` est DIFFÉRÉE après la
        # création de ``documents``. Les deux tables se référencent mutuellement
        # (cycle ``offres_formation <-> documents``) : aucun ordre de création ne
        # peut satisfaire les deux contraintes inline. Voir le bloc
        # « FK différée (cycle offres_formation <-> documents) » plus bas.
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offres_formation")),
        sa.UniqueConstraint("reference", name="uq_offres_formation_reference"),
    )
    op.create_index(op.f("ix_offres_formation_lot_id"), "offres_formation", ["lot_id"])
    op.create_index(
        op.f("ix_offres_formation_organisation_id"), "offres_formation", ["organisation_id"]
    )
    op.create_index(op.f("ix_offres_formation_statut"), "offres_formation", ["statut"])

    # --- missions --------------------------------------------------------------
    op.create_table(
        "missions",
        _uuid_pk(),
        sa.Column("offre_formation_id", sa.Uuid()),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("date_debut", sa.Date()),
        sa.Column("date_fin", sa.Date()),
        sa.Column("lieu", sa.String(length=255)),
        sa.Column("statut", sa.String(length=50), server_default="planifiee", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["offre_formation_id"],
            ["offres_formation.id"],
            name=op.f("fk_missions_offre_formation_id_offres_formation"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_missions_organisation_id_organisations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_missions")),
        sa.UniqueConstraint("reference", name="uq_missions_reference"),
    )
    op.create_index(op.f("ix_missions_dates"), "missions", ["date_debut", "date_fin"])
    op.create_index(
        op.f("ix_missions_offre_formation_id"), "missions", ["offre_formation_id"]
    )
    op.create_index(op.f("ix_missions_organisation_id"), "missions", ["organisation_id"])
    op.create_index(op.f("ix_missions_statut"), "missions", ["statut"])

    # --- sessions ---------------------------------------------------------------
    op.create_table(
        "sessions",
        _uuid_pk(),
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("date_debut", sa.Date()),
        sa.Column("date_fin", sa.Date()),
        sa.Column("lieu", sa.String(length=255)),
        sa.Column("theme", sa.String(length=255)),
        sa.Column("statut", sa.String(length=50), server_default="planifiee", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_sessions_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_dates"), "sessions", ["date_debut", "date_fin"])
    op.create_index(op.f("ix_sessions_mission_id"), "sessions", ["mission_id"])

    # --- participations -----------------------------------------------------------
    op.create_table(
        "participations",
        _uuid_pk(),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("beneficiaire_id", sa.Uuid(), nullable=False),
        sa.Column("presence", sa.String(length=50)),
        sa.Column("heure_arrivee", sa.Time()),
        sa.Column("heure_depart", sa.Time()),
        sa.Column("evaluation", sa.Text()),
        sa.Column("observations", sa.Text()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["beneficiaire_id"],
            ["beneficiaires.id"],
            name=op.f("fk_participations_beneficiaire_id_beneficiaires"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_participations_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participations")),
        sa.UniqueConstraint(
            "session_id", "beneficiaire_id", name="uq_participations_session_beneficiaire"
        ),
    )
    op.create_index(
        op.f("ix_participations_beneficiaire_id"), "participations", ["beneficiaire_id"]
    )
    op.create_index(op.f("ix_participations_session_id"), "participations", ["session_id"])

    # --- affectations_equipe ---------------------------------------------------------
    op.create_table(
        "affectations_equipe",
        _uuid_pk(),
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("equipe_id", sa.Uuid(), nullable=False),
        sa.Column("role_dans_mission", sa.String(length=100), nullable=False),
        sa.Column("date_debut", sa.Date()),
        sa.Column("date_fin", sa.Date()),
        sa.Column("statut", sa.String(length=50)),
        sa.Column(
            "source_affectation",
            sa.String(length=50),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("approved_by", sa.String(length=255)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["equipe_id"],
            ["equipes.id"],
            name=op.f("fk_affectations_equipe_equipe_id_equipes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_affectations_equipe_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affectations_equipe")),
    )
    op.create_index(
        op.f("ix_affectations_equipe_equipe_id"), "affectations_equipe", ["equipe_id"]
    )
    op.create_index(
        op.f("ix_affectations_equipe_mission_id"), "affectations_equipe", ["mission_id"]
    )

    # --- documents ------------------------------------------------------------------
    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column("nom", sa.String(length=255), nullable=False),
        sa.Column("type_document", sa.String(length=50), nullable=False),
        sa.Column("mime_type", sa.String(length=255)),
        sa.Column("taille_octets", sa.BigInteger()),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("storage_disk", sa.String(length=50)),
        sa.Column("checksum_sha256", sa.String(length=64)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("statut", sa.String(length=50), server_default="draft", nullable=False),
        sa.Column("organisation_id", sa.Uuid()),
        sa.Column("appel_offre_id", sa.Uuid()),
        sa.Column("offre_formation_id", sa.Uuid()),
        sa.Column("mission_id", sa.Uuid()),
        sa.Column("equipe_id", sa.Uuid()),
        sa.Column("doc_metadata", postgresql.JSONB()),
        sa.Column("created_by", sa.String(length=255)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["appel_offre_id"],
            ["appels_offre.id"],
            name=op.f("fk_documents_appel_offre_id_appels_offre"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["equipe_id"],
            ["equipes.id"],
            name=op.f("fk_documents_equipe_id_equipes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_documents_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offre_formation_id"],
            ["offres_formation.id"],
            name=op.f("fk_documents_offre_formation_id_offres_formation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_documents_organisation_id_organisations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(op.f("ix_documents_appel_offre_id"), "documents", ["appel_offre_id"])
    op.create_index(op.f("ix_documents_checksum"), "documents", ["checksum_sha256"])
    op.create_index(op.f("ix_documents_equipe_id"), "documents", ["equipe_id"])
    op.create_index(op.f("ix_documents_mission_id"), "documents", ["mission_id"])
    op.create_index(
        op.f("ix_documents_offre_formation_id"), "documents", ["offre_formation_id"]
    )
    op.create_index(op.f("ix_documents_organisation_id"), "documents", ["organisation_id"])
    op.create_index(op.f("ix_documents_statut"), "documents", ["statut"])
    op.create_index(op.f("ix_documents_type_document"), "documents", ["type_document"])

    # --- FK différée (cycle offres_formation <-> documents) -------------------
    # ``documents`` existe désormais : la contrainte que ``offres_formation`` ne
    # pouvait pas porter à sa création peut être ajoutée.
    op.create_foreign_key(
        op.f("fk_offres_formation_modele_document_id_documents"),
        "offres_formation",
        "documents",
        ["modele_document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- budgets / lignes_budget -----------------------------------------------------
    op.create_table(
        "budgets",
        _uuid_pk(),
        sa.Column("offre_formation_id", sa.Uuid()),
        sa.Column("mission_id", sa.Uuid()),
        sa.Column("devise", sa.String(length=10), server_default="MAD", nullable=False),
        sa.Column("statut", sa.String(length=50), server_default="brouillon", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_budgets_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offre_formation_id"],
            ["offres_formation.id"],
            name=op.f("fk_budgets_offre_formation_id_offres_formation"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
    )
    op.create_index(op.f("ix_budgets_mission_id"), "budgets", ["mission_id"])
    op.create_index(op.f("ix_budgets_offre_formation_id"), "budgets", ["offre_formation_id"])

    op.create_table(
        "lignes_budget",
        _uuid_pk(),
        sa.Column("budget_id", sa.Uuid(), nullable=False),
        sa.Column("categorie", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("unite", sa.String(length=50)),
        sa.Column("quantite", sa.Numeric(14, 2), nullable=False),
        sa.Column("cout_unitaire", sa.Numeric(14, 2), nullable=False),
        sa.Column("cout_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("ordre", sa.Integer()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["budget_id"],
            ["budgets.id"],
            name=op.f("fk_lignes_budget_budget_id_budgets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lignes_budget")),
    )
    op.create_index(op.f("ix_lignes_budget_budget_id"), "lignes_budget", ["budget_id"])

    # --- modeles_documents -------------------------------------------------------------
    op.create_table(
        "modeles_documents",
        _uuid_pk(),
        sa.Column("organisation_id", sa.Uuid()),
        sa.Column("nom", sa.String(length=255), nullable=False),
        sa.Column("type_document", sa.String(length=50), nullable=False),
        sa.Column("document_template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("statut", sa.String(length=50)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["document_template_id"],
            ["documents.id"],
            name=op.f("fk_modeles_documents_document_template_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_modeles_documents_organisation_id_organisations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_modeles_documents")),
    )
    op.create_index(
        op.f("ix_modeles_documents_organisation_id"),
        "modeles_documents",
        ["organisation_id"],
    )

    # --- approbations --------------------------------------------------------------------
    op.create_table(
        "approbations",
        _uuid_pk(),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_type", sa.String(length=100), nullable=False),
        sa.Column("proposed_by_agent", sa.String(length=100)),
        sa.Column("decision", sa.String(length=50), nullable=False),
        sa.Column("decided_by", sa.String(length=255)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approbations")),
    )
    op.create_index(op.f("ix_approbations_decision"), "approbations", ["decision"])
    op.create_index(op.f("ix_approbations_entity"), "approbations", ["entity_type", "entity_id"])

    # --- audit_events ---------------------------------------------------------------------
    op.create_table(
        "audit_events",
        _uuid_pk(),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=255)),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=255)),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("event_metadata", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])
    op.create_index(op.f("ix_audit_events_created_at"), "audit_events", ["created_at"])
    op.create_index(op.f("ix_audit_events_entity"), "audit_events", ["entity_type", "entity_id"])

    # --- agent_tasks ------------------------------------------------------------------------
    op.create_table(
        "agent_tasks",
        _uuid_pk(),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("from_agent", sa.String(length=100), nullable=False),
        sa.Column("to_agent", sa.String(length=100)),
        sa.Column("task_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("requires_approval", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tasks")),
    )
    op.create_index(op.f("ix_agent_tasks_correlation_id"), "agent_tasks", ["correlation_id"])
    op.create_index(op.f("ix_agent_tasks_from_to"), "agent_tasks", ["from_agent", "to_agent"])
    op.create_index(op.f("ix_agent_tasks_status"), "agent_tasks", ["status"])


def downgrade() -> None:
    # Reverse FK-dependency order.
    op.drop_table("agent_tasks")
    op.drop_table("audit_events")
    op.drop_table("approbations")
    op.drop_table("modeles_documents")
    op.drop_table("lignes_budget")
    op.drop_table("budgets")
    # Les deux tables du cycle se référencent mutuellement : on retire d'abord la
    # FK différée, sinon aucun des deux ``DROP TABLE`` ne peut passer.
    op.drop_constraint(
        op.f("fk_offres_formation_modele_document_id_documents"),
        "offres_formation",
        type_="foreignkey",
    )
    op.drop_table("documents")
    op.drop_table("affectations_equipe")
    op.drop_table("participations")
    op.drop_table("sessions")
    op.drop_table("missions")
    op.drop_table("offres_formation")
    op.drop_table("lots")
    op.drop_table("appels_offre")
    op.drop_table("beneficiaires")
    op.drop_table("equipes")
    op.drop_table("organisations")
