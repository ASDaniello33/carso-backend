"""Alembic migration chain tests.

Offline mode (sql=True) validates the full 0001→0002 SQL without a database.
The live upgrade test is gated by RUN_DB_TESTS=1 like the rest of the suite.
"""

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import requires_db

BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: Références FK dans le DDL émis (inline `... REFERENCES t (id)` et `ALTER TABLE`).
REFERENCED = re.compile(r"REFERENCES (\w+)")
ALTER_FK = re.compile(
    r"ALTER TABLE (\w+) ADD CONSTRAINT .*?FOREIGN KEY .*?REFERENCES (\w+)", re.S
)

DOMAIN_TABLES = (
    "organisations",
    "appels_a_proposition",
    "lots",
    "offres",
    "missions",
    "lieux",
    "mission_offres",
    "supports_formation",
    "equipes",
    "affectations_equipe",
    "documents",
    "beneficiaires",
    "sessions",
    "participations",
    "presences",
    "budgets",
    "lignes_budget",
    "modeles_documents",
    "approbations",
    "audit_events",
    "agent_tasks",
    "utilisateurs",
)

#: Tables créées sous un ancien nom puis renommées par une migration ultérieure :
#: l'offre est créée ``offres_formation`` (0002) et renommée ``offres`` (0008).
TABLES_RENOMMEES = {"appels_a_proposition", "offres"}


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return cfg


def test_offline_upgrade_emits_full_domain_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(_alembic_config(), "head", sql=True)
    sql_out = capsys.readouterr().out

    for table in DOMAIN_TABLES:
        if table == "appels_a_proposition":
            # Table créée sous l'ancien nom (0002), renommée en 0005.
            assert "CREATE TABLE appels_offre" in sql_out, "table historique manquante"
            assert "RENAME TO appels_a_proposition" in sql_out, "rename 0005 manquant"
            continue
        if table == "offres":
            # Table créée sous l'ancien nom (0002), renommée en 0008.
            assert "CREATE TABLE offres_formation" in sql_out, "table historique manquante"
            assert "RENAME TO offres" in sql_out, "rename 0008 manquant"
            continue
        assert f"CREATE TABLE {table}" in sql_out, f"table manquante: {table}"
    assert "gen_random_uuid()" in sql_out


def test_offline_downgrade_is_reversible(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), "head", sql=True)
    capsys.readouterr()
    command.downgrade(_alembic_config(), "head:0001", sql=True)
    sql_out = capsys.readouterr().out

    assert "DROP TABLE organisations" in sql_out
    assert "DROP TABLE agent_tasks" in sql_out


