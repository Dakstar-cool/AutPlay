"""Alembic lifecycle tests against disposable PostgreSQL databases."""

from __future__ import annotations

from alembic.script import ScriptDirectory

from .conftest import DatabaseHarness


def _object_count(database_harness: DatabaseHarness, database_name: str) -> int:
    with database_harness.connect(database_name) as connection:
        row = connection.execute(
            """
            SELECT count(*)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN (
                'account', 'audit', 'catalog', 'identity', 'importing', 'jobs',
                'library', 'ml', 'playlist', 'sync', 'vault'
            ) AND c.relkind IN ('r', 'p')
            """
        ).fetchone()
    if row is None or not isinstance(row[0], int):
        raise AssertionError("catalog count query returned no integer")
    return row[0]


def _current_revision(database_harness: DatabaseHarness, database_name: str) -> str | None:
    with database_harness.connect(database_name) as connection:
        exists_row = connection.execute(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ).fetchone()
        if exists_row is None:
            raise AssertionError("alembic version table query returned no row")
        exists = bool(exists_row[0])
        if not exists:
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else str(row[0])


def test_clean_upgrade_downgrade_and_upgrade_again(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    """Prove the mandatory empty-development-database lifecycle."""
    config = database_harness.alembic_config(empty_database_name)
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()

    assert heads == ["0010_indexes_privileges"]

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 57

    database_harness.downgrade(empty_database_name, "base")
    assert _current_revision(database_harness, empty_database_name) is None
    assert _object_count(database_harness, empty_database_name) == 0

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 57


def test_every_revision_has_one_linear_predecessor(database_harness: DatabaseHarness) -> None:
    """Make revision order and downgrade reachability explicit."""
    config = database_harness.alembic_config("autplay_p02_0000000000000000")
    scripts = ScriptDirectory.from_config(config)
    revisions = list(scripts.walk_revisions(base="base", head="heads"))

    assert [revision.revision for revision in reversed(revisions)] == [
        "0001_extensions_schemas",
        "0002_account_catalog",
        "0003_audit_identity",
        "0004_sync_jobs",
        "0005_library_playlists",
        "0006_vault",
        "0007_importing_identity_history",
        "0008_ml_history",
        "0009_constraints_triggers",
        "0010_indexes_privileges",
    ]
    assert all(not isinstance(revision.down_revision, tuple) for revision in revisions)
