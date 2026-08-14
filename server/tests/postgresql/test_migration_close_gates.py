"""Durable close-gates for the P02 Alembic chain and PUBLIC privileges."""

from __future__ import annotations

from typing import Any

import pytest
from alembic import command
from psycopg import Connection

from .conftest import DatabaseHarness

REVISION_PAIRS = (
    ("0010_indexes_privileges", "0009_constraints_triggers"),
    ("0009_constraints_triggers", "0008_ml_history"),
    ("0008_ml_history", "0007_importing_identity_history"),
    ("0007_importing_identity_history", "0006_vault"),
    ("0006_vault", "0005_library_playlists"),
    ("0005_library_playlists", "0004_sync_jobs"),
    ("0004_sync_jobs", "0003_audit_identity"),
    ("0003_audit_identity", "0002_account_catalog"),
    ("0002_account_catalog", "0001_extensions_schemas"),
    ("0001_extensions_schemas", "base"),
)


def test_live_alembic_metadata_check_has_no_upgrade_operations(
    database_harness: DatabaseHarness,
    database_name: str,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run Alembic autogenerate drift detection against the migrated database."""

    monkeypatch.setenv("AUTPLAY_DATABASE_URL", database_url)
    command.check(database_harness.alembic_config(database_name))


def test_every_adjacent_revision_downgrades_and_upgrades(
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    """Execute every supported adjacent pair rather than checking graph shape only."""

    assert _current_revision(database_harness, database_name) == REVISION_PAIRS[0][0]
    for revision, predecessor in REVISION_PAIRS:
        assert _current_revision(database_harness, database_name) == revision

        database_harness.downgrade(database_name, predecessor)
        expected_predecessor = None if predecessor == "base" else predecessor
        assert _current_revision(database_harness, database_name) == expected_predecessor

        database_harness.upgrade(database_name, revision)
        assert _current_revision(database_harness, database_name) == revision

        database_harness.downgrade(database_name, predecessor)
        assert _current_revision(database_harness, database_name) == expected_predecessor


def test_public_has_no_reference_object_access(
    database_connection: Connection[Any],
) -> None:
    """Prove every reference PUBLIC revoke through PostgreSQL's effective ACLs."""

    rows = database_connection.execute(
        """
        SELECT 'app_private_schema', count(*)
        FROM pg_namespace n,
             LATERAL aclexplode(
                 COALESCE(n.nspacl, acldefault('n', n.nspowner))
             ) acl
        WHERE n.nspname = 'app_private' AND acl.grantee = 0
        UNION ALL
        SELECT 'app_private_functions', count(*)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace,
             LATERAL aclexplode(
                 COALESCE(p.proacl, acldefault('f', p.proowner))
             ) acl
        WHERE n.nspname = 'app_private' AND acl.grantee = 0
        UNION ALL
        SELECT 'module_tables', count(*)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace,
             LATERAL aclexplode(
                 COALESCE(c.relacl, acldefault('r', c.relowner))
             ) acl
        WHERE n.nspname IN (
                  'account', 'audit', 'catalog', 'identity', 'importing', 'jobs',
                  'library', 'ml', 'playlist', 'sync', 'vault'
              )
          AND c.relkind IN ('r', 'p')
          AND acl.grantee = 0
        UNION ALL
        SELECT 'sync_sequences', count(*)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace,
             LATERAL aclexplode(
                 COALESCE(c.relacl, acldefault('S', c.relowner))
             ) acl
        WHERE n.nspname = 'sync'
          AND c.relkind = 'S'
          AND acl.grantee = 0
        ORDER BY 1
        """
    ).fetchall()

    assert {str(name): int(count) for name, count in rows} == {
        "app_private_functions": 0,
        "app_private_schema": 0,
        "module_tables": 0,
        "sync_sequences": 0,
    }


def _current_revision(database_harness: DatabaseHarness, database_name: str) -> str | None:
    with database_harness.connect(database_name) as connection:
        exists = connection.execute(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ).fetchone()
        if exists is None or not bool(exists[0]):
            return None
        row = connection.execute("SELECT version_num FROM public.alembic_version").fetchone()
    return None if row is None else str(row[0])
