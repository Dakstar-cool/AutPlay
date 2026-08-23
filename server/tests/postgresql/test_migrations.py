"""Alembic lifecycle tests against disposable PostgreSQL databases."""

from __future__ import annotations

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness


def _object_count(database_harness: DatabaseHarness, database_name: str) -> int:
    with database_harness.connect(database_name) as connection:
        row = connection.execute(
            """
            SELECT count(*)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN (
                'account', 'audit', 'catalog', 'discovery', 'identity', 'importing', 'jobs',
                'library', 'ml', 'playlist', 'sync', 'vault', 'wave'
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

    assert heads == ["0020_a1b_discovery_runtime"]

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 89

    database_harness.downgrade(empty_database_name, "base")
    assert _current_revision(database_harness, empty_database_name) is None
    assert _object_count(database_harness, empty_database_name) == 0

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 89


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
        "0011_vault_runtime",
        "0012_sync_runtime",
        "0013_recommendation_runtime",
        "0014_gpu_enrichment",
        "0015_wave_runtime",
        "0016_artist_id_sync_contract",
        "0017_profile_pairing_runtime",
        "0018_profile_lifecycle_cleanup",
        "0019_m6_web_admin_runtime",
        "0020_a1b_discovery_runtime",
    ]
    assert all(not isinstance(revision.down_revision, tuple) for revision in revisions)


def test_artist_sync_downgrade_refuses_durable_catalog_events(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        row = connection.execute(
            "INSERT INTO account.user_account (display_name, role) "
            "VALUES ('artist-downgrade-owner', 'OWNER') RETURNING user_id"
        ).fetchone()
        if row is None:
            raise AssertionError("expected owner insert to return a user ID")
        user_id = row[0]
        connection.execute(
            "INSERT INTO sync.sync_event "
            "(user_id, event_type, schema_version, aggregate_type, aggregate_id, payload) "
            "VALUES (%s, 'CATALOG_ARTIST_UPSERTED', 1, 'ARTIST', uuidv7(), '{}'::jsonb)",
            (user_id,),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing Artist sync downgrade with catalog events"):
        database_harness.downgrade(empty_database_name, "0015_wave_runtime")
    # Alembic executes the attempted multi-revision downgrade atomically; the
    # M5B contract remains present when the predecessor refuses its rollback.
    assert _current_revision(database_harness, empty_database_name) == (
        "0020_a1b_discovery_runtime"
    )

    with database_harness.connect(empty_database_name) as connection:
        connection.execute("DELETE FROM sync.sync_event")
        connection.commit()
    database_harness.downgrade(empty_database_name, "0015_wave_runtime")
    assert _current_revision(database_harness, empty_database_name) == "0015_wave_runtime"
