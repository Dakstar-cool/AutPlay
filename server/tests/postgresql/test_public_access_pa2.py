"""Real PostgreSQL evidence for PA2 account provisioning's corrected recovery boundary."""

from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models.profile_pairing import (
    ServerInstanceRow,
    TrustedDeviceKeyRow,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.public_access import PublicAccessError, PublicAccessService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import (
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
)
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _service(database_url: str) -> tuple[PublicAccessService, Engine]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return (
        PublicAccessService(
            sessionmaker(engine, class_=Session, expire_on_commit=False),
            Hs256AccessTokenCodec(
                b"public-access-test-secret-at-least-thirty-two-bytes",
                issuer="public-access",
                audience="public-access",
                max_ttl=timedelta(minutes=10),
            ),
            timedelta(minutes=10),
            b"public-access-source-hmac-secret-at-least-thirty-two-bytes",
        ),
        engine,
    )


def _owner_and_server(connection: Connection[Any]) -> tuple[Principal, UUID]:
    owner = connection.execute(
        "INSERT INTO account.user_account (display_name, role) "
        "VALUES ('owner', 'OWNER') RETURNING user_id"
    ).fetchone()
    assert owner is not None
    server_id = uuid4()
    server_spki = public_spki(ec.generate_private_key(ec.SECP256R1()))
    connection.execute(
        """INSERT INTO account.server_instance
        (server_instance_id,identity_epoch,identity_public_key_spki,identity_thumbprint_sha256,label_hint,api_origin,stream_origin,capability_revision,created_at,updated_at)
        VALUES (%s,1,%s,%s,'test','https://api.test.invalid','https://stream.test.invalid',1,now(),now())""",
        (server_id, server_spki, public_key_thumbprint(server_spki)),
    )
    connection.commit()
    return Principal(UUID(str(owner[0])), uuid4(), uuid4(), AccountRole.OWNER), server_id


def _request(invitation: dict[str, object], key: ec.EllipticCurvePrivateKey) -> dict[str, object]:
    spki = public_spki(key)
    request: dict[str, object] = {
        "contract_version": "v1",
        "schema_version": 1,
        "registration_id": str(uuid4()),
        "binding_commit_id": str(uuid4()),
        "invitation_id": invitation["invitation_id"],
        "invitation_secret": invitation["invitation_secret"],
        "expected_server_instance_id": invitation["server_instance_id"],
        "expected_identity_epoch": invitation["identity_epoch"],
        "expected_identity_thumbprint_sha256": invitation["identity_thumbprint_sha256"],
        "expected_api_origin": invitation["api_origin"],
        "expected_stream_origin": invitation["stream_origin"],
        "expected_account_display_name": invitation["account_display_name"],
        "device_name": "first Android",
        "platform": "ANDROID",
        "app_version": "pa2-test",
        "device_public_key_spki_b64": base64.b64encode(spki).decode(),
        "device_key_thumbprint_sha256": public_key_thumbprint(spki).hex(),
        "next_refresh_token_sha256": hashlib.sha256(b"locally-generated-refresh").hexdigest(),
        "client_nonce_b64url": "0123456789abcdefghijkl",
        "signature_algorithm": "ES256-P1363",
    }
    digest = canonical_sha256(request)
    request["request_sha256"] = digest.hex()
    request["device_signature_b64url"] = sign_p1363(
        key, "AutPlay account registration v1\n", digest
    )
    return request


def test_invitation_display_name_is_normalized_before_hash_and_persistence(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        operation_id = str(uuid4())
        body = {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": operation_id,
            "account_display_name": "\u2003 friend \u2003",
            "expires_in_seconds": 600,
        }

        invitation, replayed = service.create_invitation(owner, body)
        assert not replayed
        assert invitation["account_display_name"] == "friend"

        replay_body = dict(body)
        replay_body["account_display_name"] = "friend"
        replay, replayed = service.create_invitation(owner, replay_body)
        assert replayed
        assert replay["account_display_name"] == "friend"

        for invalid in ("   ", "friend\x00name", "friend\u202ename", "friend\u2066name"):
            invalid_body = dict(body)
            invalid_body["operation_id"] = str(uuid4())
            invalid_body["account_display_name"] = invalid
            with pytest.raises(PublicAccessError):
                service.create_invitation(owner, invalid_body)
    finally:
        engine.dispose()


def test_registration_creates_no_trusted_device_key(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, replayed = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "friend",
                "expires_in_seconds": 600,
            },
        )
        assert not replayed
        key = ec.generate_private_key(ec.SECP256R1())
        request = _request(invitation, key)
        response, replayed = service.redeem(request, "127.0.0.1")
        assert (
            not replayed
            and response["account_role"] == "USER"
            and response["refresh_generation"] == 0
        )
        # A lost response can be retried with a fresh ECDSA signature over the
        # same canonical request; it does not create another account.
        digest = bytes.fromhex(str(request["request_sha256"]))
        request["device_signature_b64url"] = sign_p1363(
            key, "AutPlay account registration v1\n", digest
        )
        replay, replayed = service.redeem(request, "127.0.0.1")
        assert replayed and replay["user_id"] == response["user_id"]
        invitation_page = service.list_invitations(owner, 10)
        invitation_items = cast(list[dict[str, object]], invitation_page["items"])
        assert invitation_items[0]["invited_user_id"] == response["user_id"]
        changed = dict(request)
        changed["device_name"] = "changed"
        changed_digest = canonical_sha256(
            changed, omit=frozenset({"request_sha256", "device_signature_b64url"})
        )
        changed["request_sha256"] = changed_digest.hex()
        changed["device_signature_b64url"] = sign_p1363(
            key, "AutPlay account registration v1\n", changed_digest
        )
        try:
            service.redeem(changed, "127.0.0.1")
        except PublicAccessError as error:
            assert error.code == "registration_conflict"
        else:
            raise AssertionError("changed registration replay must fail")
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TrustedDeviceKeyRow)) == 0
            assert session.execute(select(ServerInstanceRow)).first() is not None
    finally:
        engine.dispose()


