"""UNEXECUTED direct-INSERT gates for 0069's nonsecret receipt projection.

Every key, signature, SQL statement and assertion here is a future disposable
fixture action. Positive copies precede negative INSERTs; neither the application
parser nor an UPDATE immutability trigger may mask the intended CHECK failure.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from psycopg import Connection
from psycopg.errors import CheckViolation

from .test_device_profile_v2 import ROTATE_DOMAIN, ProfileHarness, signed
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service

STORAGE = {
    "enrollment.exchange": (
        "enrollment_exchange_receipt",
        "exchange_id",
        "enrollment_receipt_contract_evidence_check",
        (
            "exchange_id",
            "binding_commit_id",
            "invitation_id",
            "expected_user_id",
            "device_name",
            "app_version",
            "next_refresh_token_sha256",
            "client_nonce_b64url",
            "request_sha256",
        ),
    ),
    "session.rotate": (
        "session_rotation_receipt",
        "rotation_id",
        "rotation_receipt_contract_evidence_check",
        (
            "rotation_id",
            "device_id",
            "parent_session_id",
            "current_generation",
            "next_refresh_token_sha256",
            "request_sha256",
        ),
    ),
}
ROOT_STRINGS = (
    "operation",
    "contract_version",
    "platform",
    "server_instance_id",
    "identity_thumbprint_sha256",
    "api_origin",
    "stream_origin",
    "device_public_key_spki_hex",
    "device_key_thumbprint_sha256",
    "domain",
    "signature_algorithm",
    "proof_digest",
    "original_document_sha256",
    "signature_hex",
)


def populate(profile: ProfileHarness) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))


def insert_copy(
    connection: Connection[Any],
    operation: str,
    *,
    path: tuple[str, ...] | None = None,
    value: str | None = None,
) -> None:
    table, primary, _, _ = STORAGE[operation]
    projection = "source.original_request_evidence"
    parameters: list[object] = [uuid4()]
    if path is not None:
        assert value is not None
        projection = "jsonb_set(source.original_request_evidence,%s::text[],%s::jsonb)"
        parameters.extend((list(path), value))
    replacements = (
        f"jsonb_build_object('{primary}',%s::uuid,'original_request_evidence',{projection})"
    )
    if operation == "enrollment.exchange":
        replacements += " || jsonb_build_object('binding_commit_id',%s::uuid)"
        parameters.append(uuid4())
    parameters.append(operation)
    inserted = connection.execute(
        f"INSERT INTO account.{table} "
        f"SELECT (jsonb_populate_record(NULL::account.{table},"
        f"to_jsonb(source) || {replacements})).* "
        f"FROM account.{table} source WHERE source.original_request_evidence->>'operation'=%s "
        f"LIMIT 1 RETURNING {primary}",
        parameters,
    ).fetchone()
    assert inserted is not None


@pytest.mark.parametrize("operation", tuple(STORAGE))
def test_receipt_insert_rejects_nested_and_wrong_scalar_evidence_values(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    operation: str,
) -> None:
    # The imported profile fixture supplies genuinely enrolled ANDROID and MACOS.
    populate(profile)
    table, primary, constraint, fields = STORAGE[operation]
    before = database_connection.execute(
        f"SELECT * FROM account.{table} ORDER BY {primary}",
    ).fetchall()
    assert len(before) == 1
    database_connection.commit()
    # Keep hashes, original signature, authority, FKs and expiry intact. Only
    # synthetic storage IDs change to avoid a uniqueness failure on this copy.
    insert_copy(database_connection, operation)
    database_connection.rollback()
    paths = (
        *tuple((field,) for field in ROOT_STRINGS),
        ("identity_epoch",),
        ("schema_version",),
        *tuple(("fields", field) for field in fields),
    )
    for path in paths:
        numeric = path in (
            ("identity_epoch",),
            ("schema_version",),
            ("fields", "current_generation"),
        )
        wrong_scalar = '"0"' if numeric else "0"
        for value in (
            '{"refresh_token":"synthetic nested credential"}',
            '["synthetic nested credential"]',
            wrong_scalar,
            "true",
            "null",
        ):
            with pytest.raises(CheckViolation) as rejected:
                insert_copy(database_connection, operation, path=path, value=value)
            assert rejected.value.diag.constraint_name == constraint, (operation, path, value)
            database_connection.rollback()
            assert (
                database_connection.execute(
                    f"SELECT * FROM account.{table} ORDER BY {primary}",
                ).fetchall()
                == before
            )
