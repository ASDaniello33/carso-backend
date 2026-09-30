"""Offre générique, type d'appel, Lieu, mission ↔ offres (N-N), supports de formation.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-21

Règles métier confirmées directement avec CARSO :

1. **``OffreDeFormation`` → ``Offre``** — le concept devient générique. La table
   ``offres_formation`` est renommée ``offres`` ; une offre répond toujours à
   ``Appel + Lot`` et porte un ``type`` explicite (technique, financière, autre).
2. **Type d'appel** — ``appels_a_proposition.type`` : appel à proposition ou
   appel à manifestation d'intérêt.
3. **Lieu distinct** — table ``lieux`` + ``missions.lieu_id``. Un lot n'est pas
   forcément un lieu : le lieu d'exécution appartient à la mission.
4. **Mission ↔ Offre = N-N** — un appel à proposition se répond par une offre
   *technique* **et** une offre *financière* d'un même lot ; la mission les
   référence via ``mission_offres``.
5. **Supports de formation** — ``supports_formation`` relie Formateur ↔ Mission ↔
   ``Document`` (aucun second système documentaire).

Reprise de données assumée et **signalée** (voir
``docs/validation/MIGRATION-OFFRES-TYPE.md``) : les offres existantes sont
classées ``offre_technique`` (elles portaient méthodologie, équipe, planning et
livrables) — la liste est vérifiable par requête, aucune donnée n'est devinée en
silence.

Aucun fichier n'est déplacé sur disque : les documents déjà rangés sous
``offres_formation/{id}/`` restent lisibles via le scope historique ; les
nouveaux dépôts écrivent sous ``offres/{id}/`` (ADR 0004).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. offres_formation → offres --------------------------------------
    op.rename_table("offres_formation", "offres")
    op.execute("ALTER TABLE offres RENAME CONSTRAINT pk_offres_formation TO pk_offres")
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT uq_offres_formation_reference "
        "TO uq_offres_reference"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT "
        "fk_offres_formation_organisation_id_organisations "
        "TO fk_offres_organisation_id_organisations"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT fk_offres_formation_lot_id_lots "
        "TO fk_offres_lot_id_lots"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT "
        "fk_offres_formation_modele_document_id_documents "
        "TO fk_offres_modele_document_id_documents"
    )
    op.execute(
        "ALTER INDEX ix_offres_formation_organisation_id "
        "RENAME TO ix_offres_organisation_id"
    )
    op.execute("ALTER INDEX ix_offres_formation_statut RENAME TO ix_offres_statut")

    # --- 2. Type d'offre (renseigné, puis obligatoire) ----------------------
    # AVANT la contrainte unique : ``(lot_id, type)`` exige la colonne.
    op.add_column("offres", sa.Column("type", sa.String(length=50), nullable=True))
    # Reclassement de l'existant : les offres historiques portaient la partie
    # technique (méthodologie, planning, équipe, livrables). Liste vérifiable.
    op.execute("UPDATE offres SET type = 'offre_technique' WHERE type IS NULL")
    op.alter_column("offres", "type", nullable=False)

    # Unicité historique « 1 lot → 1 offre » remplacée par « 1 lot → 1 offre de
    # chaque type » : un même lot porte désormais une offre technique ET une
    # offre financière (règle confirmée CARSO).
    op.execute("ALTER TABLE offres DROP CONSTRAINT uq_offres_formation_lot_id")
    op.create_unique_constraint("uq_offres_lot_id_type", "offres", ["lot_id", "type"])
    op.create_index("ix_offres_lot_id", "offres", ["lot_id"])
    op.create_index("ix_offres_type", "offres", ["type"])

    # --- 3. Lien explicite offre → appel ------------------------------------
    op.add_column(
        "offres",
        sa.Column("appel_a_proposition_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE offres o SET appel_a_proposition_id = l.appel_a_proposition_id "
        "FROM lots l WHERE o.lot_id = l.id AND o.appel_a_proposition_id IS NULL"
    )
    op.alter_column("offres", "appel_a_proposition_id", nullable=False)
    op.create_foreign_key(
        "fk_offres_appel_a_proposition_id_appels_a_proposition",
        "offres",
        "appels_a_proposition",
        ["appel_a_proposition_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_offres_appel_a_proposition_id", "offres", ["appel_a_proposition_id"]
    )

    # --- 4. Type d'appel -----------------------------------------------------
    op.add_column(
        "appels_a_proposition",
        sa.Column(
            "type",
            sa.String(length=50),
            nullable=False,
            server_default="appel_a_proposition",
        ),
    )

    # --- 5. Lieu (donnée distincte) -----------------------------------------
    op.create_table(
        "lieux",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("nom", sa.String(length=255), nullable=False),
        sa.Column("adresse", sa.Text()),
        sa.Column("ville", sa.String(length=255)),
        sa.Column("pays", sa.String(length=100)),
        sa.Column("zone", sa.String(length=255)),
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
        sa.PrimaryKeyConstraint("id", name="pk_lieux"),
    )
    op.create_index("ix_lieux_nom", "lieux", ["nom"])
    op.create_index("ix_lieux_ville", "lieux", ["ville"])

    op.add_column(
        "missions",
        sa.Column("lieu_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Reprise : chaque texte de lieu distinct devient une ligne ``lieux``, puis
    # la mission pointe dessus. Un texte vide n'invente aucun lieu.
    op.execute(
        "INSERT INTO lieux (id, nom, created_at, updated_at) "
        "SELECT gen_random_uuid(), s.nom, now(), now() "
        "FROM (SELECT DISTINCT btrim(lieu) AS nom FROM missions "
        "      WHERE lieu IS NOT NULL AND btrim(lieu) <> '') s"
    )
    op.execute(
        "UPDATE missions m SET lieu_id = l.id FROM lieux l "
        "WHERE m.lieu IS NOT NULL AND btrim(m.lieu) = l.nom"
    )
    op.create_foreign_key(
        "fk_missions_lieu_id_lieux", "missions", "lieux", ["lieu_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_missions_lieu_id", "missions", ["lieu_id"])

    # --- 6. Mission ↔ Offre (N-N) -------------------------------------------
    op.create_table(
        "mission_offres",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offre_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_mission_offres_mission_id_missions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offre_id"],
            ["offres.id"],
            name="fk_mission_offres_offre_id_offres",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mission_offres"),
        sa.UniqueConstraint(
            "mission_id", "offre_id", name="uq_mission_offres_mission_id_offre_id"
        ),
    )
    op.create_index("ix_mission_offres_mission_id", "mission_offres", ["mission_id"])
    op.create_index("ix_mission_offres_offre_id", "mission_offres", ["offre_id"])
    # Reprise de l'ancien lien 1-1 ``missions.offre_formation_id``.
    op.execute(
        "INSERT INTO mission_offres (id, mission_id, offre_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), id, offre_formation_id, now(), now() "
        "FROM missions WHERE offre_formation_id IS NOT NULL"
    )

    # --- 7. Retrait des colonnes remplacées ---------------------------------
    # Note : ``ix_missions_offre_formation_id`` a déjà été supprimé en 0006
    # (remplacé par l'unicité) — ne pas le rechercher ici.
    op.drop_constraint(
        "uq_missions_offre_formation_id", "missions", type_="unique"
    )
    op.drop_constraint(
        "fk_missions_offre_formation_id_offres_formation", "missions", type_="foreignkey"
    )
    op.drop_column("missions", "offre_formation_id")
    # Le texte libre du lieu est remplacé par la référence ``lieux`` (les
    # valeurs ont été reprises ci-dessus).
    op.drop_column("missions", "lieu")

    # --- 8. Supports de formation -------------------------------------------
    op.create_table(
        "supports_formation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("equipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("libelle", sa.String(length=255)),
        sa.Column("statut", sa.String(length=50)),
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
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_supports_formation_mission_id_missions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["equipe_id"],
            ["equipes.id"],
            name="fk_supports_formation_equipe_id_equipes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_supports_formation_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_supports_formation"),
        sa.UniqueConstraint(
            "mission_id",
            "equipe_id",
            "document_id",
            name="uq_supports_formation_mission_equipe_document",
        ),
    )
    op.create_index(
        "ix_supports_formation_mission_id", "supports_formation", ["mission_id"]
    )
    op.create_index(
        "ix_supports_formation_equipe_id", "supports_formation", ["equipe_id"]
    )
    op.create_index(
        "ix_supports_formation_document_id", "supports_formation", ["document_id"]
    )

    # --- 9. Colonnes ``offre_formation_id`` → ``offre_id`` ------------------
    op.alter_column("documents", "offre_formation_id", new_column_name="offre_id")
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT "
        "fk_documents_offre_formation_id_offres_formation "
        "TO fk_documents_offre_id_offres"
    )
    op.execute("ALTER INDEX ix_documents_offre_formation_id RENAME TO ix_documents_offre_id")

    op.alter_column("budgets", "offre_formation_id", new_column_name="offre_id")
    op.execute(
        "ALTER TABLE budgets RENAME CONSTRAINT "
        "fk_budgets_offre_formation_id_offres_formation TO fk_budgets_offre_id_offres"
    )
    op.execute("ALTER INDEX ix_budgets_offre_formation_id RENAME TO ix_budgets_offre_id")

    # --- 10. Vocabulaires persistés -----------------------------------------
    # Les documents historiques restent lisibles ; ils basculent sur le type
    # générique ``offre``.
    op.execute(
        "UPDATE documents SET type_document = 'offre' WHERE type_document = 'offre_formation'"
    )
    # Note : ``configurations_runtime_agent`` porte la configuration provider/
    # modèle (globale), pas d'identifiant d'agent : rien à migrer là.


def downgrade() -> None:
    # --- Vocabulaires persistés ---------------------------------------------
    op.execute(
        "UPDATE documents SET type_document = 'offre_formation' WHERE type_document = 'offre'"
    )

    # --- Colonnes ``offre_id`` → ``offre_formation_id`` ---------------------
    op.execute("ALTER INDEX ix_budgets_offre_id RENAME TO ix_budgets_offre_formation_id")
    op.execute(
        "ALTER TABLE budgets RENAME CONSTRAINT "
        "fk_budgets_offre_id_offres TO fk_budgets_offre_formation_id_offres_formation"
    )
    op.alter_column("budgets", "offre_id", new_column_name="offre_formation_id")

    op.execute("ALTER INDEX ix_documents_offre_id RENAME TO ix_documents_offre_formation_id")
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT "
        "fk_documents_offre_id_offres TO fk_documents_offre_formation_id_offres_formation"
    )
    op.alter_column("documents", "offre_id", new_column_name="offre_formation_id")

    # --- Supports de formation ----------------------------------------------
    op.drop_index("ix_supports_formation_document_id", table_name="supports_formation")
    op.drop_index("ix_supports_formation_equipe_id", table_name="supports_formation")
    op.drop_index("ix_supports_formation_mission_id", table_name="supports_formation")
    op.drop_table("supports_formation")

    # --- Mission : retour du lien 1-1 et du texte de lieu -------------------
    op.add_column(
        "missions", sa.Column("lieu", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "missions",
        sa.Column("offre_formation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE missions m SET lieu = l.nom FROM lieux l WHERE m.lieu_id = l.id"
    )
    # Une mission ne peut repointer que sur une offre : la première du couple
    # (technique/financière) est retenue — le downgrade perd volontairement la
    # seconde, la relation N-N n'ayant pas d'équivalent en 1-1.
    op.execute(
        "UPDATE missions m SET offre_formation_id = mo.offre_id "
        "FROM (SELECT DISTINCT ON (mission_id) mission_id, offre_id "
        "      FROM mission_offres ORDER BY mission_id, created_at) mo "
        "WHERE m.id = mo.mission_id"
    )
    op.create_foreign_key(
        "fk_missions_offre_formation_id_offres_formation",
        "missions",
        "offres",
        ["offre_formation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_missions_offre_formation_id", "missions", ["offre_formation_id"]
    )

    op.drop_index("ix_missions_lieu_id", table_name="missions")
    op.drop_constraint("fk_missions_lieu_id_lieux", "missions", type_="foreignkey")
    op.drop_column("missions", "lieu_id")
    op.drop_index("ix_lieux_ville", table_name="lieux")
    op.drop_index("ix_lieux_nom", table_name="lieux")
    op.drop_table("lieux")

    op.drop_table("mission_offres")

    # --- Type d'appel --------------------------------------------------------
    op.drop_column("appels_a_proposition", "type")

    # --- Offre : retrait du lien appel et du type ---------------------------
    op.drop_index("ix_offres_appel_a_proposition_id", table_name="offres")
    op.drop_constraint(
        "fk_offres_appel_a_proposition_id_appels_a_proposition",
        "offres",
        type_="foreignkey",
    )
    op.drop_column("offres", "appel_a_proposition_id")
    op.drop_index("ix_offres_type", table_name="offres")
    op.drop_index("ix_offres_lot_id", table_name="offres")
    op.drop_constraint("uq_offres_lot_id_type", "offres", type_="unique")
    op.create_unique_constraint("uq_offres_formation_lot_id", "offres", ["lot_id"])
    op.drop_column("offres", "type")

    # --- offres → offres_formation ------------------------------------------
    op.execute("ALTER INDEX ix_offres_statut RENAME TO ix_offres_formation_statut")
    op.execute(
        "ALTER INDEX ix_offres_organisation_id "
        "RENAME TO ix_offres_formation_organisation_id"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT "
        "fk_offres_modele_document_id_documents "
        "TO fk_offres_formation_modele_document_id_documents"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT fk_offres_lot_id_lots "
        "TO fk_offres_formation_lot_id_lots"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT "
        "fk_offres_organisation_id_organisations "
        "TO fk_offres_formation_organisation_id_organisations"
    )
    op.execute(
        "ALTER TABLE offres RENAME CONSTRAINT uq_offres_reference "
        "TO uq_offres_formation_reference"
    )
    op.execute("ALTER TABLE offres RENAME CONSTRAINT pk_offres TO pk_offres_formation")
    op.rename_table("offres", "offres_formation")