def test_duplicate_active_owner_fails_closed_and_cancelled_invitation_cannot_redeem(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        database_connection.execute(
            "INSERT INTO account.user_account (display_name,role) VALUES ('duplicate','OWNER')"
        )
        database_connection.commit()
        command = {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "account_display_name": "friend",
            "expires_in_seconds": 600,
        }
        try:
            service.create_invitation(owner, command)
        except PublicAccessError as error:
            assert error.code == "unauthorized"
        else:
            raise AssertionError("duplicate bootstrap OWNER must fail closed")
        database_connection.execute(
            "UPDATE account.user_account SET status='DISABLED' WHERE display_name='duplicate'"
        )
        database_connection.commit()
        command["operation_id"] = str(uuid4())
        invitation, _ = service.create_invitation(owner, command)
        service.cancel_invitation(
            owner,
            UUID(str(invitation["invitation_id"])),
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "reason_code": "SECURITY",
            },
        )
        try:
            service.redeem(
                _request(invitation, ec.generate_private_key(ec.SECP256R1())), "cancelled"
            )
        except PublicAccessError:
            pass
        else:
            raise AssertionError("cancelled invitation must not redeem")
    finally:
        engine.dispose()


def test_concurrent_double_redemption_creates_one_binding_and_no_trust(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "friend",
                "expires_in_seconds": 600,
            },
        )
        barrier = Barrier(2)

        def attempt(_: int) -> bool:
            barrier.wait(timeout=5)
            try:
                service.redeem(
                    _request(invitation, ec.generate_private_key(ec.SECP256R1())), "same-source"
                )
                return True
            except PublicAccessError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        assert outcomes.count(True) == 1
        with Session(engine) as session:
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.user_account WHERE role='USER'")
                ).scalar_one()
                == 1
            )
            assert session.execute(text("SELECT count(*) FROM account.device")).scalar_one() == 1
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM account.user_session "
                        "WHERE session_mode='V2' AND generation=0"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.account_provisioning_link")
                ).scalar_one()
                == 1
            )
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.account_registration_receipt")
                ).scalar_one()
                == 1
            )
            assert session.scalar(select(func.count()).select_from(TrustedDeviceKeyRow)) == 0
    finally:
        engine.dispose()


