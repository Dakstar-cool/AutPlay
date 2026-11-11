"""Alembic lifecycle tests against disposable PostgreSQL databases."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from autplay.adapters.postgresql.readiness import EXPECTED_MIGRATION_HEAD
from sqlalchemy.exc import DBAPIError

from .conftest import SERVER_ROOT, DatabaseHarness

# All 30 admission columns present at 0066, including 0061's reviewed name.
# Compare actual original values/opaque hash bytes across 0071's five additions.
LEGACY_DEVICE_ADMISSION_SELECT = (
    "SELECT request_id,request_sha256,server_instance_id,identity_epoch,"
    "identity_thumbprint_sha256,device_public_key_spki,device_key_thumbprint_sha256,"
    "nickname,device_model_hint,platform,app_version,api_major,requested_at,state,"
    "expires_at,review_locator_hash,review_binding_hash,review_web_session_id,"
    "poll_bearer_hash,last_poll_at,secret_generation,recovery_count,last_recovery_at,"
    "approved_user_id,enrolled_device_id,enrolled_session_id,decision_action,created_at,"
    "decided_at,approved_device_name FROM account.device_admission ORDER BY request_id"
)


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

    assert heads == ["0074_device_deletion_v2"]
    assert heads == [EXPECTED_MIGRATION_HEAD]

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 182

    database_harness.downgrade(empty_database_name, "base")
    assert _current_revision(database_harness, empty_database_name) is None
    assert _object_count(database_harness, empty_database_name) == 0

    database_harness.upgrade(empty_database_name)
    assert _current_revision(database_harness, empty_database_name) == heads[0]
    assert _object_count(database_harness, empty_database_name) == 182


def test_upgrade_accepts_file_only_database_url(
    database_harness: DatabaseHarness,
    empty_database_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url_file = tmp_path / "database-url"
    url_file.write_text(database_harness.database_url(empty_database_name) + "\n", encoding="utf-8")
    monkeypatch.delenv("AUTPLAY_DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTPLAY_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTPLAY_DATABASE_URL_FILE", str(url_file))

    command.upgrade(database_harness.alembic_config(empty_database_name), "head")

    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


def test_upgrade_rejects_ambiguous_database_url_sources(
    database_harness: DatabaseHarness,
    empty_database_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = database_harness.database_url(empty_database_name)
    url_file = tmp_path / "database-url"
    url_file.write_text(url, encoding="utf-8")
    monkeypatch.setenv("AUTPLAY_DATABASE_URL", url)
    monkeypatch.delenv("AUTPLAY_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTPLAY_DATABASE_URL_FILE", str(url_file))

    with pytest.raises(RuntimeError, match="mutually exclusive"):
        command.upgrade(database_harness.alembic_config(empty_database_name), "head")

    assert _current_revision(database_harness, empty_database_name) is None


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
        "0024_a1b_auth_closure",
        "0025_a1c_automation_runtime",
        "0026_s1d_guest_room_access",
        "0027_public_access_invite_only",
        "0028_adaptive_recommend_shadow",
        "0029_sona_shadow_binding",
        "0030_temporal_snapshot_retention",
        "0031_music_library",
        "0032_track_metadata",
        "0033_web_passkeys",
        "0034_self_device_pairing",
        "0035_resource_admission",
        "0036_resource_io_execution",
        "0037_acquisition_authority",
        "0038_worker_resource_wait",
        "0039_internet_ingest_lineage",
        "0040_provider_staging",
        "0041_provider_scratch",
        "0042_provider_maintenance",
        "0043_orphan_object_claim",
        "0044_inventory_maintenance",
        "0045_orphan_missing",
        "0046_upload_cleanup",
        "0047_ingest_execution",
        "0048_ingest_cleanup",
        "0049_internal_io_budget",
        "0050_metadata_execution",
        "0051_account_recovery",
        "0052_account_deletion",
        "0053_privacy_purge",
        "0054_training_consent",
        "0055_training_work",
        "0056_training_checkpoint",
        "0057_training_execution",
        "0058_training_privacy_fence",
        "0059_training_publication_seal",
        "0060_local_bridge_authority",
        "0061_admission_device_name",
        "0062_cpu_worker_health",
        "0063_social_public_id",
        "0064_metadata_catalog_gate",
        "0065_internet_catalogue_context",
        "0066_portable_local_account",
        "0067_sona_native_collection",
        "0067_macos_platform_foundation",
        "0068_device_capability_revision",
        "0069_device_profile_contract_v2",
        "0070_device_portable_contract_v2",
        "0071_device_admission_v2",
        "0072_device_self_v2",
        "0073_device_recovery_v2",
        "0074_device_deletion_v2",
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
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)

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
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


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
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


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
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


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
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


def test_s2_downgrade_guard_names_owned_policy_table() -> None:
    migration = (
        SERVER_ROOT / "migrations" / "versions" / "0023_s2_profile_statistics_sharing.py"
    ).read_text(encoding="utf-8")
    assert "EXISTS (SELECT 1 FROM social.profile_statistics_settings)" in migration


def test_s1d_downgrade_refuses_rate_only_evidence(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    """Guest throttle evidence cannot be destroyed by an accidental rollback."""
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO social.guest_rate_window (
              rate_key_sha256, scope, window_started_at, expires_at, attempt_count
            ) VALUES (%s, 'SOURCE_15M', now(), now() + interval '15 minutes', 1)
            """,
            (b"g" * 32,),
        )
        connection.commit()

    with pytest.raises(DBAPIError, match="refusing S1D downgrade"):
        database_harness.downgrade(empty_database_name, "0025_a1c_automation_runtime")
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


