"""UNEXECUTED 0069 retention/downgrade gates on guarded disposable databases."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection
from psycopg.errors import CheckViolation
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness
from .schema_contract import snapshot_schema
from .test_device_profile_v2 import ROTATE_DOMAIN, ProfileHarness, credential, signed
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import NOW
from .test_profile_pairing_m5b import pairing_service as pairing_service


def test_0069_preserves_historical_v1_rows_and_hashes(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name, "0068_device_capability_revision")
    user, device, session, instance, invitation, exchange, rotation = (uuid4() for _ in range(7))
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            "INSERT INTO account.user_account(user_id,display_name) VALUES(%s,'0069 history')",
            (user,),
        )
        connection.execute(
            "INSERT INTO account.device(device_id,user_id,device_name,platform,app_version) "
            "VALUES(%s,%s,'historical WEB','WEB','fixture')",
            (device, user),
        )
        connection.execute(
            "INSERT INTO account.user_session(session_id,user_id,device_id,r"
            "efresh_token_hash,issued_at,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (session, user, device, b"r" * 32, NOW, NOW + timedelta(days=1)),
        )
        connection.execute(
            "INSERT INTO account.server_instance(server_instance_id,identity_public_key_spki,"
            "identity_thumbprint_sha256,label_hint,api_origin,stream_origin) "
            "VALUES(%s,%s,%s,'0069 history','https://api.test.invalid','https://stream.test.invalid')",
            (instance, b"p" * 91, b"i" * 32),
        )
        connection.execute(
            "INSERT INTO account.enrollment_invitation(invitation_id,server_"
            "instance_id,user_id,issued_by_user_id,"
            "invitation_secret_hash,issued_at,expires_at,consumed_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (invitation, instance, user, user, b"s" * 32, NOW, NOW + timedelta(minutes=10), NOW),
        )
        connection.execute(
            "INSERT INTO account.enrollment_exchange_receipt(exchange_id,inv"
            "itation_id,request_sha256,"
            "device_key_thumbprint_sha256,device_id,session_id,binding_commi"
            "t_id,receipt_expires_at,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                exchange,
                invitation,
                b"e" * 32,
                b"k" * 32,
                device,
                session,
                uuid4(),
                NOW + timedelta(days=1),
                NOW,
            ),
        )
        connection.execute(
            "INSERT INTO account.session_rotation_receipt(rotation_id,parent"
            "_session_id,successor_session_id,"
            "request_sha256,device_key_thumbprint_sha256,receipt_expires_at,"
            "created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (rotation, session, session, b"o" * 32, b"k" * 32, NOW + timedelta(days=1), NOW),
        )
        old_enrollment = connection.execute(
            "SELECT * FROM account.enrollment_exchange_receipt"
        ).fetchall()
        old_rotation = connection.execute(
            "SELECT * FROM account.session_rotation_receipt"
        ).fetchall()
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        assert (
            connection.execute(
                "SELECT exchange_id,invitation_id,request_sha256,device_key_thum"
                "bprint_sha256,device_id,session_id,"
                "binding_commit_id,receipt_expires_at,created_at FROM "
                "account.enrollment_exchange_receipt",
            ).fetchall()
            == old_enrollment
        )
        assert (
            connection.execute(
                "SELECT rotation_id,parent_session_id,successor_session_id,request_sha256,"
                "device_key_thumbprint_sha256,receipt_expires_at,created_at "
                "FROM account.session_rotation_receipt",
            ).fetchall()
            == old_rotation
        )
        for table in ("enrollment_exchange_receipt", "session_rotation_receipt"):
            assert connection.execute(
                "SELECT contract_version,platform,original_document_sha256,"
                "original_request_evidence,authority_generation "
                f"FROM account.{table}",
            ).fetchall() == [("v1", None, None, None, None)]
    database_harness.downgrade(empty_database_name, "0068_device_capability_revision")
    with database_harness.connect(empty_database_name) as connection:
        assert (
            connection.execute("SELECT * FROM account.enrollment_exchange_receipt").fetchall()
            == old_enrollment
        )
        assert (
            connection.execute("SELECT * FROM account.session_rotation_receipt").fetchall()
            == old_rotation
        )


@pytest.mark.parametrize("retained", ["enrollment", "rotation"])
def test_downgrade_refuses_either_table_before_any_schema_removal(
    profile: ProfileHarness,
    database_harness: DatabaseHarness,
    database_name: str,
    database_connection: Connection[Any],
    retained: str,
) -> None:
    if retained == "enrollment":
        profile.enroll()
    else:
        # Existing platform-bearing device/session evidence, without a v2
        # enrollment receipt, isolates the rotation table's own preflight.
        invitation = profile.invitation()
        key, refresh = ec.generate_private_key(ec.SECP256R1()), credential()
        device, parent = uuid4(), uuid4()
        spki = public_spki(key)
        database_connection.execute(
            "INSERT INTO account.device(device_id,user_id,device_name,platfo"
            "rm,app_version,public_key,"
            "public_key_thumbprint_sha256) VALUES(%s,%s,'0069 parent',%s,'fixture',%s,%s)",
            (device, profile.owner.user_id, profile.platform, spki, public_key_thumbprint(spki)),
        )
        database_connection.execute(
            "INSERT INTO account.user_session(session_id,user_id,device_id,r"
            "efresh_token_hash,issued_at,"
            "expires_at,family_id,generation,session_mode) VALUES(%s,%s,%s,%s,%s,%s,%s,0,'V2')",
            (
                parent,
                profile.owner.user_id,
                device,
                hashlib.sha256(refresh.encode()).digest(),
                NOW,
                NOW + timedelta(days=90),
                parent,
            ),
        )
        database_connection.commit()
        original = {
            "expected_server_instance_id": invitation["server_instance_id"],
            "expected_identity_epoch": invitation["identity_epoch"],
            "expected_identity_thumbprint_sha256": invitation["identity_thumbprint_sha256"],
            "expected_api_origin": invitation["api_origin"],
            "expected_stream_origin": invitation["stream_origin"],
        }
        rotation, _ = profile.rotation(
            {"device_id": str(device), "session_id": str(parent)}, original, refresh
        )
        profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    before = snapshot_schema(database_connection)
    database_connection.commit()
    with pytest.raises(DBAPIError, match="refusing to discard device profile v2 evidence"):
        database_harness.downgrade(database_name, "0068_device_capability_revision")
    assert snapshot_schema(database_connection) == before
    assert database_connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0074_device_deletion_v2",
    )


@pytest.mark.parametrize(
    "field,sql_value",
    [
        ("contract_version", "'v1'"),
        ("platform", "'OTHER'"),
        ("original_document_sha256", "decode(repeat('00',32),'hex')"),
        (
            "original_request_evidence",
            'original_request_evidence || \'{"invitation_secret":"synthetic"}\'::jsonb',
        ),
        ("receipt_expires_at", "receipt_expires_at + interval '1 day'"),
        ("authority_generation", "authority_generation + 1"),
    ],
)
def test_v2_receipt_is_immutable_and_cannot_retain_added_credentials(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    field: str,
    sql_value: str,
) -> None:
    body, _, _, _ = profile.enroll()
    before = database_connection.execute(
        "SELECT * FROM account.enrollment_exchange_receipt"
    ).fetchall()
    database_connection.commit()
    with pytest.raises(CheckViolation):
        database_connection.execute(
            f"UPDATE account.enrollment_exchange_receipt SET {field}={sql_value} "
            "WHERE exchange_id=%s",
            (UUID(body["exchange_id"]),),
        )
    database_connection.rollback()
    assert (
        database_connection.execute("SELECT * FROM account.enrollment_exchange_receipt").fetchall()
        == before
    )


@pytest.mark.parametrize(
    "projection",
    [
        'original_request_evidence || \'{"request":{"invitation_secret":"synthetic"}}\'::jsonb',
        'original_request_evidence || \'{"encrypted_request":"synthetic ciphertext"}\'::jsonb',
        "jsonb_set(original_request_evidence,'{fields,invitation_secret}','\"synthetic\"'::jsonb)",
        (
            "jsonb_set(original_request_evidence,'{credential_hashes,invitation_secret_sh"
            "a256}','\"synthetic plaintext\"'::jsonb)"
        ),
    ],
)
def test_new_receipt_rejects_raw_nested_or_encrypted_request_aliases(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    projection: str,
) -> None:
    body, _, _, _ = profile.enroll()
    database_connection.commit()
    with pytest.raises(CheckViolation):
        database_connection.execute(
            "INSERT INTO account.enrollment_exchange_receipt(exchange_id,inv"
            "itation_id,request_sha256,"
            "device_key_thumbprint_sha256,device_id,session_id,binding_commit_id,receipt_expires_at,created_at,"
            "contract_version,platform,original_document_sha256,original_req"
            "uest_evidence,authority_generation) "
            "SELECT %s,invitation_id,request_sha256,device_key_thumbprint_sh"
            "a256,device_id,session_id,"
            "binding_commit_id,receipt_expires_at,created_at,contract_version,platform,original_document_sha256,"
            f"{projection},authority_generation FROM account.enrollment_exchange_receipt "
            "WHERE exchange_id=%s",
            (uuid4(), UUID(body["exchange_id"])),
        )
    database_connection.rollback()
    assert database_connection.execute(
        "SELECT count(*) FROM account.enrollment_exchange_receipt"
    ).fetchone() == (1,)
