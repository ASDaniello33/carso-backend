"""Renommage de l'entité « appel à proposition » en « appel » (règle CARSO 26/10).

Un appel est soit un appel à proposition, soit un appel à manifestation
d'intérêt : l'entité générique ne doit plus porter le nom d'un de ses types.
Le renommage couvre la table, ses contraintes et index, et les colonnes
étrangères qui la référencent (lots, offres, documents).

**Réversible à 100 %** : le retour arrière restaure chaque nom exactement —
aucune donnée n'est modifiée, seulement des noms (table, colonnes,
contraintes, index).

Vocabulaire historique **conservé** (pas touché par cette migration) :

- les valeurs de ``type`` (``appel_a_proposition``,
  ``appel_a_manifestation_interet``, ``autre``) : ce sont des types, pas
  l'entité ;
- les ``entity_type`` d'audit déjà écrits (``appel_a_proposition``) :
  l'historique est immuable — les nouveaux événements portent ``appel``, les
  anciens restent lus (l'interface de journal accepte les deux noms) ;
- les valeurs d'``action`` d'audit (``appel_a_proposition.enregistre``, …) :
  vocabulaire figé du journal.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Renommages (table, colonnes, contraintes, index) — chaque entrée est
#: inversée telle quelle dans le ``downgrade``.
_TABLE = ("appels_a_proposition", "appels")

_COLONNES: tuple[tuple[str, str, str]] = (
    # (table, colonne actuelle, colonne nouvelle)
    ("lots", "appel_a_proposition_id", "appel_id"),
    ("offres", "appel_a_proposition_id", "appel_id"),
    ("documents", "appel_a_proposition_id", "appel_id"),
)

_CONSTRAINTES: tuple[tuple[str, str, str, str]] = (
    # (table, contrainte actuelle, contrainte nouvelle, genre) — genre pour
    # l'énoncé ALTER (PRIMARY KEY / FOREIGN KEY / UNIQUE).
    ("appels_a_proposition", "pk_appels_a_proposition", "pk_appels", "PRIMARY KEY"),
    (
        "appels_a_proposition",
        "fk_appels_a_proposition_organisation_id_organisations",
        "fk_appels_organisation_id_organisations",
        "FOREIGN KEY",
    ),
    ("appels_a_proposition", "uq_appels_a_proposition_reference", "uq_appels_reference", "UNIQUE"),
    (
        "lots",
        "fk_lots_appel_a_proposition_id_appels_a_proposition",
        "fk_lots_appel_id_appels",
        "FOREIGN KEY",
    ),
    (
        "lots",
        "uq_lots_appel_a_proposition_id_numero",
        "uq_lots_appel_id_numero",
        "UNIQUE",
    ),
    (
        "offres",
        "fk_offres_appel_a_proposition_id_appels_a_proposition",
        "fk_offres_appel_id_appels",
        "FOREIGN KEY",
    ),
    (
        "documents",
        "fk_documents_appel_a_proposition_id_appels_a_proposition",
        "fk_documents_appel_id_appels",
        "FOREIGN KEY",
    ),
)

_INDEXES: tuple[tuple[str, str]] = (
    # (index actuel, index nouveau) — la table se déduit du préfixe.
    ("ix_appels_a_proposition_statut", "ix_appels_statut"),
    ("ix_appels_a_proposition_organisation_id", "ix_appels_organisation_id"),
    ("ix_lots_appel_a_proposition_id", "ix_lots_appel_id"),
    ("ix_offres_appel_a_proposition_id", "ix_offres_appel_id"),
    ("ix_documents_appel_a_proposition_id", "ix_documents_appel_id"),
)


def _renommer_table(ancien: str, nouveau: str) -> None:
    op.execute(f'ALTER TABLE {ancien} RENAME TO {nouveau}')


def _renommer_colonne(table: str, ancien: str, nouveau: str) -> None:
    op.execute(f'ALTER TABLE {table} RENAME COLUMN {ancien} TO {nouveau}')


def _renommer_contrainte(table: str, ancien: str, nouveau: str, genre: str) -> None:
    op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {ancien} TO {nouveau}')


def _renommer_index(ancien: str, nouveau: str) -> None:
    op.execute(f'ALTER INDEX {ancien} RENAME TO {nouveau}')


def upgrade() -> None:
    """appels_a_proposition → appels, et les noms qui en dérivent."""
    for table, ancien, nouveau in reversed(_COLONNES):
        _renommer_colonne(table, ancien, nouveau)
    _renommer_table(*_TABLE)
    for ancien, nouveau in _INDEXES:
        _renommer_index(ancien, nouveau)
    for table, ancien, nouveau, genre in reversed(_CONSTRAINTES):
        # Les contraintes de l'ancienne table vivent désormais sur « appels » :
        # le renommage de la table précède, la cible est le nouveau nom.
        cible = "appels" if table == "appels_a_proposition" else table
        _renommer_contrainte(cible, ancien, nouveau, genre)


def downgrade() -> None:
    """Retour exact aux noms d'origine — aucune donnée touchée."""
    for table, ancien, nouveau, genre in _CONSTRAINTES:
        cible = "appels" if table == "appels_a_proposition" else table
        _renommer_contrainte(cible, nouveau, ancien, genre)
    for ancien, nouveau in reversed(_INDEXES):
        _renommer_index(nouveau, ancien)
    _renommer_table("appels", "appels_a_proposition")
    for table, ancien, nouveau in _COLONNES:
        _renommer_colonne(table, nouveau, ancien)
