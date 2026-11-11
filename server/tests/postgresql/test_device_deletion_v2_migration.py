"""UNEXECUTED additive 0074 constraints, history, purge and rollback fixtures.

Manually derived inventory: +1 table, +17 columns there, +2 intent columns,
+1 explicit index, +3 functions, +5 triggers. 0066 reference stays unchanged.
Metadata fingerprint stays stale/guarded until real executable qualification.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.application.account_recovery import cleanup_expired_recovery_operations
from autplay.application.privacy_deletion import PrivacyDeletionService
from psycopg import Connection
from psycopg.errors import CheckViolation, RaiseException
from psycopg.types.json import Jsonb
from typed_assertions import present, require_instance

from .conftest import DatabaseHarness
from .schema_contract import snapshot_schema
from .test_device_deletion_v2 import DeletionHarness, sign_original
from .test_device_deletion_v2 import deletion_v2 as deletion_v2
from .test_device_profile_v2 import profile as profile
from .test_device_recovery_v2 import RecoveryHarness
from .test_device_recovery_v2 import recovery_v2 as recovery_v2
from .test_profile_pairing_m5b import pairing_service as pairing_service


def insert_proof(
    h: DeletionHarness,
    connection: Connection[Any],
    *,
    mutate: str | None = None,
    timestamp: str | None = None,
    decision: str = "SEALED",
) -> str:
    doc = json.loads(h.wire("request"))
    doc["operation_id"] = str(uuid4())
    if timestamp is not None:
        doc["requested_at"] = timestamp
    row_platform, row_version = doc["platform"], "v2"
    original_instance = doc["expected_server_instance_id"]
    if mutate == "extra_secret":
        doc["recovery_code"] = "synthetic-disallowed-secret-field"
    elif mutate == "wrong_thumb":
        doc["device_key_thumbprint_sha256"] = "00" * 32
    elif mutate == "null_generation":
        doc["expected_authority_generation"] = None
    elif mutate == "bool_generation":
        doc["expected_code_generation"] = True
    elif mutate == "wrong_identity":
        doc["expected_server_instance_id"] = str(uuid4())
    elif mutate == "platform_column":
        row_platform = "MACOS" if row_platform == "ANDROID" else "ANDROID"
    elif mutate == "future_version":
        row_version = "v3"
    elif mutate == "oversized":
        doc["device_name"] = "x" * 8193
    elif mutate not in {
        None,
        "missing_original",
        "array_original",
        "null_decision",
        "wrong_algorithm",
        "wrong_domain",
    }:
        raise AssertionError("unknown mutation")
    raw = sign_original(doc, h.pair.key, "request")  # Independent signature AFTER mutation.
    doc = json.loads(raw)
    stored = None if mutate == "missing_original" else [doc] if mutate == "array_original" else doc
    connection.execute(
        "INSERT INTO account.account_deletion_proof(operation_id,user_id"
        ",server_instance_id,identity_epoch,"
        "actor_device_id,actor_public_key_spki,actor_key_thumbprint_sha256,code_verifier_sha256,contract_version,"
        "platform,request_sha256,original_document_sha256,original_reque"
        "st_document,signature_algorithm,proof_domain,decision,decided_a"
        "t) "
        "SELECT %s,%s,%s,%s,%s,d.public_key,d.public_key_thumbprint_sha2"
        "56,c.verifier_sha256,%s,%s,%s,%s,%s,%s,%s,%s,%s "
        "FROM account.device d JOIN account.account_recovery_credential "
        "c USING(user_id) WHERE d.device_id=%s",
        (
            doc["operation_id"],
            doc["account_id"],
            original_instance,
            doc["expected_identity_epoch"],
            h.pair.actor.device_id,
            row_version,
            row_platform,
            bytes.fromhex(doc["request_sha256"]),
            hashlib.sha256(rfc8785.dumps(stored)).digest(),
            Jsonb(stored),
            "ES256-DER" if mutate == "wrong_algorithm" else "ES256-P1363",
            "autplay:device-compatibility:deletion:cancel:v2\n"
            if mutate == "wrong_domain"
            else "autplay:device-compatibility:deletion:request:v2\n",
            None if mutate == "null_decision" else decision,
            h.now(),
            h.pair.actor.device_id,
        ),
    )
    return require_instance(doc["operation_id"], str)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_secret",
        "wrong_thumb",
        "null_generation",
        "bool_generation",
        "wrong_identity",
        "platform_column",
        "future_version",
        "oversized",
        "missing_original",
        "array_original",
        "wrong_algorithm",
        "wrong_domain",
    ],
)
def test_exact_sql_check_after_positive_control(
    deletion_v2: DeletionHarness, database_connection: Connection[Any], mutation: str
) -> None:
    connection, h = database_connection, deletion_v2
    positive = insert_proof(h, connection)
    connection.commit()  # Includes real deferred actor/SEALED checks.
    assert connection.execute(
        "SELECT count(*) FROM account.account_deletion_proof WHERE operation_id=%s", (positive,)
    ).fetchone() == (1,)
    connection.commit()
    with pytest.raises(CheckViolation) as caught:
        insert_proof(h, connection, mutate=mutation)
    assert caught.value.diag.constraint_name == "deletion_proof_v2_check"
    connection.rollback()


def test_null_decision_and_immutable_history_cannot_be_discarded(
    deletion_v2: DeletionHarness, database_connection: Connection[Any]
) -> None:
    from psycopg.errors import NotNullViolation

    h, connection = deletion_v2, database_connection
    with pytest.raises(NotNullViolation):
        insert_proof(h, connection, mutate="null_decision")
    connection.rollback()
    identifier = insert_proof(h, connection)
    connection.commit()
    for sql in (
        "UPDATE account.account_deletion_proof SET decision='ATTEMPTED' WHERE operation_id=%s",
        "DELETE FROM account.account_deletion_proof WHERE operation_id=%s",
    ):
        with pytest.raises(RaiseException):
            connection.execute(sql, (identifier,))
        connection.rollback()
    with pytest.raises(RaiseException):
        connection.execute("TRUNCATE account.account_deletion_proof")
    connection.rollback()


@pytest.mark.parametrize("member", ["expected_api_origin", "expected_stream_origin"])
@pytest.mark.parametrize(
    "origin,allowed",
    [
        ("https://a:00001", True),
        ("http://EXAMPLE.test.:65535", True),
        ("https://[::1]:443", True),
        ("https://[::ffff:192.0.2.1]", True),
        ("https://a:0", False),
        ("https://a:65536", False),
        ("https://a:100000", False),
        ("https://a:000001", False),
        ("https://a/", False),
        ("https://[::ffff:192..2.1]", False),
        ("https://[::ffff:192.00.2.1]", False),
        ("https://[::ffff:256.0.2.1]", False),
        ("https://[127.0.0.1]", False),
        ("HTTPS://a", False),
        ("https://bad..example", False),
        ("https://a\n", False),
    ],
)
def test_frozen_origin_family_predicate_preserves_spelling_and_strict_suffix(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    member: str,
    origin: str,
    allowed: bool,
) -> None:
    h = deletion_v2
    doc = json.loads(h.wire("request"))
    doc.update(operation_id=str(uuid4()), **{member: origin})
    original = json.loads(sign_original(doc, h.pair.key, "request"))
    assert original[member] == origin
    assert database_connection.execute(
        "SELECT app_private.valid_deletion_document_v2(%s,'REQUEST')", (Jsonb(original),)
    ).fetchone() == (allowed,)
    assert database_connection.execute(
        "SELECT app_private.valid_deletion_document_v2(%s,NULL)", (Jsonb(original),)
    ).fetchone() == (False,)


def test_cancel_sql_checks_exact_parent_and_live_result_retention(
    deletion_v2: DeletionHarness, database_connection: Connection[Any]
) -> None:
    h, connection = deletion_v2, database_connection
    h.profile.clock["now"] -= timedelta(days=2)
    h.accept()
    result = h.send("cancel").json()
    operation = result["operation_id"]
    h.profile.clock["now"] += timedelta(days=2)
    # Reply expiry is not permission to discard a live original result.
    assert connection.execute(
        (
            "SELECT expires_at<clock_timestamp() FROM account.account_recovery_operation "
            "WHERE operation_id=%s"
        ),
        (operation,),
    ).fetchone() == (True,)
    connection.commit()
    with pytest.raises(RaiseException, match="live recovery v2 result"):
        connection.execute(
            "DELETE FROM account.account_recovery_operation WHERE operation_id=%s", (operation,)
        )
    connection.rollback()
    identifier = insert_proof(h, connection)
    connection.commit()
    cleanup_expired_recovery_operations(h.recovery.sessions)
    assert connection.execute(
        "SELECT count(*) FROM account.account_recovery_operation WHERE operation_id=%s",
        (operation,),
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT count(*) FROM account.account_deletion_proof WHERE operation_id=%s", (identifier,)
    ).fetchone() == (1,)
    connection.commit()
    with pytest.raises(RaiseException):
        connection.execute(
            (
                "UPDATE account.account_recovery_operation SET proof_domain='wrong' WHERE "
                "operation_id=%s"
            ),
            (operation,),
        )
    connection.rollback()
    connection.execute(
        "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=%s",
        (result["session_id"],),
    )
    connection.commit()
    cleanup_expired_recovery_operations(h.recovery.sessions)
    assert connection.execute(
        "SELECT count(*) FROM account.account_recovery_operation WHERE operation_id=%s",
        (operation,),
    ).fetchone() == (0,)
    connection.commit()
    assert h.send("outcome").status_code != 200


def test_0074_empty_lifecycle_and_0073_data_projection_is_preserved(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0073_device_recovery_v2")
    with database_harness.connect(empty_database_name) as connection:
        before = snapshot_schema(connection)
        rows = connection.execute(
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
        ).fetchall()
    database_harness.upgrade(empty_database_name, "0074_device_deletion_v2")
    database_harness.downgrade(empty_database_name, "0073_device_recovery_v2")
    with database_harness.connect(empty_database_name) as connection:
        assert snapshot_schema(connection) == before
        assert (
            connection.execute(
                "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
            ).fetchall()
            == rows
        )
    database_harness.upgrade(empty_database_name, "0074_device_deletion_v2")


def test_retained_proof_downgrade_refuses_before_any_drop(
    deletion_v2: DeletionHarness,
    database_harness: DatabaseHarness,
    database_name: str,
    database_connection: Connection[Any],
) -> None:
    h, connection = deletion_v2, database_connection
    h.accept()
    before = snapshot_schema(connection)
    rows = connection.execute(
        "SELECT to_jsonb(r) FROM account.account_deletion_proof r ORDER BY operation_id"
    ).fetchall()
    connection.commit()
    with pytest.raises(Exception, match="Refusing to discard retained deletion v2 evidence"):
        database_harness.downgrade(database_name, "0073_device_recovery_v2")
    assert snapshot_schema(connection) == before
    assert (
        connection.execute(
            "SELECT to_jsonb(r) FROM account.account_deletion_proof r ORDER BY operation_id"
        ).fetchall()
        == rows
    )


def test_owner_purge_covers_new_originals_without_new_export_or_ttl(
    deletion_v2: DeletionHarness, database_connection: Connection[Any]
) -> None:
    h, connection = deletion_v2, database_connection
    h.profile.clock["now"] -= timedelta(days=31)
    h.accept()
    h.profile.clock["now"] += timedelta(days=31)
    # Original acceptance used the controlled historical fixture clock. Purge
    # now uses the real PG deadline; no row rewrite or trigger disabling.
    service = PrivacyDeletionService(h.recovery.sessions, h.ledger)
    service.purge(h.pair.actor.user_id, UUID(present(h.parent)["deletion_request_id"]))
    assert connection.execute(
        "SELECT count(*) FROM account.account_deletion_proof WHERE user_id=%s",
        (h.pair.actor.user_id,),
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT pg_get_function_result('account.verify_owner_absent(uuid,uuid[])'::regprocedure)"
    ).fetchone() == ("void",)
    assert connection.execute(
        "SELECT account.verify_owner_absent(%s,'{}'::uuid[])", (h.pair.actor.user_id,)
    ).fetchone() == ("",)
    assert h.ledger.request_read()[0].decision == "ATTEMPTED"
    assert h.send("receipt").status_code != 200


def insert_request_clone(h: DeletionHarness, connection: Connection[Any], timestamp: str) -> str:
    """Full signed ATTEMPTED original plus its actual typed PENDING result."""
    operation = insert_proof(h, connection, timestamp=timestamp, decision="ATTEMPTED")
    connection.execute(
        "UPDATE account.user_account SET status='DELETION_PENDING' WHERE user_id=%s",
        (h.pair.actor.user_id,),
    )
    connection.execute(
        "INSERT INTO account.account_deletion_request(deletion_request_i"
        "d,user_id,server_instance_id,identity_epoch,"
        "identity_thumbprint_sha256,request_sha256,actor_device_id,actor_public_key_spki,code_generation,code_verifier_sha256,"
        "authority_generation,requested_at,cancel_before,state,revision,contract_version,platform) "
        "SELECT operation_id,user_id,server_instance_id,identity_epoch,"
        "decode(original_request_document->>'expected_identity_thumbprint_sha256','hex'),request_sha256,"
        "actor_device_id,actor_public_key_spki,(original_request_document->>'expected_code_generation')::bigint,"
        "code_verifier_sha256,(original_request_document->>'expected_authority_generation')::bigint+1,"
        "decided_at,decided_at+interval '720 hours','PENDING',1,'v2',platform "
        "FROM account.account_deletion_proof WHERE operation_id=%s",
        (operation,),
    )
    return operation


def insert_cancel_clone(
    h: DeletionHarness,
    connection: Connection[Any],
    *,
    timestamp: str | None = None,
    mutation: str | None = None,
) -> str:
    """A genuine independently signed CANCEL and matching result rows, no bearer grant.

    The caller owns a real accepted PENDING parent. Every variant uses new IDs,
    a fresh refresh hash and a signature/full-original digest generated AFTER body
    changes. SQL result mutations deliberately leave the valid original intact.
    """
    assert h.parent is not None
    doc = json.loads(h.wire("cancel"))
    doc.update(
        operation_id=str(uuid4()),
        binding_commit_id=str(uuid4()),
        next_refresh_token_sha256=hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    if timestamp is not None:
        doc["requested_at"] = timestamp
    if mutation == "parent":
        doc["deletion_request_id"] = str(uuid4())
    if mutation == "revision":
        doc["expected_revision"] += 1
    raw = sign_original(doc, h.recipient, "cancel")
    doc = json.loads(raw)
    device, binding = uuid4(), uuid4()
    platform = (
        ("ANDROID" if doc["platform"] == "MACOS" else "MACOS")
        if mutation == "platform"
        else doc["platform"]
    )
    connection.execute(
        "INSERT INTO account.device(device_id,user_id,device_name,platform,app_version,public_key,"
        "public_key_thumbprint_sha256,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            device,
            doc["account_id"],
            doc["device_name"],
            platform,
            doc["app_version"],
            base64.b64decode(doc["device_public_key_spki_b64"]),
            bytes.fromhex(doc["device_key_thumbprint_sha256"]),
            h.now(),
            h.now(),
        ),
    )
    connection.execute(
        "INSERT INTO account.user_session(session_id,user_id,device_id,r"
        "efresh_token_hash,issued_at,expires_at,"
        "last_rotated_at,family_id,generation,session_mode,revoked_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'V2',%s)",
        (
            binding,
            doc["account_id"],
            device,
            b"r" * 32 if mutation == "refresh" else bytes.fromhex(doc["next_refresh_token_sha256"]),
            h.now() + timedelta(seconds=1) if mutation == "issued_at" else h.now(),
            h.now() + timedelta(days=90),
            h.now(),
            uuid4() if mutation == "family" else binding,
            1 if mutation == "generation" else 0,
            h.now() if mutation == "revoked" else None,
        ),
    )
    connection.execute(
        "INSERT INTO account.account_recovery_operation(operation_id,kin"
        "d,user_id,server_instance_id,identity_epoch,"
        "request_sha256,previous_generation,result_generation,authority_generation,spent_verifier_sha256,"
        "next_verifier_sha256,result_device_id,result_session_id,binding_commit_id,created_at,expires_at,"
        "contract_version,platform,original_document_sha256,original_req"
        "uest_document,signature_algorithm,proof_domain) "
        "SELECT %s,'DELETE_CANCEL',user_id,server_instance_id,identity_e"
        "poch,%s,code_generation,code_generation+1,"
        "authority_generation+1,%s,%s,%s,%s,%s,%s,%s,'v2',%s,%s,%s,'ES256-P1363',%s "
        "FROM account.account_deletion_request WHERE deletion_request_id=%s",
        (
            doc["operation_id"],
            bytes.fromhex(doc["request_sha256"]),
            b"v" * 32
            if mutation == "verifier"
            else bytes(
                present(
                    connection.execute(
                        (
                            "SELECT code_verifier_sha256 "
                            "FROM account.account_deletion_request WHERE "
                            "deletion_request_id=%s"
                        ),
                        (h.parent["deletion_request_id"],),
                    ).fetchone()
                )[0]
            ),
            bytes.fromhex(doc["next_code_verifier_sha256"]),
            device,
            binding,
            doc["binding_commit_id"],
            h.now(),
            h.now() + timedelta(days=1),
            doc["platform"],
            hashlib.sha256(raw).digest(),
            Jsonb(doc),
            "autplay:device-compatibility:deletion:cancel:v2\n",
            h.parent["deletion_request_id"],
        ),
    )
    return require_instance(doc["operation_id"], str)


@pytest.mark.parametrize("kind", ["SEALED", "REQUEST", "CANCEL"])
@pytest.mark.parametrize(
    "invalid",
    [
        "2026-10-16T24:00:00Z",
        "2026-10-16T23:59:60Z",
        "2026-10-16T23:60:00Z",
        "2026-10-16T00:00:00+00:00",
        "2026-10-16T00:00:00.1234567890Z",
        "2026-02-30T00:00:00Z",
    ],
)
def test_raw_timestamp_sql_rejects_python_invalid_signed_original_after_valid_boundary(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    kind: str,
    invalid: str,
) -> None:
    h, connection = deletion_v2, database_connection
    if kind == "CANCEL":
        h.accept()

    def insert(stamp: str) -> str:
        if kind == "SEALED":
            return insert_proof(h, connection, timestamp=stamp)
        if kind == "REQUEST":
            return insert_request_clone(h, connection, stamp)
        return insert_cancel_clone(h, connection, timestamp=stamp)

    # No canonicalization/rounding of signed nanoseconds, including year 9999.
    for boundary in (
        "2026-10-16T00:00:00Z",
        "2026-10-16T23:59:59.1Z",
        "9999-12-31T23:59:59.999999999Z",
    ):
        operation = insert(boundary)
        connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
        table = (
            "account.account_recovery_operation"
            if kind == "CANCEL"
            else "account.account_deletion_proof"
        )
        assert connection.execute(
            "SELECT original_request_document->>'requested_at' FROM "
            + table
            + " WHERE operation_id=%s",
            (operation,),
        ).fetchone() == (boundary,)
        connection.rollback()  # Each control owns disposable rows; the real parent survives.
    with pytest.raises(CheckViolation) as caught:
        insert(invalid)
    assert caught.value.diag.constraint_name == (
        "recovery_operation_v2_check" if kind == "CANCEL" else "deletion_proof_v2_check"
    )
    connection.rollback()


@pytest.mark.parametrize(
    "mutation",
    [
        "parent",
        "revision",
        "verifier",
        "platform",
        "refresh",
        "issued_at",
        "family",
        "generation",
        "revoked",
    ],
)
def test_cancel_direct_sql_requires_exact_parent_and_result_after_valid_control(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    mutation: str,
) -> None:
    h, connection = deletion_v2, database_connection
    h.accept()
    insert_cancel_clone(h, connection)
    connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
    connection.rollback()
    before = connection.execute(
        "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
    ).fetchall()
    connection.commit()
    with pytest.raises(RaiseException, match="deletion cancel original mismatch"):
        insert_cancel_clone(h, connection, mutation=mutation)
    connection.rollback()
    assert (
        connection.execute(
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
        ).fetchall()
        == before
    )


def test_populated_v1_and_recovery_v2_survive_adjacent_0074_roundtrip(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    from .conftest import prepare_adjacent_downgrade

    h, connection = recovery_v2, database_connection
    assert h.send("commit").status_code == 200
    rows = connection.execute(
        "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
    ).fetchall()
    assert {row[0]["contract_version"] for row in rows} == {"v1", "v2"}
    assert any(row[0]["original_request_document"] is not None for row in rows)
    connection.commit()
    # 0074 must permit the unchanged, populated 0073 RECOVER branch.
    prepare_adjacent_downgrade(database_harness, database_name, "0073_device_recovery_v2")
    before = snapshot_schema(connection)
    assert (
        connection.execute(
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
        ).fetchall()
        == rows
    )
    connection.commit()
    database_harness.upgrade(database_name, "0074_device_deletion_v2")
    assert (
        connection.execute(
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
        ).fetchall()
        == rows
    )
    connection.commit()
    database_harness.downgrade(database_name, "0073_device_recovery_v2")
    assert snapshot_schema(connection) == before
    assert (
        connection.execute(
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
        ).fetchall()
        == rows
    )
    connection.commit()
    database_harness.upgrade(database_name, "0074_device_deletion_v2")
    # Exact original remains a usable live result after the full schema roundtrip.
    assert h.send("outcome").status_code == 200