@requires_db
def test_live_upgrade_creates_all_tables() -> None:
    """Applies head to the configured carso_dev database, then checks via inspection."""
    from sqlalchemy import create_engine, inspect

    from app.core.config import get_settings

    command.upgrade(_alembic_config(), "head")
    engine = create_engine(get_settings().database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    for table in DOMAIN_TABLES:
        assert table in tables, f"table absente après migration: {table}"


def test_offline_ddl_ne_reference_pas_une_table_non_encore_creee(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-régression : interdit toute référence FK à une table pas encore créée.

    Bug réel corrigé (2026-09-15) : ``offres_formation`` portait une FK **inline**
    vers ``documents`` alors que ``documents`` était créée plus loin. Les deux
    tables formant un cycle, PostgreSQL annulait **toute** la migration
    (``UndefinedTable: la relation "documents" n'existe pas``) — 0 table créée.

    Ce test analyse le DDL **émis** (mode offline, aucune base requise) et refuse :
    - une FK inline vers une table créée plus tard ;
    - un ``ALTER TABLE ... FOREIGN KEY`` ajouté avant la table référencée.
    """
    command.upgrade(_alembic_config(), "head", sql=True)
    sql_out = capsys.readouterr().out

    violations = _references_invalides(sql_out)

    assert violations == [], f"FK non résolues dans le DDL émis: {violations}"
    # Garde-fou : l'analyse a bien parcouru les tables du domaine.
    # ``appels_a_proposition`` est créée sous l'ancien nom (0002) puis
    # renommée (0005) : on accepte les deux formes dans le DDL émis.
    creees = set(re.findall(r"CREATE TABLE (\w+)", sql_out))
    renommees = set(re.findall(r"RENAME TO (\w+)", sql_out))
    attendues = set(DOMAIN_TABLES) - TABLES_RENOMMEES
    assert attendues <= creees
    assert "appels_offre" in creees
    assert "offres_formation" in creees
    assert "appels_a_proposition" in renommees
    assert "offres" in renommees


def _references_invalides(sql_text: str) -> list[str]:
    """Références FK vers une table pas encore créée, dans l'ordre du DDL émis.

    Les commentaires SQL (``-- Running upgrade ...``, émis par Alembic en mode
    offline) sont retirés avant l'analyse : sans cela, la première instruction
    qui suit un commentaire (``CREATE TABLE organisations``) n'est pas reconnue,
    ce qui produit des faux positifs.
    """
    sans_commentaires = re.sub(r"--[^\n]*", "", sql_text)

    violations: list[str] = []
    creees: set[str] = set()

    for instruction in sans_commentaires.split(";"):
        statement = instruction.strip()
        if not statement:
            continue

        entete = re.search(r"CREATE TABLE (\w+)", statement)
        if entete is not None:
            nom = entete.group(1)
            violations.extend(
                f"{nom} -> {cible} (inline)"
                for cible in REFERENCED.findall(statement)
                if cible not in creees
            )
            creees.add(nom)
            continue

        rename = re.search(r"ALTER TABLE (\w+) RENAME TO (\w+)", statement)
        if rename is not None:
            ancien, nouveau = rename.group(1), rename.group(2)
            if ancien in creees:
                creees.discard(ancien)
                creees.add(nouveau)
            continue

        ajout = ALTER_FK.search(statement)
        if ajout is not None and ajout.group(2) not in creees:
            violations.append(f"{ajout.group(1)} -> {ajout.group(2)} (ALTER TABLE)")

    return violations


def test_le_detecteur_repere_le_cycle_fk_corrige() -> None:
    """Preuve que le test de non-régression ci-dessus est falsifiable.

    Le DDL ci-dessous reproduit le défaut réel de la migration 0002 avant
    correction : ``offres_formation`` référence ``documents`` avant sa création.
    """
    ddl_fautif = """
    CREATE TABLE organisations (id UUID PRIMARY KEY);
    CREATE TABLE offres_formation (
        id UUID PRIMARY KEY,
        modele_document_id UUID,
        CONSTRAINT fk_offres_formation_modele_document_id_documents
            FOREIGN KEY(modele_document_id) REFERENCES documents (id) ON DELETE SET NULL
    );
    CREATE TABLE documents (
        id UUID PRIMARY KEY,
        offre_formation_id UUID,
        CONSTRAINT fk_documents_offre_formation_id_offres_formation
            FOREIGN KEY(offre_formation_id) REFERENCES offres_formation (id) ON DELETE CASCADE
    );
    """

    assert _references_invalides(ddl_fautif) == [
        "offres_formation -> documents (inline)"
    ]

    # Le même schéma avec la FK différée (après `documents`) est accepté.
    ddl_corrige = ddl_fautif.replace(
        "CONSTRAINT fk_offres_formation_modele_document_id_documents\n"
        "            FOREIGN KEY(modele_document_id) REFERENCES documents (id) ON DELETE SET NULL",
        "",
    ) + """
    ALTER TABLE offres_formation ADD CONSTRAINT fk_offres_formation_modele_document_id_documents
        FOREIGN KEY(modele_document_id) REFERENCES documents (id) ON DELETE SET NULL;
    """

    assert _references_invalides(ddl_corrige) == []
