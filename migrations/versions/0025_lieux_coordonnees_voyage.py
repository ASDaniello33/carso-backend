"""Géographie : coordonnées des lieux, lieux des organisations et des lots.

Trois changements qui forment une seule chaîne — « savoir **où** se passe une
mission » :

1. ``lieux`` gagne ``latitude``/``longitude`` (WGS84) : le lieu devient
   cartographiable (Photon pour le trouver, ajustement manuel si nécessaire).
   Les deux colonnes vont ensemble : aucune position partielle n'est acceptée
   (règle du service).
2. ``organisations`` et ``lots`` gagnent ``lieu_id`` (FK ``SET NULL``) : ce
   sont **elles** qui désignent un lieu — le lieu reste un référentiel neutre
   et partagé (plusieurs lots d'un même appel peuvent se dérouler au même
   endroit). Le lieu d'exécution d'une mission reste la donnée du terrain.
3. ``parametres_voyage`` (une ligne) porte les réglages de voyage de
   l'organisation — origine par défaut, consommation, prix du litre, devise —
   et ``itineraires_cache`` conserve les résultats OpenRouteService jusqu'à
   expiration (les quotas gratuits ne doivent pas être dépensés deux fois pour
   le même trajet).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Identifiant fixe de la ligne unique (miroir de ``app.domain.geo``).
PARAMETRES_VOYAGE_ID = "00000000-0000-4000-8000-00000000a11e"


def upgrade() -> None:
    op.add_column("lieux", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("lieux", sa.Column("longitude", sa.Float(), nullable=True))

    op.add_column(
        "organisations",
        sa.Column("lieu_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "fk_organisations_lieu_id_lieux",
        "organisations",
        ["lieu_id"],
    )
    op.create_foreign_key(
        "fk_organisations_lieu_id_lieux",
        "organisations",
        "lieux",
        ["lieu_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("lots", sa.Column("lieu_id", sa.Uuid(), nullable=True))
    op.create_index("fk_lots_lieu_id_lieux", "lots", ["lieu_id"])
    op.create_foreign_key(
        "fk_lots_lieu_id_lieux",
        "lots",
        "lieux",
        ["lieu_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "parametres_voyage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("origine_libelle", sa.String(length=255), nullable=True),
        sa.Column("origine_latitude", sa.Float(), nullable=True),
        sa.Column("origine_longitude", sa.Float(), nullable=True),
        sa.Column("consommation_l_100km", sa.Numeric(5, 2), nullable=False),
        sa.Column("prix_litre", sa.Numeric(12, 2), nullable=False),
        sa.Column("devise", sa.String(length=10), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    # Ligne unique créée par la migration : une lecture des Paramètres ne doit
    # jamais avoir à écrire.
    op.execute(
        "INSERT INTO parametres_voyage (id, consommation_l_100km, prix_litre, devise) "
        f"VALUES ('{PARAMETRES_VOYAGE_ID}', 8.00, 0.00, 'MGA')"
    )

    op.create_table(
        "itineraires_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cle", sa.String(length=64), nullable=False),
        sa.Column("profil", sa.String(length=30), nullable=False),
        sa.Column("origine_latitude", sa.Float(), nullable=False),
        sa.Column("origine_longitude", sa.Float(), nullable=False),
        sa.Column("destination_latitude", sa.Float(), nullable=False),
        sa.Column("destination_longitude", sa.Float(), nullable=False),
        sa.Column("aller_retour", sa.Boolean(), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=False),
        sa.Column("duree_s", sa.Integer(), nullable=False),
        sa.Column("geometrie", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("expire_le", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_itineraires_cache_cle", "itineraires_cache", ["cle"], unique=True
    )
    op.create_index(
        "ix_itineraires_cache_expire_le", "itineraires_cache", ["expire_le"]
    )


def downgrade() -> None:
    op.drop_table("itineraires_cache")
    op.drop_table("parametres_voyage")
    op.drop_constraint("fk_lots_lieu_id_lieux", "lots", type_="foreignkey")
    op.drop_index("fk_lots_lieu_id_lieux", table_name="lots")
    op.drop_column("lots", "lieu_id")
    op.drop_constraint("fk_organisations_lieu_id_lieux", "organisations", type_="foreignkey")
    op.drop_index("fk_organisations_lieu_id_lieux", table_name="organisations")
    op.drop_column("organisations", "lieu_id")
    op.drop_column("lieux", "longitude")
    op.drop_column("lieux", "latitude")
