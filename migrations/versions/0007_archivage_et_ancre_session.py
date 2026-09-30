"""Archivage logique (équipe, bénéficiaire) et ancre documentaire de session.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-20

Deux décisions utilisateur du 20/09 (cf. docs/adr/0003) :

1. **Archivage logique** — « supprimer » une équipe ou un bénéficiaire archive
   la personne au lieu de la détruire : l'historique (affectations, CV,
   participations) reste intact. ``equipes.statut`` existait déjà en texte
   libre sans vocabulaire ; les lignes dont il est NULL sont alignées sur
   ``actif`` pour qu'elles ne disparaissent pas des listes actives.
   ``beneficiaires`` reçoit la même colonne.

2. **Documents d'une session** — ``documents.session_id`` : fiche de présence,
   checklist et rapport appartiennent à la session, pas seulement à la mission.
   Un index suit la convention des autres ancres.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Archivage logique -------------------------------------------------
    op.add_column(
        "beneficiaires",
        sa.Column(
            "statut",
            sa.String(length=50),
            nullable=False,
            server_default="actif",
        ),
    )
    # Les équipes existantes sans statut sont actives : sans ce rattrapage, un
    # filtre « actif » les ferait disparaître des listes.
    op.execute("UPDATE equipes SET statut = 'actif' WHERE statut IS NULL")

    # --- Ancre documentaire de session ------------------------------------
    op.add_column(
        "documents",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_session_id_sessions",
        "documents",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_documents_session_id", "documents", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_session_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_session_id_sessions", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "session_id")

    op.drop_column("beneficiaires", "statut")
    # ``equipes.statut`` existait avant cette révision : rien à défaire, le
    # rattrapage des NULL n'est pas réversible (il ne détruit aucune donnée).
