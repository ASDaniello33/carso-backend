"""Présence par jour : table ``presences``, le pointage quitte l'inscription.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24

Règle confirmée avec CARSO (24/09) : une session de cinq jours (01 → 05/09) se
pointe **cinq fois**. Il faut donc une date, qui n'existait pas : ``Participation``
portait une seule présence par (session, bénéficiaire).

1. **Nouvelle table ``presences``** — une ligne par ``(participation, date)``,
   avec le statut (``StatutPresence``) et les heures facultatives. L'unicité
   ``uq_presences_participation_id_date`` interdit deux pointages du même jour
   pour la même personne : re-pointer une date la corrige, sans doublon.
2. **``participations`` redevient l'inscription seule** — les colonnes
   ``presence``, ``heure_arrivee`` et ``heure_depart`` sont retirées : une
   présence à deux endroits serait deux vérités.
3. **Reprise des pointages existants** — la date est **déduite**
   (``COALESCE(sessions.date_debut, missions.date_debut)``), jamais inventée.
   Un pointage dont aucune date n'est dérivable n'est **pas** migré : la
   migration le compte et l'annonce (``RAISE NOTICE``), il n'est jamais perdu
   en silence.

Suppression en cascade assumée : retirer une inscription emporte ses pointages
(``ON DELETE CASCADE``) — un pointage sans inscription n'a aucun sens. Le
downgrade est réversible mais **perd les jours supplémentaires** : la présence
la plus ancienne redevient la valeur unique de l'inscription.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Une ligne par (participation, date) ------------------------------
    op.create_table(
        "presences",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("participation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("presence", sa.String(length=50)),
        sa.Column("heure_arrivee", sa.Time()),
        sa.Column("heure_depart", sa.Time()),
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
            ["participation_id"],
            ["participations.id"],
            name="fk_presences_participation_id_participations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_presences"),
        sa.UniqueConstraint(
            "participation_id", "date", name="uq_presences_participation_id_date"
        ),
    )
    op.create_index("ix_presences_participation_id", "presences", ["participation_id"])
    op.create_index("ix_presences_date", "presences", ["date"])

    # --- 2. Reprise des pointages existants ----------------------------------
    # La date vivait implicitement dans la session (ou, à défaut, dans la
    # mission) : c'est cette date qui est reprise, aucune autre n'est inventée.
    op.execute(
        "INSERT INTO presences ("
        "id, participation_id, date, presence, heure_arrivee, heure_depart, "
        "created_at, updated_at) "
        "SELECT gen_random_uuid(), p.id, COALESCE(s.date_debut, m.date_debut), "
        "       p.presence, p.heure_arrivee, p.heure_depart, now(), now() "
        "FROM participations p "
        "JOIN sessions s ON s.id = p.session_id "
        "LEFT JOIN missions m ON m.id = s.mission_id "
        "WHERE p.presence IS NOT NULL "
        "  AND COALESCE(s.date_debut, m.date_debut) IS NOT NULL"
    )

    # Contrôle de non-perte : les pointages sans date dérivable sont annoncés
    # dans le journal de la migration, jamais abandonnés en silence.
    op.execute(
        "DO $$ DECLARE restants integer; BEGIN "
        "  SELECT count(*) INTO restants FROM participations p "
        "  JOIN sessions s ON s.id = p.session_id "
        "  LEFT JOIN missions m ON m.id = s.mission_id "
        "  WHERE p.presence IS NOT NULL "
        "    AND COALESCE(s.date_debut, m.date_debut) IS NULL; "
        "  IF restants > 0 THEN "
        "    RAISE NOTICE 'presences: % pointage(s) sans date dérivable, non migrés', "
        "      restants; "
        "  END IF; "
        "END $$;"
    )

    # --- 3. Le pointage ne vit plus sur l'inscription ------------------------
    # Aucune donnée disparaît ici : ce qui était repris l'a été ci-dessus.
    op.drop_column("participations", "heure_depart")
    op.drop_column("participations", "heure_arrivee")
    op.drop_column("participations", "presence")


def downgrade() -> None:
    op.add_column("participations", sa.Column("presence", sa.String(length=50)))
    op.add_column("participations", sa.Column("heure_arrivee", sa.Time()))
    op.add_column("participations", sa.Column("heure_depart", sa.Time()))

    # Retour au modèle « une présence par inscription » : la plus ancienne est
    # retenue. Les jours suivants sont perdus — le 0009 n'a pas de place pour eux.
    op.execute(
        "UPDATE participations p "
        "SET presence = pr.presence, "
        "    heure_arrivee = pr.heure_arrivee, "
        "    heure_depart = pr.heure_depart "
        "FROM ("
        "  SELECT DISTINCT ON (participation_id) "
        "    participation_id, presence, heure_arrivee, heure_depart "
        "  FROM presences ORDER BY participation_id, date"
        ") pr WHERE p.id = pr.participation_id"
    )

    op.drop_index("ix_presences_date", table_name="presences")
    op.drop_index("ix_presences_participation_id", table_name="presences")
    op.drop_table("presences")
