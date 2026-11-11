"""UNEXECUTED 0071 historical/immutable/secret-storage/downgrade DB gates."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from psycopg import Connection
from psycopg.errors import CheckViolation
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness
from .schema_contract import snapshot_schema
from .test_device_admission_s1b import admission_runtime as admission_runtime
from .test_device_admission_v2 import AdmissionHarness, proof
from .test_device_admission_v2 import admission_v2 as admission_v2

TABLES = (
    "device_admission",
    "trusted_device_key",
    "trusted_device_reenrollment_challenge",
    "device_admission_exchange_receipt",
    "device_admission_web_operation_receipt",
)

EVIDENCE_STORAGE = {
    "admission.submit": ("device_admission", "request_id", "admission_v2_evidence_check"),
    "trusted.challenge": (
        "trusted_device_reenrollment_challenge",
        "challenge_id",
        "trusted_challenge_v2_evidence_check",
    ),
    "admission.exchange": (
        "device_admission_exchange_receipt",
        "exchange_id",
        "admission_receipt_v2_evidence_check",
    ),
    "trusted.exchange": (
        "device_admission_exchange_receipt",
        "exchange_id",
        "admission_receipt_v2_evidence_check",
    ),
}


def populate(h: AdmissionHarness) -> None:
    admission = h.submit()
    h.bind(admission)
    challenge = h.service.trusted_challenge(
        proof(h.challenge_body(admission), admission.key, "trusted.challenge")
    )
    h.service.trusted_exchange(
        proof(h.trusted_body(admission, challenge), admission.key, "trusted.exchange")
    )


def insert_evidence_copy(
    connection: Connection[Any],
    operation: str,
    projection: str = "source.original_request_evidence",
    parameters: tuple[object, ...] = (),
) -> None:
    """Future direct INSERT: retain all original digests, FKs, times and authority.

    Only synthetic storage identifiers change to avoid duplicate keys. Each
    caller first proves this same INSERT accepts the unmodified flat evidence.
    No UPDATE/immutability trigger or application parser can mask the CHECK.
    """
    table, primary_key, _ = EVIDENCE_STORAGE[operation]
    replacements = (
        f"jsonb_build_object('{primary_key}',%s::uuid,'original_request_evidence',{projection})"
    )
    values: list[object] = [uuid4(), *parameters]
    if table == "device_admission_exchange_receipt":
        replacements += " || jsonb_build_object('binding_commit_id',%s::uuid)"
        values.append(uuid4())
    values.append(operation)
    inserted = connection.execute(
        f"INSERT INTO account.{table} "
        f"SELECT (jsonb_populate_record(NULL::account.{table},"
        f"to_jsonb(source) || {replacements})).* "
        f"FROM account.{table} source WHERE source.original_request_evidence->>'operation'=%s "
        f"LIMIT 1 RETURNING {primary_key}",
        values,
    ).fetchone()
    assert inserted is not None


@pytest.mark.parametrize(
    "operation,fields",
    [
        (
            "admission.submit",
            (
                "request_id",
                "client_nonce_b64url",
                "api_major",
                "nickname",
                "app_version",
                "requested_at",
                "device_model_hint",
            ),
        ),
        ("trusted.challenge", ("challenge_request_id", "account_id", "client_nonce_b64url")),
        (
            "admission.exchange",
            (
                "request_id",
                "request_sha256",
                "exchange_id",
                "binding_commit_id",
                "poll_bearer_sha256",
                "approved_account_id",
                "client_nonce_b64url",
                "next_refresh_token_sha256",
            ),
        ),
        (
            "trusted.exchange",
            (
                "challenge_id",
                "exchange_id",
                "binding_commit_id",
                "account_id",
                "next_refresh_token_sha256",
                "client_nonce_b64url",
            ),
        ),
    ],
)
def test_insert_rejects_objects_and_arrays_under_every_allowed_field(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    operation: str,
    fields: tuple[str, ...],
) -> None:
    # admission_v2 supplies both ANDROID and MACOS with verified original rows.
    populate(admission_v2)
    table, _, constraint = EVIDENCE_STORAGE[operation]
    before = database_connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
    assert before
    database_connection.commit()
    insert_evidence_copy(database_connection, operation)
    database_connection.rollback()
    for field in fields:
        for nested in (
            '{"challenge":"synthetic nested credential"}',
            '["synthetic nested credential"]',
        ):
            with pytest.raises(CheckViolation) as rejected:
                insert_evidence_copy(
                    database_connection,
                    operation,
                    "jsonb_set(source.original_request_evidence,%s::text[],%s::jsonb)",
                    (["fields", field], nested),
                )
            assert rejected.value.diag.constraint_name == constraint, (operation, field, nested)
            database_connection.rollback()
            assert (
                database_connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
                == before
            )


@pytest.mark.parametrize("operation", tuple(EVIDENCE_STORAGE))
def test_insert_rejects_nested_and_wrong_scalar_types_in_evidence_envelope(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    operation: str,
) -> None:
    populate(admission_v2)
    table, _, constraint = EVIDENCE_STORAGE[operation]
    before = database_connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
    assert before
    database_connection.commit()
    insert_evidence_copy(database_connection, operation)
    database_connection.rollback()
    for field in ("server_instance_id", "identity_epoch", "api_origin", "stream_origin"):
        wrong_scalar = '"1"' if field == "identity_epoch" else "1"
        for value in (
            '{"challenge":"synthetic nested credential"}',
            '["synthetic nested credential"]',
            wrong_scalar,
            "true",
            "null",
        ):
            with pytest.raises(CheckViolation) as rejected:
                insert_evidence_copy(
                    database_connection,
                    operation,
                    "jsonb_set(source.original_request_evidence,%s::text[],%s::jsonb)",
                    ([field], value),
                )
            assert rejected.value.diag.constraint_name == constraint, (operation, field, value)
            database_connection.rollback()
            assert (
                database_connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
                == before
            )


@pytest.mark.parametrize(
    "operation,field,value",
    [
        ("admission.submit", "api_major", '"1"'),
        ("admission.submit", "api_major", "true"),
        ("admission.submit", "api_major", "null"),
        ("admission.submit", "api_major", "2"),
        ("admission.submit", "api_major", "1.5"),
        ("admission.submit", "device_model_hint", "1"),
        ("admission.submit", "device_model_hint", "true"),
        ("trusted.challenge", "account_id", "1"),
        ("admission.exchange", "request_sha256", "false"),
        ("trusted.exchange", "challenge_id", "null"),
    ],
)
def test_insert_rejects_wrong_scalar_types_under_allowed_fields(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    operation: str,
    field: str,
    value: str,
) -> None:
    populate(admission_v2)
    database_connection.commit()
    insert_evidence_copy(database_connection, operation)
    database_connection.rollback()
    with pytest.raises(CheckViolation) as rejected:
        insert_evidence_copy(
            database_connection,
            operation,
            "jsonb_set(source.original_request_evidence,%s::text[],%s::jsonb)",
            (["fields", field], value),
        )
    assert rejected.value.diag.constraint_name == EVIDENCE_STORAGE[operation][2]
    database_connection.rollback()


@pytest.mark.parametrize(
    "projection",
    [
        "source.original_request_evidence",
        "source.original_request_evidence #- '{fields,device_model_hint}'",
        "jsonb_set(source.original_request_evidence,'{fields,device_model_hint}','null'::jsonb)",
    ],
)
def test_insert_accepts_flat_admission_with_text_absent_or_null_model_hint(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    projection: str,
) -> None:
    populate(admission_v2)
    database_connection.commit()
    insert_evidence_copy(database_connection, "admission.submit")
    database_connection.rollback()
    insert_evidence_copy(database_connection, "admission.submit", projection)
    database_connection.rollback()


@pytest.mark.parametrize("retained", TABLES)
def test_each_table_independently_prevents_all_downgrade_removal(
    admission_v2: AdmissionHarness,
    database_harness: DatabaseHarness,
    database_name: str,
    database_connection: Connection[Any],
    retained: str,
) -> None:
    populate(admission_v2)
    # Isolate one retained table in a guarded DISPOSABLE fixture. Never remove
    # the retained evidence in order to make downgrade fit.
    for table in TABLES:
        if table != retained:
            database_connection.execute(f"DELETE FROM account.{table}")
    rows = database_connection.execute(f"SELECT * FROM account.{retained} ORDER BY 1").fetchall()
    assert rows
    before = snapshot_schema(database_connection)
    database_connection.commit()
    with pytest.raises(DBAPIError, match="refusing to discard admission v2 evidence"):
        database_harness.downgrade(database_name, "0070_device_portable_contract_v2")
    assert snapshot_schema(database_connection) == before
    assert database_connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0074_device_deletion_v2",
    )
    assert (
        database_connection.execute(f"SELECT * FROM account.{retained} ORDER BY 1").fetchall()
        == rows
    )


@pytest.mark.parametrize(
    "table,column,expression",
    [
        ("device_admission", "contract_version", "'v1'"),
        ("device_admission", "platform", "'OTHER'"),
        ("device_admission", "request_sha256", "decode(repeat('00',32),'hex')"),
        ("device_admission", "expires_at", "expires_at+interval '1 day'"),
        ("device_admission", "approved_account_role", "'USER'"),
        (
            "device_admission",
            "original_request_evidence",
            'original_request_evidence || \'{"raw_headers":"synthetic"}\'::jsonb',
        ),
        ("trusted_device_key", "platform", "'OTHER'"),
        ("trusted_device_key", "contract_version", "'v1'"),
        ("trusted_device_reenrollment_challenge", "trust_revision", "trust_revision+1"),
        ("trusted_device_reenrollment_challenge", "consumed_at", "NULL"),
        (
            "trusted_device_reenrollment_challenge",
            "challenge_hash",
            "decode(repeat('00',32),'hex')",
        ),
        (
            "device_admission_exchange_receipt",
            "binding_commit_id",
            "'00000000-0000-0000-0000-000000000071'::uuid",
        ),
        (
            "device_admission_exchange_receipt",
            "receipt_expires_at",
            "receipt_expires_at+interval '1 day'",
        ),
        ("device_admission_exchange_receipt", "authority_generation", "authority_generation+1"),
        ("device_admission_web_operation_receipt", "contract_version", "'v1'"),
    ],
)
def test_retained_v2_evidence_and_consumption_cannot_be_rewritten(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    table: str,
    column: str,
    expression: str,
) -> None:
    populate(admission_v2)
    before = database_connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
    database_connection.commit()
    with pytest.raises(CheckViolation):
        database_connection.execute(f"UPDATE account.{table} SET {column}={expression}")
    database_connection.rollback()
    assert (
        database_connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
        == before
    )


@pytest.mark.parametrize(
    "projection",
    [
        'original_request_evidence || \'{"request":{"challenge":"synthetic"}}\'::jsonb',
        'original_request_evidence || \'{"encrypted_request":"synthetic ciphertext"}\'::jsonb',
        "jsonb_set(original_request_evidence,'{fields,challenge}','\"synthetic\"'::jsonb)",
        "jsonb_set(original_request_evidence,'{fields,access_token}','\"synthetic\"'::jsonb)",
        (
            "jsonb_set(original_request_evidence,'{credential_hashes,challenge_sha256}','"
            '"synthetic plaintext"\'::jsonb)'
        ),
    ],
)
def test_insert_rejects_nested_encrypted_and_response_credentials_at_storage_boundary(
    admission_v2: AdmissionHarness, database_connection: Connection[Any], projection: str
) -> None:
    populate(admission_v2)
    database_connection.commit()
    with pytest.raises(CheckViolation) as rejected:
        # Fresh operation AND binding UUIDs prevent a uniqueness error from
        # masking the intended evidence constraint on a newly inserted row.
        database_connection.execute(
            "INSERT INTO account.device_admission_exchange_receipt(exchange_"
            "id,request_or_challenge_id,request_sha256,"
            "device_key_thumbprint_sha256,device_id,session_id,binding_commit_id,receipt_expires_at,created_at,"
            "contract_version,platform,original_document_sha256,original_req"
            "uest_evidence,authority_generation,trust_revision,account_role)"
            " "
            "SELECT %s,request_or_challenge_id,request_sha256,device_key_thu"
            "mbprint_sha256,device_id,session_id,%s,"
            "receipt_expires_at,created_at,contract_version,platform,original_document_sha256,"
            f"{projection},authority_generation,trust_revision,account_role "
            "FROM account.device_admission_exchange_receipt "
            "WHERE original_request_evidence->>'operation'='trusted.exchange'",
            (uuid4(), uuid4()),
        )
    assert rejected.value.diag.constraint_name == "admission_receipt_v2_evidence_check"
    database_connection.rollback()
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission_exchange_receipt"
    ).fetchone() == (2,)


def test_0071_preserves_all_legacy_rows_and_opaque_signed_hashes(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0070_device_portable_contract_v2")
    (
        user,
        device,
        session,
        instance,
        admission,
        challenge,
        exchange,
        binding,
        web,
        operation,
        reference,
    ) = (uuid4() for _ in range(11))
    new_columns = {
        ("device_admission"): (
            "'contract_version','original_document_sha256','original_request_evidence','a"
            "pproved_authority_generation','approved_account_role'"
        ),
        "trusted_device_key": "'contract_version','platform'",
        ("trusted_device_reenrollment_challenge"): (
            "'contract_version','platform','original_document_sha256','original_request_e"
            "vidence','authority_generation','trust_revision','account_role'"
        ),
        ("device_admission_exchange_receipt"): (
            "'contract_version','platform','original_document_sha256','original_request_e"
            "vidence','authority_generation','trust_revision','account_role'"
        ),
        "device_admission_web_operation_receipt": "'contract_version'",
    }
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            "INSERT INTO account.user_account(user_id,display_name) VALUES(%s,'D1 legacy history')",
            (user,),
        )
        connection.execute(
            (
                "INSERT INTO account.device(device_id,user_id,device_name,platform,app_versio"
                "n) VALUES(%s,%s,'D1 historical WEB','WEB','fixture')"
            ),
            (device, user),
        )
        connection.execute(
            (
                "INSERT INTO account.user_session(session_id,user_id,device_id,refresh_token_"
                "hash,issued_at,expires_at) VALUES(%s,%s,%s,%s,'2026-10-16 "
                "00:00:00+00','2026-10-17 00:00:00+00')"
            ),
            (session, user, device, b"r" * 32),
        )
        connection.execute(
            (
                "INSERT INTO account.server_instance(server_instance_id,identity_public_key_s"
                "pki,identity_thumbprint_sha256,label_hint,api_origin,stream_origin) "
                "VALUES(%s,%s,%s,'D1 history','https://api.test.invalid','https://stream.test"
                ".invalid')"
            ),
            (instance, b"p" * 91, b"i" * 32),
        )
        connection.execute(
            (
                "INSERT INTO account.device_admission(request_id,request_sha256,server_instan"
                "ce_id,identity_epoch,identity_thumbprint_sha256,device_public_key_spki,devic"
                "e_key_thumbprint_sha256,nickname,platform,app_version,api_major,requested_at"
                ",state,expires_at,review_locator_hash,poll_bearer_hash,created_at) "
                "VALUES(%s,%s,%s,1,%s,%s,%s,'D1 legacy admission','ANDROID','fixture',1,'2026"
                "-10-16 00:00:00+00','PENDING','2026-10-16 00:15:00+00',%s,%s,'2026-10-16 "
                "00:00:00+00')"
            ),
            (admission, b"s" * 32, instance, b"i" * 32, b"p" * 91, b"k" * 32, b"l" * 32, b"b" * 32),
        )
        connection.execute(
            (
                "INSERT INTO account.trusted_device_key(user_id,device_key_thumbprint_sha256,"
                "device_public_key_spki,approved_request_id,key_reference,revision) "
                "VALUES(%s,%s,%s,%s,%s,1)"
            ),
            (user, b"k" * 32, b"p" * 91, admission, reference),
        )
        connection.execute(
            (
                "INSERT INTO account.trusted_device_reenrollment_challenge(challenge_id,user_"
                "id,device_key_thumbprint_sha256,request_sha256,client_nonce_sha256,challenge"
                "_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,now()+interval '300 seconds')"
            ),
            (challenge, user, b"k" * 32, b"c" * 32, b"n" * 32, b"h" * 32),
        )
        connection.execute(
            (
                "INSERT INTO account.device_admission_exchange_receipt(exchange_id,request_or"
                "_challenge_id,request_sha256,device_key_thumbprint_sha256,device_id,session_"
                "id,binding_commit_id,receipt_expires_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,now()+interval '30 days')"
            ),
            (exchange, challenge, b"e" * 32, b"k" * 32, device, session, binding),
        )
        connection.execute(
            (
                "INSERT INTO account.web_session(web_session_id,family_id,server_instance_id,"
                "user_id,token_generation,token_sha256,csrf_sha256,issued_at,token_issued_at,"
                "last_activity_at,idle_expires_at,absolute_expires_at) "
                "VALUES(%s,%s,%s,%s,1,%s,%s,now(),now(),now(),now()+interval '30 "
                "minutes',now()+interval '8 hours')"
            ),
            (web, web, instance, user, b"t" * 32, b"f" * 32),
        )
        connection.execute(
            (
                "INSERT INTO account.device_admission_web_operation_receipt(operation_id,acto"
                "r_user_id,web_session_id,action,target_sha256,request_sha256,terminal_at,rec"
                "eipt_expires_at) VALUES(%s,%s,%s,'RESOLVE_REVIEW',%s,%s,now(),now()+interval"
                " '1 day')"
            ),
            (operation, user, web, b"l" * 32, b"w" * 32),
        )
        old = {
            table: connection.execute(
                f"SELECT to_jsonb(t) FROM account.{table} t ORDER BY 1::text"
            ).fetchall()
            for table in TABLES
        }
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        for table in TABLES:
            assert (
                connection.execute(
                    f"SELECT to_jsonb(t)-ARRAY[{new_columns[table]}] "
                    f"FROM account.{table} t ORDER BY 1::text"
                ).fetchall()
                == old[table]
            )
            assert connection.execute(
                f"SELECT DISTINCT contract_version FROM account.{table}"
            ).fetchall() == [("v1",)]
        for table in (
            "trusted_device_key",
            "trusted_device_reenrollment_challenge",
            "device_admission_exchange_receipt",
        ):
            assert connection.execute(f"SELECT platform FROM account.{table}").fetchall() == [
                (None,)
            ]
    database_harness.downgrade(empty_database_name, "0070_device_portable_contract_v2")
    with database_harness.connect(empty_database_name) as connection:
        for table in TABLES:
            assert (
                connection.execute(
                    f"SELECT to_jsonb(t) FROM account.{table} t ORDER BY 1::text"
                ).fetchall()
                == old[table]
            )


def test_partial_legacy_evidence_still_blocks_downgrade_before_any_removal(
    admission_v2: AdmissionHarness,
    database_harness: DatabaseHarness,
    database_name: str,
    database_connection: Connection[Any],
) -> None:
    from .test_device_admission_s1b import _admission

    _admission(admission_v2.runtime)
    # First prove normal storage rejects partial v1 evidence.
    with pytest.raises(CheckViolation):
        database_connection.execute(
            "UPDATE account.device_admission SET original_document_sha256=%s", (b"p" * 32,)
        )
    database_connection.rollback()
    # Simulate an already damaged/partially applied DISPOSABLE schema. The
    # migration's own preflight must remain effective without relying on its
    # CHECK still being present. No runtime or production permission is implied.
    database_connection.execute(
        "ALTER TABLE account.device_admission DROP CONSTRAINT admission_v2_evidence_check"
    )
    database_connection.execute(
        "UPDATE account.device_admission SET original_document_sha256=%s", (b"p" * 32,)
    )
    before = snapshot_schema(database_connection)
    database_connection.commit()
    with pytest.raises(DBAPIError, match="refusing to discard admission v2 evidence"):
        database_harness.downgrade(database_name, "0070_device_portable_contract_v2")
    assert snapshot_schema(database_connection) == before
    assert database_connection.execute(
        "SELECT original_document_sha256 FROM account.device_admission"
    ).fetchall() == [(b"p" * 32,)]
