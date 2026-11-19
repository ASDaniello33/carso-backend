"""Alembic environment wired to the application settings (instruction/04 §7)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.domain import Base

config = context.config

if config.config_file_name is not None:
    # ``disable_existing_loggers=False`` — la valeur par défaut de ``fileConfig``
    # (``True``) **désactive** tous les loggers déjà instanciés qui ne figurent
    # pas dans ``alembic.ini``, dont ``app.agents.mesure_modele``. En suite de
    # tests, une migration exécutée avant les tests du résumé d'historique les
    # rendait muets : leurs assertions sur les lignes journalisées échouaient
    # alors qu'ils passent isolément (``test_resume_historique``). Alembic ne
    # doit reconfigurer que **ses** loggers, jamais couper ceux de l'application.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Phase 3: the full domain metadata is registered, enabling autogenerate support.
target_metadata = Base.metadata

#: Tables créées et migrées **hors** Alembic : le checkpointer LangGraph
#: (``langgraph-checkpoint-postgres``) pose lui-même ``checkpoints``,
#: ``checkpoint_blobs``, ``checkpoint_writes`` et ``checkpoint_migrations`` au
#: premier démarrage. Sans ce filtre, ``alembic revision --autogenerate``
#: proposait de les **supprimer** — donc de perdre tous les threads d'agents
#: (constaté le 18/11 sur ``alembic check``). Ces tables ne sont ni modélisées
#: ni versionnées par l'application : elles restent la propriété du checkpointer.
TABLES_EXTERNES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_object(objet, nom, type_, reflechi, comparaison):
    """Réduit l'autogenerate au schéma que l'application possède réellement."""
    if type_ == "table" and nom in TABLES_EXTERNES:
        return False
    # Les index et contraintes de ces tables suivent la même règle.
    table = getattr(objet, "table", None)
    return not (table is not None and table.name in TABLES_EXTERNES)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connected to the database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            # Sans cela, un changement de type de colonne (``String(50)`` →
            # ``String(80)``) n'était pas détecté par ``alembic check``.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
