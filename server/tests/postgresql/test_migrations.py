"""Alembic lifecycle tests against disposable PostgreSQL databases."""

from __future__ import annotations

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DBAPIError

from .conftest import SERVER_ROOT, DatabaseHarness


def _object_count(database_harness: DatabaseHarness, database_name: str) -> int:
    with database_harness.connect(database_name) as connection:
        row = connection.execute(
            """
            SELECT count(*)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN (
                'account', 'audit', 'catalog', 'discovery', 'identity', 'importing', 'jobs',
                'library', 'ml', 'playlist', 'social', 'sync', 'vault', 'wave'
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

    assert heads == ["0023_s2_profile_stats"]

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 106

    database_harness.downgrade(empty_database_name, "base")
    assert _current_revision(database_harness, empty_database_name) is None
    assert _object_count(database_harness, empty_database_name) == 0

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 106


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
        "0021_s1b_device_admission",
        "0022_s1c_social_runtime",
        "0023_s2_profile_stats",
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
    assert _current_revision(database_harness, empty_database_name) == ("0023_s2_profile_stats")

    with database_harness.connect(empty_database_name) as connection:
        connection.execute("DELETE FROM sync.sync_event")
        connection.commit()
    database_harness.downgrade(empty_database_name, "0015_wave_runtime")
    assert _current_revision(database_harness, empty_database_name) == "0015_wave_runtime"


def test_s1b_downgrade_refuses_durable_admission_evidence(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    """S1B's hash-only evidence must not disappear during an accidental rollback."""
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO account.device_admission (
                request_id, request_sha256, server_instance_id, identity_epoch,
                identity_thumbprint_sha256, device_public_key_spki,
                device_key_thumbprint_sha256, nickname, platform, app_version, state,
                api_major, requested_at, expires_at, review_locator_hash, poll_bearer_hash
            ) VALUES (
                uuidv7(), %s, uuidv7(), 1, %s, %s, %s, 'rollback-proof', 'ANDROID', '1',
                'PENDING', 1, now(), now() + interval '15 minutes', %s, %s
            )
            """,
            (b"r" * 32, b"i" * 32, b"p256", b"k" * 32, b"l" * 32, b"b" * 32),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing S1B downgrade"):
        database_harness.downgrade(empty_database_name, "0020_a1b_discovery_runtime")
    assert _current_revision(database_harness, empty_database_name) == ("0023_s2_profile_stats")


def test_s1b_downgrade_refuses_rate_only_evidence(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    """Even security throttle state blocks an evidence-destroying S1B rollback."""
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO account.device_admission_rate_window (
              rate_key_sha256, scope, window_started_at, expires_at, attempt_count
            ) VALUES (%s, 'SOURCE_15M', now(), now() + interval '15 minutes', 1)
            """,
            (b"q" * 32,),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing S1B downgrade"):
        database_harness.downgrade(empty_database_name, "0020_a1b_discovery_runtime")
    assert _current_revision(database_harness, empty_database_name) == ("0023_s2_profile_stats")


def test_s1b_downgrade_guard_names_every_owned_table() -> None:
    """Future migration edits cannot silently omit a receipt/challenge table from the guard."""
    migration = (
        SERVER_ROOT / "migrations" / "versions" / "0021_s1b_device_admission.py"
    ).read_text(encoding="utf-8")
    for table in (
        "account.device_admission",
        "account.device_admission_nonce",
        "account.trusted_device_key",
        "account.device_key_block",
        "account.trusted_device_reenrollment_challenge",
        "account.device_admission_exchange_receipt",
        "account.device_admission_web_operation_receipt",
        "account.device_admission_rate_window",
    ):
        assert f"EXISTS (SELECT 1 FROM {table})" in migration


def test_s1c_downgrade_refuses_rate_only_evidence(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    """Even bounded throttle evidence prevents a destructive S1C rollback."""
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO social.rate_window (
              rate_key_sha256, scope, window_started_at, expires_at, attempt_count
            ) VALUES (%s, 'CONTACT_CARD', now(), now() + interval '15 minutes', 1)
            """,
            (b"s" * 32,),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing S1C downgrade"):
        database_harness.downgrade(empty_database_name, "0021_s1b_device_admission")
    assert _current_revision(database_harness, empty_database_name) == ("0023_s2_profile_stats")


def test_s1c_downgrade_guard_names_every_owned_table() -> None:
    migration = (SERVER_ROOT / "migrations" / "versions" / "0022_s1c_social_runtime.py").read_text(
        encoding="utf-8"
    )
    for table in (
        "social.friend_request",
        "social.friendship",
        "social.user_block",
        "social.presence_settings",
        "social.presence_heartbeat",
        "social.friend_room_invitation",
        "social.operation_receipt",
        "social.rate_window",
    ):
        assert f"EXISTS (SELECT 1 FROM {table})" in migration


def test_s2_upgrade_from_s1c_keeps_existing_accounts_private_without_backfill(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0022_s1c_social_runtime")
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            "INSERT INTO account.user_account (display_name,role) VALUES ('s2-existing','USER')"
        )
        connection.commit()

    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        row = connection.execute(
            "SELECT count(*) FROM social.profile_statistics_settings"
        ).fetchone()
    assert row == (0,)


def test_s2_downgrade_refuses_profile_statistics_policy(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        user_id = connection.execute(
            "INSERT INTO account.user_account (display_name,role) "
            "VALUES ('s2-downgrade','USER') RETURNING user_id"
        ).fetchone()
        assert user_id is not None
        connection.execute(
            "INSERT INTO social.profile_statistics_settings "
            "(user_id,friends_can_view_statistics,revision) VALUES (%s,true,1)",
            (user_id[0],),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing S2 downgrade"):
        database_harness.downgrade(empty_database_name, "0022_s1c_social_runtime")
    assert _current_revision(database_harness, empty_database_name) == ("0023_s2_profile_stats")


def test_s2_downgrade_guard_names_owned_policy_table() -> None:
    migration = (
        SERVER_ROOT / "migrations" / "versions" / "0023_s2_profile_statistics_sharing.py"
    ).read_text(encoding="utf-8")
    assert "EXISTS (SELECT 1 FROM social.profile_statistics_settings)" in migration
