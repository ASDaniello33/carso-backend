"""Chat social : conversations, conférences, messages, annonces, commentaires, réactions.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-26

Fonctionnalité sociale validée (incrément 20) :

- ``conversations`` : directe (2 membres, unique par paire) ou conférence
  (groupe nommé temporaire, clôture logique ``fermee_at``) ;
- ``membres_conversation`` : participation avec rôle (animateur/membre) et
  fenêtre de présence (``parti_at`` — l'historique reste consultable) ;
- ``messages`` : texte + pièce jointe optionnelle (scope ``chat``), auteur
  humain ou agent (@mention, incrément 21), suppression auteur (soft-delete) ;
- ``annonces`` / ``commentaires`` : publication d'équipe et discussion,
  suppression auteur ;
- ``reactions`` : emoji polymorphe unique par (utilisateur, emoji, cible).

Le downgrade supprime les tables : le social est un canal de discussion, sa
perte n'affecte pas les données métier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=True),
        sa.Column("fermee_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creee_par_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["creee_par_id"], ["utilisateurs.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_conversations_type", "conversations", ["type"])
    op.create_index("ix_conversations_ferme", "conversations", ["fermee_at"])

    op.create_table(
        "membres_conversation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("utilisateur_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("parti_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utilisateur_id"], ["utilisateurs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("conversation_id", "utilisateur_id", name="uq_membres_conversation_paire"),
    )
    op.create_index("ix_membres_conversation_utilisateur", "membres_conversation", ["utilisateur_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("auteur_id", sa.Uuid(), nullable=True),
        sa.Column("auteur_type", sa.String(length=10), nullable=False),
        sa.Column("auteur_agent_id", sa.String(length=60), nullable=True),
        sa.Column("contenu", sa.Text(), nullable=False),
        sa.Column("piece_nom", sa.String(length=255), nullable=True),
        sa.Column("piece_chemin", sa.String(length=1024), nullable=True),
        sa.Column("piece_mime", sa.String(length=255), nullable=True),
        sa.Column("piece_taille", sa.Integer(), nullable=True),
        sa.Column("supprime_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["auteur_id"], ["utilisateurs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_messages_conversation_date", "messages", ["conversation_id", "created_at"])
    op.create_index("ix_messages_auteur", "messages", ["auteur_id"])
    op.create_index("ix_messages_supprime", "messages", ["supprime_at"])

    op.create_table(
        "annonces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("auteur_id", sa.Uuid(), nullable=False),
        sa.Column("titre", sa.String(length=255), nullable=False),
        sa.Column("contenu", sa.Text(), nullable=True),
        sa.Column("piece_nom", sa.String(length=255), nullable=True),
        sa.Column("piece_chemin", sa.String(length=1024), nullable=True),
        sa.Column("piece_mime", sa.String(length=255), nullable=True),
        sa.Column("piece_taille", sa.Integer(), nullable=True),
        sa.Column("supprime_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["auteur_id"], ["utilisateurs.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_annonces_date", "annonces", ["created_at"])

    op.create_table(
        "commentaires",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("annonce_id", sa.Uuid(), nullable=False),
        sa.Column("auteur_id", sa.Uuid(), nullable=False),
        sa.Column("contenu", sa.Text(), nullable=False),
        sa.Column("supprime_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["annonce_id"], ["annonces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["auteur_id"], ["utilisateurs.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_commentaires_annonce_date", "commentaires", ["annonce_id", "created_at"])

    op.create_table(
        "reactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("utilisateur_id", sa.Uuid(), nullable=False),
        sa.Column("emoji", sa.String(length=16), nullable=False),
        sa.Column("cible_type", sa.String(length=20), nullable=False),
        sa.Column("cible_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["utilisateur_id"], ["utilisateurs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "utilisateur_id", "emoji", "cible_type", "cible_id",
            name="uq_reactions_utilisateur_emoji_cible",
        ),
    )
    op.create_index("ix_reactions_cible", "reactions", ["cible_type", "cible_id"])


def downgrade() -> None:
    op.drop_index("ix_reactions_cible", table_name="reactions")
    op.drop_table("reactions")
    op.drop_index("ix_commentaires_annonce_date", table_name="commentaires")
    op.drop_table("commentaires")
    op.drop_index("ix_annonces_date", table_name="annonces")
    op.drop_table("annonces")
    op.drop_index("ix_messages_supprime", table_name="messages")
    op.drop_index("ix_messages_auteur", table_name="messages")
    op.drop_index("ix_messages_conversation_date", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_membres_conversation_utilisateur", table_name="membres_conversation")
    op.drop_table("membres_conversation")
    op.drop_index("ix_conversations_ferme", table_name="conversations")
    op.drop_index("ix_conversations_type", table_name="conversations")
    op.drop_table("conversations")
