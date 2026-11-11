"""UNEXECUTED 0073 typed history, direct INSERT controls and downgrade preflight.

No imports/SQL/hashes/signatures/validators have run. Direct INSERT tests assert
the exact CHECK after a valid positive control using fresh operation/binding IDs.
Legacy projections are explicit across the added columns. SQL checks shape and
typed consistency, not whole-document cryptography. Representable invalid bodies
are independently signed after mutation; impossible proof shapes use explicitly
paired SQL-helper controls before the failing INSERT.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from uuid import uuid4

import pytest
import rfc8785
from autplay.application.account_recovery import cleanup_expired_recovery_operations
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from psycopg import Connection
from psycopg.errors import CheckViolation, RaiseException
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError
from typed_assertions import require_instance

from .conftest import DatabaseHarness, prepare_adjacent_downgrade
from .schema_contract import snapshot_schema
from .test_device_profile_v2 import profile as profile
from .test_device_recovery_v2 import RecoveryHarness
from .test_device_recovery_v2 import recovery_v2 as recovery_v2
from .test_profile_pairing_m5b import pairing_service as pairing_service

LEGACY = (
    "SELECT operation_id,kind,user_id,server_instance_id,identity_epoch,request_sha256,"
    "actor_device_id,actor_family_id,previous_generation,result_generation,authority_generation,"
    "spent_verifier_sha256,next_verifier_sha256,result_device_id,result_session_id,binding_commit_id,"
    "created_at,expires_at FROM account.account_recovery_operation ORDER BY operation_id"
)
FULL_HISTORY = "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
PROOF_DOMAIN = b"autplay:device-compatibility:recovery:recover:v2\n"
ROW_MISMATCHES = frozenset(
    {"legacy_marker", "future_marker", "platform_mismatch", "wrong_identity"}
)


def sign_history_original(doc: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> bytes:
    """Independent future fixture recipe; no application parser/hash/sign helper."""
    doc.pop("request_sha256", None)
    doc.pop("device_signature_b64url", None)
    digest = hashlib.sha256(rfc8785.dumps(doc)).digest()
    doc["request_sha256"] = digest.hex()
    der = key.sign(PROOF_DOMAIN + digest, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    proof = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    doc["device_signature_b64url"] = base64.urlsafe_b64encode(proof).rstrip(b"=").decode("ascii")
    raw = rfc8785.dumps(doc)

    # Re-read the final original: a later mutation must not inherit stale D/proof.
    final = json.loads(raw)
    unsigned = {
        name: value
        for name, value in final.items()
        if name not in {"request_sha256", "device_signature_b64url"}
    }
    assert hashlib.sha256(rfc8785.dumps(unsigned)).digest() == bytes.fromhex(
        final["request_sha256"]
    )
    spki = base64.b64decode(final["device_public_key_spki_b64"], validate=True)
    assert len(spki) == 91
    assert spki == key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    decoded = base64.urlsafe_b64decode(final["device_signature_b64url"] + "==")
    assert len(decoded) == 64
    key.public_key().verify(
        utils.encode_dss_signature(
            int.from_bytes(decoded[:32], "big"), int.from_bytes(decoded[32:], "big")
        ),
        PROOF_DOMAIN + bytes.fromhex(final["request_sha256"]),
        ec.ECDSA(hashes.SHA256()),
    )
    assert rfc8785.dumps(final) == raw
    return raw


def clone_history(
    h: RecoveryHarness,
    connection: Connection[Any],
    *,
    mutate: str | None = None,
    origin: tuple[str, str] | None = None,
    helper_accepts: bool = True,
    expired: bool = False,
) -> str:
    source = json.loads(h.wire("commit"))
    source_id = source["operation_id"]
    doc = {
        name: value
        for name, value in source.items()
        if name not in {"request_sha256", "device_signature_b64url"}
    }
    doc.update(operation_id=str(uuid4()), binding_commit_id=str(uuid4()))
    assert len({source_id, doc["operation_id"], doc["binding_commit_id"]}) == 3
    assert doc["binding_commit_id"] != source["binding_commit_id"]
    platform, version = doc["platform"], "v2"
    if origin is not None:
        member, value = origin
        assert member in {"expected_api_origin", "expected_stream_origin"}
        doc[member] = value
    if mutate == "extra_secret":
        doc["recovery_code"] = "synthetic-must-be-rejected"
    elif mutate == "nested_identity":
        doc["expected_identity_epoch"] = {"value": 1}
    elif mutate == "boolean_generation":
        doc["expected_code_generation"] = True
    elif mutate == "legacy_marker":
        version = "v1"
    elif mutate == "future_marker":
        version = "v3"
    elif mutate == "platform_mismatch":
        platform = "ANDROID" if platform == "MACOS" else "MACOS"
    elif mutate == "null_generation":
        doc["expected_code_generation"] = None
    elif mutate == "wrong_identity":
        # Only the row/document server-instance equality is intentionally false.
        doc["expected_server_instance_id"] = str(uuid4())
    elif mutate == "oversized":
        doc["device_name"] = "x" * 8193
    elif mutate == "wrong_key_thumbprint":
        thumbprint = doc["device_key_thumbprint_sha256"]
        doc["device_key_thumbprint_sha256"] = ("0" if thumbprint[0] != "0" else "1") + thumbprint[
            1:
        ]
    elif mutate not in {None, "root_array", "nested_signature", "missing_original"}:
        raise AssertionError("unknown history mutation")

    # Every JSON-object mutation above precedes independent D/proof generation,
    # including invalid origins/thumbprints, nested/null types and oversized text.
    raw = sign_history_original(doc, h.pair.key)
    if mutate == "wrong_key_thumbprint":
        assert (
            hashlib.sha256(
                base64.b64decode(doc["device_public_key_spki_b64"], validate=True)
            ).hexdigest()
            != doc["device_key_thumbprint_sha256"]
        )
    stored: Any = doc
    if mutate in {"root_array", "nested_signature", "missing_original"}:
        # These shapes cannot carry a RecoveryCommit proof. Use a signed sibling
        # as the positive helper control; the altered values are shape sentinels,
        # with no claim that an array/NULL/nested signature is cryptographically valid.
        assert connection.execute(
            "SELECT app_private.valid_recovery_commit_v2(%s)", (Jsonb(doc),)
        ).fetchone() == (True,)
        if mutate == "root_array":
            stored = [doc]
        elif mutate == "nested_signature":
            stored = {**doc, "device_signature_b64url": ["shape-sentinel"]}
        else:
            stored = None
    encoded = Jsonb(stored) if stored is not None else None
    assert connection.execute(
        "SELECT app_private.valid_recovery_commit_v2(%s)", (encoded,)
    ).fetchone() == (helper_accepts,)

    # Valid typed columns follow the intended document. Non-scalar shape cases
    # use the source's valid integer solely to reach the helper/CHECK, and the
    # explicit wrong_identity case alone retains a different row instance.
    epoch = (
        doc["expected_identity_epoch"]
        if type(doc["expected_identity_epoch"]) is int
        else source["expected_identity_epoch"]
    )
    generation = (
        doc["expected_code_generation"]
        if type(doc["expected_code_generation"]) is int
        else source["expected_code_generation"]
    )
    instance = (
        source["expected_server_instance_id"]
        if mutate == "wrong_identity"
        else doc["expected_server_instance_id"]
    )
    full_original = rfc8785.dumps(stored)
    if stored is doc:
        assert full_original == raw
    original_digest = hashlib.sha256(full_original).digest()
    inserted = connection.execute(
        "INSERT INTO account.account_recovery_operation "
        "(operation_id,kind,user_id,server_instance_id,identity_epoch,"
        "request_sha256,actor_device_id,actor_family_id,previous_generation,result_generation,authority_generation,"
        "spent_verifier_sha256,next_verifier_sha256,result_device_id,result_session_id,binding_commit_id,"
        "created_at,expires_at,contract_version,platform,original_docume"
        "nt_sha256,original_request_document,signature_algorithm,proof_d"
        "omain) "
        "SELECT %s,kind,%s,%s,%s,%s,actor_device_id,actor_family_id,"
        "%s,%s,authority_generation,spent_verifier_sha256,%s,"
        "result_device_id,result_session_id,%s,"
        + (
            "statement_timestamp()-interval '2 days',statement_timestamp()-interval '1 day',"
            if expired
            else "created_at,expires_at,"
        )
        + (
            "%s,%s,%s,%s,%s,%s FROM account.account_recovery_operation WHERE "
            "operation_id=%s RETURNING operation_id::text"
        ),
        (
            doc["operation_id"],
            doc["account_id"],
            instance,
            epoch,
            bytes.fromhex(doc["request_sha256"]),
            generation,
            generation + 1,
            bytes.fromhex(doc["next_code_verifier_sha256"]),
            doc["binding_commit_id"],
            version,
            platform,
            original_digest,
            encoded,
            "ES256-P1363",
            PROOF_DOMAIN.decode("ascii"),
            source_id,
        ),
    ).fetchone()
    assert inserted == (doc["operation_id"],)
    assert connection.execute(
        "SELECT original_request_document,original_document_sha256,request_sha256 "
        "FROM account.account_recovery_operation WHERE operation_id=%s",
        (doc["operation_id"],),
    ).fetchone() == (stored, original_digest, bytes.fromhex(doc["request_sha256"]))
    return require_instance(doc["operation_id"], str)


def test_legacy_exact_columns_and_bytes_survive_0073_roundtrip(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    before = database_connection.execute(LEGACY).fetchall()
    database_connection.commit()
    database_harness.downgrade(database_name, "0072_device_self_v2")
    assert database_connection.execute(LEGACY).fetchall() == before
    database_connection.commit()
    database_harness.upgrade(database_name)
    assert database_connection.execute(LEGACY).fetchall() == before
    assert database_connection.execute(
        "SELECT DISTINCT contract_version,platform,original_document_sha256,"
        "original_request_document,signature_algorithm,proof_domain "
        "FROM account.account_recovery_operation"
    ).fetchall() == [("v1", None, None, None, None, None)]


@pytest.mark.parametrize(
    "mutation",
    [
        "root_array",
        "extra_secret",
        "nested_identity",
        "boolean_generation",
        "nested_signature",
        "missing_original",
        "legacy_marker",
        "future_marker",
        "platform_mismatch",
        "null_generation",
        "wrong_identity",
        "oversized",
        "wrong_key_thumbprint",
    ],
)
def test_direct_insert_has_positive_control_and_precise_shape_rejection(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    mutation: str,
) -> None:
    h = recovery_v2
    assert h.send("commit").status_code == 200
    control = clone_history(h, database_connection)
    database_connection.commit()
    assert database_connection.execute(
        "SELECT contract_version FROM account.account_recovery_operation WHERE operation_id=%s",
        (control,),
    ).fetchone() == ("v2",)
    database_connection.commit()
    before = database_connection.execute(FULL_HISTORY).fetchall()
    database_connection.commit()
    with pytest.raises(CheckViolation) as caught:
        clone_history(
            h, database_connection, mutate=mutation, helper_accepts=mutation in ROW_MISMATCHES
        )
    assert caught.value.diag.constraint_name == "recovery_operation_v2_check"
    database_connection.rollback()
    assert database_connection.execute(FULL_HISTORY).fetchall() == before


@pytest.mark.parametrize("member", ["expected_api_origin", "expected_stream_origin"])
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "https://node-1.example",
        "https://Node-1.Example.",
        "http://a:1",
        "https://a.:65535",
        "https://a:00001",
        "http://a:00080",
        "https://001.002.003.004",
        "https://999.999.999.999",
        "https://" + "a" * 63,
        "https://" + ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61)) + ".",
        "http://[::]",
        "https://[::1]:1",
        "https://[2001:0DB8:0:0:0:0:0:1]:65535",
        "https://[1:2:3:4:5:6:7::]",
        "https://[::ffff:192.0.2.128]:00001",
        "https://[::192.0.2.1]",
        "https://[2001:db8:0:0:0:0:192.0.2.1]",
        "https://[1:2:3:4:5:6:255.255.255.255]",
    ],
)
def test_direct_insert_preserves_accepted_origin_spellings(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    member: str,
    origin: str,
) -> None:
    h = recovery_v2
    assert h.send("commit").status_code == 200
    control = clone_history(h, database_connection)
    identifier = clone_history(h, database_connection, origin=(member, origin))
    assert identifier != control
    assert database_connection.execute(
        "SELECT original_request_document->>%s "
        "FROM account.account_recovery_operation WHERE operation_id=%s",
        (member, identifier),
    ).fetchone() == (origin,)


@pytest.mark.parametrize("member", ["expected_api_origin", "expected_stream_origin"])
@pytest.mark.parametrize(
    "origin",
    [
        "https://a:99999",
        "https://a:0",
        "https://a:00000",
        "https://a:65536",
        "https://a:100000",
        "https://a:",
        "https://a:-1",
        "https://a:+1",
        "https://-bad.example",
        "https://bad-.example",
        "https://bad..example",
        "https://.",
        "https://..",
        "https://" + "a" * 64,
        "https://" + ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 62)),
        "https://a/",
        "https://a?q=1",
        "https://a#fragment",
        "https://user@a",
        "https://%61.example",
        "https://a b",
        "https://a\n",
        "https://a\r",
        "https://\u00e9.example",
        "HTTPS://a",
        "https://[::1",
        "https://[::1]]",
        "https://[]",
        "https://[1234]",
        "https://[127.0.0.1]",
        "https://[:::]",
        "https://[1::2::3]",
        "https://[1:2:3:4:5:6:7]",
        "https://[1:2:3:4:5:6:7:8:9]",
        "https://[1:2:3:4:5:6:7:8::]",
        "https://[10000::]",
        "https://[gg::]",
        "https://[::ffff:192.168.001.1]",
        "https://[::ffff:192.0.2]",
        "https://[::ffff:192..2.1]",
        "https://[::ffff:256.0.2.1]",
        "https://[::ffff:192.0.2.1:1]",
        "https://[fe80::1%eth0]",
        "https://[::1/128]",
    ],
)
def test_direct_insert_rejects_only_the_mutated_origin(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    member: str,
    origin: str,
) -> None:
    h = recovery_v2
    assert h.send("commit").status_code == 200
    clone_history(h, database_connection)
    database_connection.commit()
    before = database_connection.execute(FULL_HISTORY).fetchall()
    database_connection.commit()
    with pytest.raises(CheckViolation) as caught:
        clone_history(h, database_connection, origin=(member, origin), helper_accepts=False)
    assert caught.value.diag.constraint_name == "recovery_operation_v2_check"
    database_connection.rollback()
    assert database_connection.execute(FULL_HISTORY).fetchall() == before


def test_immutable_proof_and_live_binding_retention_beyond_reply_expiry(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
) -> None:
    h = recovery_v2
    binding = h.send("commit").json()
    identifier = clone_history(h, database_connection, expired=True)
    database_connection.commit()
    with pytest.raises(RaiseException, match="immutable"):
        database_connection.execute(
            "UPDATE account.account_recovery_operation SET platform=platform WHERE operation_id=%s",
            (identifier,),
        )
    database_connection.rollback()
    with pytest.raises(RaiseException, match="live recovery v2 result"):
        database_connection.execute(
            "DELETE FROM account.account_recovery_operation WHERE operation_id=%s", (identifier,)
        )
    database_connection.rollback()
    cleanup_expired_recovery_operations(h.v1.sessions)
    assert database_connection.execute(
        "SELECT count(*) FROM account.account_recovery_operation WHERE operation_id=%s",
        (identifier,),
    ).fetchone() == (1,)
    database_connection.execute(
        "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=%s",
        (binding["session_id"],),
    )
    database_connection.commit()
    cleanup_expired_recovery_operations(h.v1.sessions)
    assert database_connection.execute(
        "SELECT count(*) FROM account.account_recovery_operation WHERE operation_id=%s",
        (identifier,),
    ).fetchone() == (0,)


@pytest.mark.parametrize("partial", [False, True])
def test_downgrade_preflight_precedes_every_removal_and_function_replacement(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
    partial: bool,
) -> None:
    h = recovery_v2
    if not partial:
        # Issue genuine retained recovery evidence while runtime readiness is
        # intact. Adjacent-downgrade setup deliberately clears capacity evidence.
        assert h.send("commit").status_code == 200
    # Isolate 0073 before corrupting CONFIGURE; intact recovery v2 is retained
    # by 0074's downgrade, but partial corruption would correctly block it.
    database_connection.commit()
    prepare_adjacent_downgrade(database_harness, database_name, "0073_device_recovery_v2")
    if partial:
        # DISPOSABLE corruption fixture: prove downgrade fails closed even if an
        # operator previously bypassed the insertion CHECK/immutable trigger.
        database_connection.execute(
            "ALTER TABLE account.account_recovery_operation DROP CONSTRAINT "
            "recovery_operation_v2_check"
        )
        database_connection.execute(
            "ALTER TABLE account.account_recovery_operation DISABLE TRIGGER "
            "guard_recovery_operation"
        )
        database_connection.execute(
            "UPDATE account.account_recovery_operation SET platform='ANDROID'"
        )
        database_connection.execute(
            "ALTER TABLE account.account_recovery_operation ENABLE TRIGGER guard_recovery_operation"
        )
        database_connection.commit()
    before = snapshot_schema(database_connection)
    rows = database_connection.execute(
        "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
    ).fetchall()
    function = database_connection.execute(
        "SELECT pg_get_functiondef('account.guard_recovery_operation()'::regprocedure)"
    ).fetchone()
    database_connection.commit()
    with pytest.raises(DBAPIError, match="Refusing to discard retained recovery v2 evidence"):
        database_harness.downgrade(database_name, "0072_device_self_v2")
    assert snapshot_schema(database_connection) == before
    assert (
        database_connection.execute(
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id"
        ).fetchall()
        == rows
    )
    assert (
        database_connection.execute(
            "SELECT pg_get_functiondef('account.guard_recovery_operation()'::regprocedure)"
        ).fetchone()
        == function
    )
    assert database_connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0073_device_recovery_v2",
    )


def test_adjacent_0073_positive_legacy_configure_retains_all_original_columns(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    connection = database_connection
    before = connection.execute(LEGACY).fetchall()
    assert before and connection.execute(
        "SELECT DISTINCT contract_version,platform FROM account.account_recovery_operation"
    ).fetchall() == [("v1", None)]
    connection.commit()
    prepare_adjacent_downgrade(database_harness, database_name, "0073_device_recovery_v2")
    database_harness.downgrade(database_name, "0072_device_self_v2")
    assert connection.execute(LEGACY).fetchall() == before
    connection.commit()
    database_harness.upgrade(database_name, "0073_device_recovery_v2")
    assert connection.execute(LEGACY).fetchall() == before
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0073_device_recovery_v2",
    )
