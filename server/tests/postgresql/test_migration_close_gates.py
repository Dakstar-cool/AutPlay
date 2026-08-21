"""Durable close-gates for the P02 Alembic chain and PUBLIC privileges."""

from __future__ import annotations

from typing import Any

import pytest
from alembic import command
from psycopg import Connection
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness

REVISION_PAIRS = (
    ("0019_m6_web_admin_runtime", "0018_profile_lifecycle_cleanup"),
    ("0018_profile_lifecycle_cleanup", "0017_profile_pairing_runtime"),
    ("0017_profile_pairing_runtime", "0016_artist_id_sync_contract"),
    ("0016_artist_id_sync_contract", "0015_wave_runtime"),
    ("0015_wave_runtime", "0014_gpu_enrichment"),
    ("0014_gpu_enrichment", "0013_recommendation_runtime"),
    ("0013_recommendation_runtime", "0012_sync_runtime"),
    ("0012_sync_runtime", "0011_vault_runtime"),
    ("0011_vault_runtime", "0010_indexes_privileges"),
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


def test_p12_downgrade_refuses_to_destroy_registered_model(
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    """A downgrade is executable only while every P12-owned data surface is empty."""

    with database_harness.connect(database_name) as connection:
        connection.execute(
            """
            INSERT INTO ml.embedding_model (
                model_key, version, task, source, source_revision, artifact_filename,
                artifact_format, artifact_byte_size, artifact_manifest, manifest_sha256,
                weights_sha256, license_id, runtime, runtime_revision, inference_precision,
                input_sample_rate_hz, segment_duration_ms, preprocessing_version,
                preprocessing_manifest, preprocessing_sha256, license_review_reference,
                pooling_strategy, dimension, status
            ) VALUES (
                'downgrade-guard', '1', 'AUDIO_EMBEDDING', 'fixture://reviewed', '1',
                'model.bin', 'fixture', 1, '{}'::jsonb, %s, %s, 'fixture', 'fixture', '1',
                'fp32', 16000, 10000, '1', '{}'::jsonb, %s, 'review://fixture', 'mean', 3,
                'BENCHMARK'
            )
            """,
            (b"m" * 32, b"w" * 32, b"p" * 32),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing destructive P12 downgrade"):
        database_harness.downgrade(database_name, "0013_recommendation_runtime")
    assert _current_revision(database_harness, database_name) == "0019_m6_web_admin_runtime"


def test_p12_downgrade_refuses_after_blocking_legacy_active_model(
    database_harness: DatabaseHarness,
) -> None:
    """A legacy lifecycle change cannot be silently retained by downgrade."""

    database_name = database_harness.create_database()
    try:
        database_harness.upgrade(database_name, "0013_recommendation_runtime")
        with database_harness.connect(database_name) as connection:
            connection.execute(
                """
                INSERT INTO ml.embedding_model (
                    model_key, version, task, weights_sha256, license_id, runtime,
                    inference_precision, input_sample_rate_hz, segment_duration_ms,
                    preprocessing_version, pooling_strategy, dimension, status
                ) VALUES (
                    'legacy-active', '1', 'AUDIO_EMBEDDING', %s, 'legacy', 'legacy',
                    'fp32', 16000, 10000, '1', 'mean', 3, 'ACTIVE'
                )
                """,
                (b"w" * 32,),
            )
            connection.commit()

        database_harness.upgrade(database_name, "0014_gpu_enrichment")
        with database_harness.connect(database_name) as connection:
            status = connection.execute(
                "SELECT status FROM ml.embedding_model WHERE model_key = 'legacy-active'"
            ).fetchone()
        assert status == ("BLOCKED",)

        with pytest.raises(DBAPIError, match="refusing destructive P12 downgrade"):
            database_harness.downgrade(database_name, "0013_recommendation_runtime")
        assert _current_revision(database_harness, database_name) == "0014_gpu_enrichment"
    finally:
        database_harness.drop_database(database_name)


def _current_revision(database_harness: DatabaseHarness, database_name: str) -> str | None:
    with database_harness.connect(database_name) as connection:
        exists = connection.execute(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ).fetchone()
        if exists is None or not bool(exists[0]):
            return None
        row = connection.execute("SELECT version_num FROM public.alembic_version").fetchone()
    return None if row is None else str(row[0])