def test_s1d_downgrade_guard_names_every_owned_table() -> None:
    migration = (
        SERVER_ROOT / "migrations" / "versions" / "0026_s1d_guest_room_access.py"
    ).read_text(encoding="utf-8")
    for table in (
        "social.guest_invitation",
        "social.guest_session",
        "social.guest_operation_receipt",
        "social.guest_preflight",
        "social.guest_timing_report",
        "social.guest_rate_window",
    ):
        assert f"EXISTS(SELECT 1 FROM {table})" in migration


def test_pa2_downgrade_refuses_rate_only_evidence(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    """PA2 throttle evidence is security-relevant and cannot be silently discarded."""
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO account.account_provisioning_rate_window
              (rate_key_sha256,scope,window_started_at,expires_at,attempt_count)
            VALUES (%s,'REDEEM_SERVER',now(),now()+interval '15 minutes',1)
            """,
            (b"p" * 32,),
        )
        connection.commit()
    with pytest.raises(DBAPIError, match="refusing PA2 downgrade"):
        database_harness.downgrade(empty_database_name, "0026_s1d_guest_room_access")
    assert _current_revision(database_harness, empty_database_name) == (EXPECTED_MIGRATION_HEAD)


def test_pa2_downgrade_guard_names_every_owned_table() -> None:
    migration = (
        SERVER_ROOT / "migrations" / "versions" / "0027_public_access_invite_only.py"
    ).read_text(encoding="utf-8")
    for table in (
        "account.account_invitation",
        "account.account_provisioning_link",
        "account.account_registration_receipt",
        "account.account_provisioning_operation_receipt",
        "account.account_provisioning_rate_window",
    ):
        assert f"EXISTS(SELECT 1 FROM {table})" in migration


def test_platform_upgrade_and_downgrade_preserve_legacy_rows(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0066_portable_local_account")
    with database_harness.connect(empty_database_name) as connection:
        owner = connection.execute(
            "INSERT INTO account.user_account(display_name) VALUES ('platform fixture') "
            "RETURNING user_id"
        ).fetchone()
        assert owner is not None
        for platform in ("ANDROID", "WEB", "OTHER"):
            connection.execute(
                "INSERT INTO account.device(user_id,device_name,platform,app_version) "
                "VALUES (%s,%s,%s,'fixture')",
                (owner[0], platform, platform),
            )
        connection.execute(
            "INSERT INTO account.device_admission ("
            "request_id,request_sha256,server_instance_id,identity_epoch,"
            "identity_thumbprint_sha256,device_public_key_spki,device_key_thumbprint_sha256,"
            "nickname,platform,app_version,state,api_major,requested_at,expires_at,"
            "review_locator_hash,poll_bearer_hash) VALUES ("
            "uuidv7(),%s,uuidv7(),1,%s,%s,%s,'legacy','ANDROID','1','PENDING',1,"
            "now(),now()+interval '15 minutes',%s,%s)",
            (b"r" * 32, b"i" * 32, b"p256", b"k" * 32, b"l" * 32, b"b" * 32),
        )
        before_devices = connection.execute(
            "SELECT * FROM account.device ORDER BY device_id"
        ).fetchall()
        before_admissions = connection.execute(LEGACY_DEVICE_ADMISSION_SELECT).fetchall()
        assert len(before_admissions) == 1
        assert before_admissions[0][1] == b"r" * 32
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        assert (
            connection.execute("SELECT * FROM account.device ORDER BY device_id").fetchall()
            == before_devices
        )
        assert connection.execute(LEGACY_DEVICE_ADMISSION_SELECT).fetchall() == before_admissions
        assert connection.execute(
            "SELECT contract_version,original_document_sha256,original_request_evidence,"
            "approved_authority_generation,approved_account_role FROM account.device_admission"
        ).fetchall() == [("v1", None, None, None, None)]
    database_harness.downgrade(empty_database_name, "0066_portable_local_account")
    with database_harness.connect(empty_database_name) as connection:
        assert (
            connection.execute("SELECT * FROM account.device ORDER BY device_id").fetchall()
            == before_devices
        )
        assert connection.execute(LEGACY_DEVICE_ADMISSION_SELECT).fetchall() == before_admissions


@pytest.mark.parametrize("evidence", ["device", "admission"])
def test_platform_downgrade_refuses_mac_before_either_constraint_changes(
    database_harness: DatabaseHarness, empty_database_name: str, evidence: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        if evidence == "device":
            owner = connection.execute(
                "INSERT INTO account.user_account(display_name) VALUES ('Mac fixture') "
                "RETURNING user_id"
            ).fetchone()
            assert owner is not None
            connection.execute(
                "INSERT INTO account.device(user_id,device_name,platform,app_version,revoked_at) "
                "VALUES (%s,'historical Mac','MACOS','fixture',now())",
                (owner[0],),
            )
        else:
            connection.execute(
                "INSERT INTO account.device_admission ("
                "request_id,request_sha256,server_instance_id,identity_epoch,"
                "identity_thumbprint_sha256,device_public_key_spki,device_key_thumbprint_sha256,"
                "nickname,platform,app_version,state,api_major,requested_at,expires_at,"
                "review_locator_hash,poll_bearer_hash) VALUES ("
                "uuidv7(),%s,uuidv7(),1,%s,%s,%s,'Mac','MACOS','1','PENDING',1,"
                "now(),now()+interval '15 minutes',%s,%s)",
                (b"r" * 32, b"i" * 32, b"p256", b"k" * 32, b"l" * 32, b"b" * 32),
            )
        before_devices = connection.execute("SELECT * FROM account.device").fetchall()
        before_admissions = connection.execute("SELECT * FROM account.device_admission").fetchall()
        connection.commit()
    with pytest.raises(DBAPIError, match="refusing platform downgrade"):
        database_harness.downgrade(empty_database_name, "0066_portable_local_account")
    assert _current_revision(database_harness, empty_database_name) == EXPECTED_MIGRATION_HEAD
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute("SELECT * FROM account.device").fetchall() == before_devices
        assert (
            connection.execute("SELECT * FROM account.device_admission").fetchall()
            == before_admissions
        )
        definitions = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname IN ('ck_device_platform','device_admission_platform_check')"
        ).fetchall()
        assert len(definitions) == 2
        assert all("MACOS" in definition[0] for definition in definitions)


def test_capability_evidence_upgrade_preserves_v1_and_blocks_lossy_downgrade(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name, "0067_macos_platform_foundation")
    with database_harness.connect(empty_database_name) as connection:
        owner = connection.execute(
            "INSERT INTO account.user_account(display_name) VALUES('D1 migration "
            "fixture') RETURNING user_id"
        ).fetchone()
        assert owner is not None
        device = connection.execute(
            "INSERT INTO account.device(user_id,device_name,platform,app_version) "
            "VALUES(%s,'legacy','ANDROID','fixture') RETURNING device_id",
            (owner[0],),
        ).fetchone()
        assert device is not None
        instance = connection.execute(
            "INSERT INTO account.server_instance(identity_public_key_spki,id"
            "entity_thumbprint_sha256,"
            "label_hint,api_origin,stream_origin) VALUES(%s,%s,'fixture',"
            "'https://api.test.invalid','https://stream.test.invalid') "
            "RETURNING server_instance_id",
            (b"s" * 91, b"i" * 32),
        ).fetchone()
        assert instance is not None
        connection.execute(
            "INSERT INTO account.user_session(user_id,device_id,refresh_toke"
            "n_hash,issued_at,expires_at) "
            "VALUES(%s,%s,%s,now(),now()+interval '1 day')",
            (owner[0], device[0], b"r" * 32),
        )
        legacy_queries = {
            **{
                table: "SELECT * FROM account." + table
                for table in ("device", "user_session", "server_instance")
            },
            "device_admission": LEGACY_DEVICE_ADMISSION_SELECT,
        }
        legacy = {
            table: connection.execute(query).fetchall() for table, query in legacy_queries.items()
        }
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        for table, rows in legacy.items():
            assert connection.execute(legacy_queries[table]).fetchall() == rows
        connection.execute(
            "INSERT INTO account.device_capability_revision(server_instance_"
            "id,identity_epoch,user_id,"
            "device_id,capability_revision,effective_content_sha256,identity_thumbprint_sha256,"
            "device_key_thumbprint_sha256,created_at,updated_at) "
            "VALUES(%s,1,%s,%s,1,%s,%s,%s,now(),now())",
            (instance[0], owner[0], device[0], b"c" * 32, b"i" * 32, b"k" * 32),
        )
        evidence = connection.execute("SELECT * FROM account.device_capability_revision").fetchall()
        connection.commit()
    with pytest.raises(
        DBAPIError, match="refusing to discard device capability high-water evidence"
    ):
        database_harness.downgrade(empty_database_name, "0066_portable_local_account")
    assert _current_revision(database_harness, empty_database_name) == EXPECTED_MIGRATION_HEAD
    with database_harness.connect(empty_database_name) as connection:
        assert (
            connection.execute("SELECT * FROM account.device_capability_revision").fetchall()
            == evidence
        )
        for table, rows in legacy.items():
            assert connection.execute(legacy_queries[table]).fetchall() == rows
