"""Parité schéma déclaratif ↔ migrations Alembic.

Le bug réel corrigé (28/09/2026) : ``AnnonceLecture`` héritait de
``TimestampMixin`` (created_at / updated_at) mais la migration 0015 n'avait
créé ni l'un ni l'autre — la suite de tests passait (SQLite recrée le schéma
depuis ``Base.metadata``) et **la production PostgreSQL échouait** à chaque
insertion (``UndefinedColumn: annonces_lectures.created_at``) : le badge
« annonces non lues » ne se vidait jamais.

Le garde-fou ci-dessous compare les **colonnes** de chaque table du domaine
entre le schéma déclaratif ORM et la base configurée après
``alembic upgrade head`` (même approche que ``test_live_upgrade_creates_all_tables``
de ``test_migrations.py`` : la migration est idempotente sur une base à jour).
Toute divergence (colonne ORM absente de la base migrée, ou l'inverse) fait
échouer la suite **avant** la mise en prod.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from tests.conftest import requires_db

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Tables exclues : techniques (Alembic) ou gérées hors du domaine déclaratif.
_TABLES_HORS_DOMAINE = {"alembic_version", "spatial_ref_sys"}


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return cfg


@requires_db
def test_migrations_alignees_sur_le_schema_declaratif() -> None:
    """Aucune colonne ORM ne peut manquer en base après ``alembic upgrade head``."""
    from sqlalchemy import create_engine, inspect

    from app.core.config import get_settings
    from app.domain.base import Base

    command.upgrade(_alembic_config(), "head")
    engine = create_engine(get_settings().database_url)
    try:
        inspecteur = inspect(engine)
        tables_base = set(inspecteur.get_table_names()) - _TABLES_HORS_DOMAINE
        tables_orm = set(Base.metadata.tables)

        manquantes_en_base = tables_orm - tables_base
        assert (
            not manquantes_en_base
        ), f"tables ORM absentes des migrations: {manquantes_en_base}"

        divergences: list[str] = []
        for table in sorted(tables_orm & tables_base):
            colonnes_orm = {
                colonne.name for colonne in Base.metadata.tables[table].columns
            }
            colonnes_base = {colonne["name"] for colonne in inspecteur.get_columns(table)}
            for nom in sorted(colonnes_orm - colonnes_base):
                divergences.append(f"{table}.{nom} : dans l'ORM, absente des migrations")
            for nom in sorted(colonnes_base - colonnes_orm):
                divergences.append(f"{table}.{nom} : en base, absente de l'ORM")
        assert (
            divergences == []
        ), "dérive modèle ↔ migrations:\n" + "\n".join(divergences)
    finally:
        engine.dispose()