def test_concurrent_exact_registration_id_has_one_create_and_one_replay(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "friend",
                "expires_in_seconds": 600,
            },
        )
        request = _request(invitation, ec.generate_private_key(ec.SECP256R1()))
        barrier = Barrier(2)

        def attempt(_: int) -> tuple[str, bool]:
            barrier.wait(timeout=5)
            response, replayed = service.redeem(dict(request), "exact-replay-source")
            return str(response["user_id"]), replayed

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        assert {user_id for user_id, _ in outcomes} == {outcomes[0][0]}
        assert sorted(replayed for _, replayed in outcomes) == [False, True]
        with Session(engine) as session:
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.account_registration_receipt")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_failed_authority_attempts_persist_all_three_redemption_rate_scopes(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "friend",
                "expires_in_seconds": 600,
            },
        )
        key = ec.generate_private_key(ec.SECP256R1())
        request = _request(invitation, key)
        request["expected_account_display_name"] = "wrong"
        digest = canonical_sha256(
            request, omit=frozenset({"request_sha256", "device_signature_b64url"})
        )
        request["request_sha256"] = digest.hex()
        request["device_signature_b64url"] = sign_p1363(
            key, "AutPlay account registration v1\n", digest
        )
        for _ in range(5):
            with suppress(PublicAccessError):
                service.redeem(request, "failing-authority")
        with Session(engine) as session:
            scopes = set(
                session.scalars(
                    text("SELECT scope FROM account.account_provisioning_rate_window")
                ).all()
            )
            assert {"REDEEM_INVITATION", "REDEEM_SOURCE", "REDEEM_SERVER"} <= scopes
    finally:
        engine.dispose()


def test_invalid_device_proof_persists_every_applicable_redemption_rate_scope(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "invalid-proof",
                "expires_in_seconds": 600,
            },
        )
        request = _request(invitation, ec.generate_private_key(ec.SECP256R1()))
        signature = str(request["device_signature_b64url"])
        request["device_signature_b64url"] = (
            signature[:10] + ("A" if signature[10] != "A" else "B") + signature[11:]
        )

        with pytest.raises(PublicAccessError):
            service.redeem(request, "invalid-proof-source")

        with Session(engine) as session:
            counts: dict[str, int] = {
                str(scope): int(attempt_count)
                for scope, attempt_count in session.execute(
                    text(
                        "SELECT scope,attempt_count FROM account.account_provisioning_rate_window "
                        "WHERE scope LIKE 'REDEEM_%'"
                    )
                ).all()
            }
            assert counts == {
                "REDEEM_INVITATION": 1,
                "REDEEM_SOURCE": 1,
                "REDEEM_SERVER": 1,
            }
    finally:
        engine.dispose()


def test_unavailable_canonical_source_uses_no_source_window(
    database_url: str, database_connection: Connection[Any]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "global-fallback",
                "expires_in_seconds": 600,
            },
        )
        request = _request(invitation, ec.generate_private_key(ec.SECP256R1()))
        request["expected_account_display_name"] = "wrong"
        digest = canonical_sha256(
            request, omit=frozenset({"request_sha256", "device_signature_b64url"})
        )
        request["request_sha256"] = digest.hex()
        # The stale signature is intentionally invalid; all non-source budgets still persist.
        with pytest.raises(PublicAccessError):
            service.redeem(request, None)

        with Session(engine) as session:
            scopes = set(
                session.scalars(
                    text("SELECT scope FROM account.account_provisioning_rate_window")
                ).all()
            )
            assert {"REDEEM_INVITATION", "REDEEM_SERVER"} <= scopes
            assert "REDEEM_SOURCE" not in scopes
    finally:
        engine.dispose()
