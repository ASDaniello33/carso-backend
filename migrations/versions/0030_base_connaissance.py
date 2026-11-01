"""Base de connaissance (RAG) — bibliothèque, passages, modèle d'embedding.

Trois tables nouvelles, aucune donnée existante touchée :

- ``documents_connaissance`` : la fiche de l'ouvrage (fichier d'origine
  conservé) et son état d'indexation ;
- ``chunks_connaissance`` : les passages indexés, avec leur provenance (page,
  section) et la signature du modèle qui a produit leur vecteur ;
- ``configurations_embedding`` : le modèle d'embedding administré (versionné,
  une seule ligne active), sa clé chiffrée et sa signature mesurée.

La contrainte d'unicité sur ``checksum_sha256`` est ce qui empêche un même
ouvrage d'entrer deux fois dans la bibliothèque : la migration **échoue** si la
table contient déjà des doublons (impossible sur une table neuve, mais la règle
est explicite plutôt que silencieusement contournée).

Le downgrade supprime les trois tables — il ne touche aucune table métier.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # --- Bibliothèque -----------------------------------------------------
    op.create_table(
        "documents_connaissance",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("titre", sa.String(length=500), nullable=False),
        sa.Column("auteur", sa.String(length=255), nullable=True),
        sa.Column("editeur", sa.String(length=255), nullable=True),
        sa.Column("annee", sa.Integer(), nullable=True),
        sa.Column("categorie", sa.String(length=100), nullable=True),
        sa.Column("langue", sa.String(length=10), nullable=False, server_default="fr"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mots_cles", postgresql.JSONB(), nullable=False),
        sa.Column("nom_fichier", sa.String(length=255), nullable=False),
        sa.Column("chemin_stockage", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("taille_octets", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("nombre_pages", sa.Integer(), nullable=True),
        sa.Column("nombre_caracteres", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nombre_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "statut", sa.String(length=30), nullable=False, server_default="en_attente"
        ),
        sa.Column("erreur", sa.Text(), nullable=True),
        sa.Column("signature_embedding", sa.String(length=255), nullable=True),
        sa.Column("date_indexation", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ajoute_par_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "utilisateurs.id",
                ondelete="SET NULL",
                name="fk_documents_connaissance_ajoute_par_id_utilisateurs",
            ),
            nullable=True,
        ),
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
    )
    op.create_index(
        "ix_documents_connaissance_statut", "documents_connaissance", ["statut"]
    )
    op.create_index(
        "ix_documents_connaissance_categorie", "documents_connaissance", ["categorie"]
    )
    op.create_index(
        "ix_documents_connaissance_langue", "documents_connaissance", ["langue"]
    )
    op.create_index(
        "ix_documents_connaissance_titre", "documents_connaissance", ["titre"]
    )
    op.create_index(
        "uq_documents_connaissance_checksum",
        "documents_connaissance",
        ["checksum_sha256"],
        unique=True,
    )

    # --- Passages indexés -------------------------------------------------
    op.create_table(
        "chunks_connaissance",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "documents_connaissance.id",
                ondelete="CASCADE",
                name="fk_chunks_connaissance_document_id_documents_connaissance",
            ),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=255), nullable=True),
        sa.Column("contenu", sa.Text(), nullable=False),
        sa.Column("nombre_caracteres", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signature_embedding", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "document_id",
            "position",
            name="uq_chunks_connaissance_document_position",
        ),
    )
    op.create_index(
        "ix_chunks_connaissance_document_id", "chunks_connaissance", ["document_id"]
    )

    # --- Modèle d'embedding administré -----------------------------------
    op.create_table(
        "configurations_embedding",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=150), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=255), nullable=False),
        sa.Column("api_key_chiffree", sa.String(length=1000), nullable=True),
        sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("modifie_par", sa.String(length=64), nullable=True),
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
    )
    op.create_index(
        "ix_configurations_embedding_actif", "configurations_embedding", ["actif"]
    )
    op.create_index(
        "ix_configurations_embedding_created_at",
        "configurations_embedding",
        ["created_at"],
    )


def downgrade() -> None:
    """Retire les trois tables — aucune table métier n'est concernée."""
    op.drop_index(
        "ix_configurations_embedding_created_at", table_name="configurations_embedding"
    )
    op.drop_index(
        "ix_configurations_embedding_actif", table_name="configurations_embedding"
    )
    op.drop_table("configurations_embedding")
    op.drop_index("ix_chunks_connaissance_document_id", table_name="chunks_connaissance")
    op.drop_table("chunks_connaissance")
    op.drop_index(
        "uq_documents_connaissance_checksum", table_name="documents_connaissance"
    )
    op.drop_index("ix_documents_connaissance_titre", table_name="documents_connaissance")
    op.drop_index(
        "ix_documents_connaissance_langue", table_name="documents_connaissance"
    )
    op.drop_index(
        "ix_documents_connaissance_categorie", table_name="documents_connaissance"
    )
    op.drop_index("ix_documents_connaissance_statut", table_name="documents_connaissance")
    op.drop_table("documents_connaissance")
