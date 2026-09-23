"""Migration 0061 preserves legacy rows and fences key/license generations."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import Connection
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness


def _legacy_model(
    connection: Connection[Any],
    *,
    key: str,
    manifest: bytes = b"m" * 32,
    artifact_manifest: str = "{}",
) -> None:
    connection.execute(
        """
        INSERT INTO ml.embedding_model (
          model_key,version,task,source,source_revision,artifact_filename,
          artifact_format,artifact_byte_size,artifact_manifest,manifest_sha256,
          weights_sha256,license_id,runtime,runtime_revision,inference_precision,
          input_sample_rate_hz,segment_duration_ms,preprocessing_version,
          preprocessing_manifest,preprocessing_sha256,pooling_strategy,dimension,
          license_review_reference
        ) VALUES (
          %s,'1','AUDIO_EMBEDDING','fixture://model','r1','model.onnx',
          'ONNX',100,%s::jsonb,%s,%s,'test-license','onnxruntime','1',
          'fp32',48000,1000,'1','{}'::jsonb,%s,'mean',3,'reviewed-legacy'
        )
        """,
        (key, artifact_manifest, manifest, b"a" * 32, b"p" * 32),
    )


def test_upgrade_preserves_legacy_model_without_approving_license(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0060_local_bridge_authority")
    with database_harness.connect(empty_database_name) as connection:
        user_id = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('ml-reviewer','OWNER') RETURNING user_id"
        ).fetchone()
        assert user_id is not None
        connection.execute(
            "INSERT INTO account.device(user_id,device_name,platform,app_version,public_key) "
            "VALUES(%s,'before-upgrade','ANDROID','1',%s)",
            (user_id[0], b"old-key"),
        )
        _legacy_model(connection, key="legacy-a")
        connection.commit()

    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        model = connection.execute(
            "SELECT weights_sha256,artifact_sha256,status FROM ml.embedding_model "
            "WHERE model_key='legacy-a'"
        ).fetchone()
        assert model == (b"a" * 32, b"a" * 32, "BENCHMARK")
        decision = connection.execute(
            "SELECT state,decision_sequence,effective_generation FROM ml.artifact_license_current"
        ).fetchone()
        assert decision == ("LEGACY_UNREVIEWED", 1, 1)
        assert connection.execute(
            "SELECT device_key_generation FROM account.device WHERE device_name='before-upgrade'"
        ).fetchone() == (1,)

        connection.execute(
            """
            INSERT INTO ml.artifact_license_decision(
              artifact_sha256,decision_sequence,effective_generation,superseded_sequence,
              state,license_identifier,license_text_sha256,use_restrictions,
              redistribution_decision,modification_decision,attribution_payload,
              reviewer_user_id,reviewed_at,review_reference,
              max_offline_revocation_lag_ms,derived_output_disposition)
            VALUES(%s,2,2,1,'APPROVED','test-license',%s,'{}'::jsonb,
              'SEPARATE_INSTALL_ONLY','DENIED','{}'::jsonb,%s,now(),
              'manual-review',604800000,'RETAIN_NON_DISTRIBUTABLE')
            """,
            (b"a" * 32, b"l" * 32, user_id[0]),
        )
        assert connection.execute(
            "SELECT state,decision_sequence,effective_generation FROM ml.artifact_license_current"
        ).fetchone() == ("APPROVED", 2, 2)
        connection.commit()

    with database_harness.connect(empty_database_name) as connection:
        with (
            pytest.raises(psycopg.Error, match="artifact_license_current_is_derived"),
            connection.transaction(),
        ):
            connection.execute("UPDATE ml.artifact_license_current SET state='REVOKED'")
        with (
            pytest.raises(psycopg.Error, match="artifact_license_sequence_invalid"),
            connection.transaction(),
        ):
            connection.execute(
                "INSERT INTO ml.artifact_license_decision "
                "(artifact_sha256,decision_sequence,effective_generation,state) "
                "VALUES(%s,4,4,'LEGACY_UNREVIEWED')",
                (b"a" * 32,),
            )
        with (
            pytest.raises(psycopg.Error, match="artifact_license_review_shape"),
            connection.transaction(),
        ):
            connection.execute(
                "INSERT INTO ml.artifact_license_decision "
                "(artifact_sha256,decision_sequence,effective_generation,"
                "superseded_sequence,state) "
                "VALUES(%s,3,3,2,'LEGACY_UNREVIEWED')",
                (b"a" * 32,),
            )

    with pytest.raises(DBAPIError, match="refusing ml artifact authority downgrade"):
        database_harness.downgrade(empty_database_name, "0060_local_bridge_authority")


def test_key_generation_advances_on_binding_rotation_and_revocation(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        user_id = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('ml-device','USER') RETURNING user_id"
        ).fetchone()
        assert user_id is not None
        device_id = connection.execute(
            "INSERT INTO account.device(user_id,device_name,platform,app_version) "
            "VALUES(%s,'ml-device','ANDROID','1') "
            "RETURNING device_id,device_key_generation",
            (user_id[0],),
        ).fetchone()
        assert device_id is not None and device_id[1] == 0
        connection.commit()

    with database_harness.connect(empty_database_name) as connection:
        with (
            pytest.raises(psycopg.Error, match="device_key_generation_invalid"),
            connection.transaction(),
        ):
            connection.execute(
                "UPDATE account.device SET public_key=%s WHERE device_id=%s",
                (b"key-1", device_id[0]),
            )
        connection.execute(
            "UPDATE account.device SET public_key=%s,device_key_generation=1 WHERE device_id=%s",
            (b"key-1", device_id[0]),
        )
        with (
            pytest.raises(psycopg.Error, match="device_key_generation_invalid"),
            connection.transaction(),
        ):
            connection.execute(
                "UPDATE account.device SET public_key=%s WHERE device_id=%s",
                (b"key-2", device_id[0]),
            )
        connection.execute(
            "UPDATE account.device SET public_key=%s,device_key_generation=2 WHERE device_id=%s",
            (b"key-2", device_id[0]),
        )
        connection.execute(
            "UPDATE account.device SET revoked_at=now() WHERE device_id=%s",
            (device_id[0],),
        )
        assert connection.execute(
            "SELECT device_key_generation FROM account.device WHERE device_id=%s",
            (device_id[0],),
        ).fetchone() == (3,)
        with (
            pytest.raises(psycopg.Error, match="device_key_revocation_immutable"),
            connection.transaction(),
        ):
            connection.execute(
                "UPDATE account.device SET revoked_at=NULL WHERE device_id=%s",
                (device_id[0],),
            )
        connection.commit()


def test_conflicting_legacy_content_metadata_is_reported_without_promotion(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0060_local_bridge_authority")
    with database_harness.connect(empty_database_name) as connection:
        _legacy_model(connection, key="first", manifest=b"m" * 32)
        _legacy_model(connection, key="second", manifest=b"n" * 32)
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        rows = connection.execute(
            "SELECT model_key,artifact_sha256 FROM ml.embedding_model ORDER BY model_key"
        ).fetchall()
        assert rows == [("first", b"a" * 32), ("second", None)]
        assert connection.execute("SELECT reason FROM ml.artifact_migration_issue").fetchone() == (
            "CONTENT_METADATA_CONFLICT",
        )
        assert connection.execute("SELECT state FROM ml.artifact_license_current").fetchone() == (
            "LEGACY_UNREVIEWED",
        )


def test_oversized_legacy_manifest_is_reported_without_aborting_upgrade(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0060_local_bridge_authority")
    with database_harness.connect(empty_database_name) as connection:
        _legacy_model(
            connection,
            key="oversized",
            artifact_manifest='{"payload":"' + "x" * 66000 + '"}',
        )
        connection.commit()

    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute(
            "SELECT artifact_sha256 FROM ml.embedding_model WHERE model_key='oversized'"
        ).fetchone() == (None,)
        assert connection.execute("SELECT count(*) FROM ml.artifact").fetchone() == (0,)
        assert connection.execute("SELECT reason FROM ml.artifact_migration_issue").fetchone() == (
            "MANIFEST_OVERSIZE",
        )


def test_competing_license_decisions_are_serialized_by_current_row(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0060_local_bridge_authority")
    with database_harness.connect(empty_database_name) as connection:
        _legacy_model(connection, key="concurrent-license")
        reviewer = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('concurrent-reviewer','OWNER') RETURNING user_id"
        ).fetchone()
        assert reviewer is not None
        reviewer_id = reviewer[0]
        connection.commit()
    database_harness.upgrade(empty_database_name)

    append_review = """
        INSERT INTO ml.artifact_license_decision(
          artifact_sha256,decision_sequence,effective_generation,superseded_sequence,
          state,license_identifier,license_text_sha256,redistribution_decision,
          modification_decision,reviewer_user_id,reviewed_at,review_reference,
          max_offline_revocation_lag_ms)
        VALUES(%s,2,2,1,'APPROVED','reviewed-license',%s,
          'SEPARATE_INSTALL_ONLY','DENIED',%s,now(),'concurrent-review',604800000)
    """
    values = (b"a" * 32, b"l" * 32, reviewer_id)
    with (
        database_harness.connect(empty_database_name) as winner,
        database_harness.connect(empty_database_name) as contender,
    ):
        winner.execute(append_review, values)
        contender.execute("SET lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            contender.execute(append_review, values)
        contender.rollback()

        winner.commit()
        with pytest.raises(psycopg.Error, match="artifact_license_sequence_invalid"):
            contender.execute(append_review, values)
        contender.rollback()
        assert contender.execute(
            "SELECT decision_sequence,effective_generation,state "
            "FROM ml.artifact_license_current WHERE artifact_sha256=%s",
            (b"a" * 32,),
        ).fetchone() == (2, 2, "APPROVED")


def test_new_ml_authority_tables_grant_no_public_access(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    names = {
        "artifact",
        "artifact_migration_issue",
        "artifact_license_decision",
        "artifact_license_current",
        "face_artifact_release",
        "sona_artifact_release",
        "control_step_up_credential",
        "control_step_up_challenge",
        "control_step_up_receipt",
    }
    with database_harness.connect(empty_database_name) as connection:
        rows = connection.execute(
            """
            SELECT c.relname,
              EXISTS(
                SELECT 1
                FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
                WHERE acl.grantee=0
              ) AS public_grant
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='ml' AND c.relname=ANY(%s::text[])
            """,
            (sorted(names),),
        ).fetchall()
    assert {row[0] for row in rows} == names
    assert all(not row[1] for row in rows)


def test_downgrade_refuses_new_unlinked_artifact(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO ml.artifact(
              artifact_sha256,artifact_byte_size,artifact_format,source,
              source_revision,manifest_sha256,artifact_manifest)
            VALUES(%s,100,'ONNX','fixture://new','r1',%s,'{}'::jsonb)
            """,
            (b"z" * 32, b"m" * 32),
        )
        connection.commit()
    with pytest.raises(DBAPIError, match="refusing ml artifact authority downgrade"):
        database_harness.downgrade(empty_database_name, "0060_local_bridge_authority")
