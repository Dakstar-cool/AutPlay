"""Portable account identity survives a server copy and requires proofs for lifecycle changes."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.models import (
    DeviceRow,
    PortableAccountChallengeRow,
    PortableAccountRow,
    SelfPairingRateRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.portable_account import PortableAccountService
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import ProfilePairingError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


def passport(key: ec.EllipticCurvePrivateKey, account_id: UUID | None = None) -> dict[str, Any]:
    def b64(value: bytes) -> str:
        return base64.b64encode(value).decode()

    return {
        "format": "autplay-portable-account",
        "version": 1,
        "account_id": str(account_id or uuid4()),
        "public_id": "portable_test",
        "public_key_spki_b64": b64(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ),
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": 600000,
        "password_salt_b64": b64(b"a" * 16),
        "password_nonce_b64": b64(b"b" * 12),
        "password_ciphertext_b64": b64(b"c" * 100),
        "recovery_nonce_b64": b64(b"d" * 12),
        "recovery_ciphertext_b64": b64(b"e" * 100),
    }


def proof(key: ec.EllipticCurvePrivateKey, body: dict[str, Any]) -> str:
    purpose = body["challenge"]["purpose"].lower()
    payload = f"autplay:portable-account:{purpose}:v1\n".encode() + rfc8785.dumps(body)
    return base64.b64encode(key.sign(payload, ec.ECDSA(hashes.SHA256()))).decode()


def services(database_url: str) -> tuple[Any, PortableAccountService, ProfilePairingService]:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    codec = Hs256AccessTokenCodec(
        b"portable-test-access-secret-distinct-32bytes", issuer="test", audience="test"
    )
    ttl = timedelta(minutes=10)
    pairing = ProfilePairingService(
        sessions,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        label_hint="Portable test",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=codec,
        access_ttl=ttl,
        portable_accounts_enabled=True,
    )
    pairing.discovery()
    return (
        engine,
        PortableAccountService(sessions, codec, ttl, b"portable-source-secret-32bytes"),
        pairing,
    )


def binding_body(challenge: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> dict[str, Any]:
    return {
        **challenge,
        "device_public_key_spki_b64": base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).decode(),
        "device_name": "Test phone",
        "app_version": "test-1",
        "next_refresh_token_sha256": hashlib.sha256(uuid4().bytes).hexdigest(),
        "binding_commit_id": str(uuid4()),
    }


def test_register_login_exact_receipt_and_action_proof(database_url: str) -> None:
    engine, account, pairing = services(database_url)
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    document = passport(root)
    try:
        command = binding_body(account.challenge("REGISTER", "source", document=document), device)
        result = account.complete(command, proof(root, command))
        assert result["account_id"] == document["account_id"]
        repeated = account.complete(command, proof(root, command))
        assert repeated == result
        changed = {**command, "binding_commit_id": str(uuid4())}
        with pytest.raises(ProfilePairingError, match="operation_conflict"):
            account.complete(changed, proof(root, changed))
        login = binding_body(
            account.challenge("LOGIN", "source", login="portable_test"),
            ec.generate_private_key(ec.SECP256R1()),
        )
        second = account.complete(login, proof(root, login))
        assert second["account_id"] == result["account_id"]
        assert second["device_id"] != result["device_id"]
        actor = Principal(
            UUID(str(result["account_id"])),
            UUID(str(result["device_id"])),
            UUID(str(result["session_id"])),
            AccountRole.USER,
        )
        operation = uuid4()
        with pytest.raises(ProfilePairingError, match="account_reauthentication_required"):
            pairing.logout_current(actor, operation)
        authorization = account.challenge(
            "REAUTH", "source", actor=actor, action="LOGOUT_CURRENT", operation_id=operation
        )
        pairing.logout_current(
            actor,
            operation,
            portable_authorization={
                "request": authorization,
                "proof": proof(root, authorization),
            },
        )
        with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
            account.complete(command, proof(root, command))
    finally:
        engine.dispose()


def test_claim_preserves_legacy_identity_and_rejects_second_root(database_url: str) -> None:
    engine, account, _ = services(database_url)
    root = ec.generate_private_key(ec.SECP256R1())
    document = passport(root)
    user, device, session_id = UUID(document["account_id"]), uuid4(), uuid4()
    now = datetime.now(UTC)
    try:
        with account.sessions.begin() as session:
            session.add(
                UserAccountRow(user_id=user, display_name="Legacy", role="USER", status="ACTIVE")
            )
            session.flush()
            session.add(
                DeviceRow(
                    user_id=user,
                    device_id=device,
                    device_name="Legacy phone",
                    platform="ANDROID",
                    app_version="test",
                )
            )
            session.flush()
            session.add(
                UserSessionRow(
                    user_id=user,
                    device_id=device,
                    session_id=session_id,
                    refresh_token_hash=uuid4().bytes + uuid4().bytes,
                    issued_at=now,
                    expires_at=now + timedelta(days=1),
                    last_rotated_at=now,
                )
            )
        actor = Principal(user, device, session_id, AccountRole.USER)
        command = account.challenge("CLAIM", "source", document=document, actor=actor)
        assert account.complete(command, proof(root, command), actor=actor)["claimed"] is True
        with account.sessions() as session:
            assert len(session.scalars(select(UserAccountRow)).all()) == 1
        assert account.document(actor) == document
        with pytest.raises(ProfilePairingError, match="account_identity_conflict"):
            account.challenge(
                "CLAIM",
                "source",
                document=passport(ec.generate_private_key(ec.SECP256R1()), user),
                actor=actor,
            )
    finally:
        engine.dispose()


def test_rotation_preserves_uuid_revokes_authority_and_recovers_exact_receipt(
    database_url: str,
) -> None:
    engine, account, _ = services(database_url)
    old_key, new_key = (
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP256R1()),
    )
    original = passport(old_key)
    replacement = passport(new_key, UUID(original["account_id"]))
    try:
        enroll = binding_body(account.challenge("REGISTER", "source", document=original), old_key)
        bound = account.complete(enroll, proof(old_key, enroll))
        stale_login = binding_body(
            account.challenge("LOGIN", "source", login="portable_test"), old_key
        )
        rotation: dict[str, Any] = {
            **account.challenge("ROTATE", "source", document=original),
            "replacement_account": replacement,
        }
        competing_rotation: dict[str, Any] = {
            **account.challenge("ROTATE", "source", document=original),
            "replacement_account": passport(
                ec.generate_private_key(ec.SECP256R1()), UUID(original["account_id"])
            ),
        }
        with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
            account.complete(
                rotation, proof(old_key, rotation), replacement_proof=proof(old_key, rotation)
            )
        result = account.complete(
            rotation, proof(old_key, rotation), replacement_proof=proof(new_key, rotation)
        )
        assert result == {
            "account_id": original["account_id"],
            "rotated": True,
            "account": replacement,
            "revision": 2,
        }
        assert "access_token" not in result
        with account.sessions.begin() as session:
            assert len(session.scalars(select(UserAccountRow)).all()) == 1
            credential = session.get(UserSessionRow, UUID(str(bound["session_id"])))
            device = session.get(DeviceRow, UUID(str(bound["device_id"])))
            identity = session.get(PortableAccountRow, UUID(original["account_id"]))
            assert credential is not None and credential.revoked_at is not None
            assert device is not None and device.revoked_at is not None
            assert identity is not None and identity.document == replacement
            row = session.get(
                PortableAccountChallengeRow, UUID(rotation["challenge"]["challenge_id"])
            )
            assert row is not None
            row.receipt_expires_at = datetime.now(UTC) - timedelta(days=1)
        # Rotation is not an access grant: an exact retry still repairs a crash a day later.
        assert (
            account.complete(
                rotation, proof(old_key, rotation), replacement_proof=proof(new_key, rotation)
            )
            == result
        )
        for command in (enroll, stale_login, competing_rotation):
            with pytest.raises(ProfilePairingError):
                account.complete(command, proof(old_key, command))
        with pytest.raises(ProfilePairingError, match="account_identity_conflict"):
            account.challenge("ROTATE", "source", document=original)
        current = binding_body(account.challenge("LOGIN", "source", login="portable_test"), new_key)
        with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
            account.complete(current, proof(old_key, current))
        assert (
            account.complete(current, proof(new_key, current))["account_id"]
            == original["account_id"]
        )
    finally:
        engine.dispose()


def test_expired_pending_retires_refresh_before_terminal_outcome(database_url: str) -> None:
    engine, account, _ = services(database_url)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        command = binding_body(account.challenge("REGISTER", "source", document=passport(key)), key)
        bound = account.complete(command, proof(key, command))
        with account.sessions.begin() as session:
            row = session.get(
                PortableAccountChallengeRow, UUID(command["challenge"]["challenge_id"])
            )
            assert row is not None
            row.receipt_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(ProfilePairingError, match="account_receipt_expired"):
            account.complete(command, proof(key, command))
        with account.sessions() as session:
            credential = session.get(UserSessionRow, UUID(str(bound["session_id"])))
            assert credential is not None and credential.revoked_at is not None
        pending = binding_body(account.challenge("LOGIN", "source", login="portable_test"), key)
        with account.sessions.begin() as session:
            row = session.get(
                PortableAccountChallengeRow, UUID(pending["challenge"]["challenge_id"])
            )
            assert row is not None
            row.created_at = datetime.now(UTC) - timedelta(minutes=3)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(ProfilePairingError, match="account_challenge_expired"):
            account.complete(pending, proof(key, pending))
    finally:
        engine.dispose()


def test_failed_lookup_and_bad_proof_persist_source_limit(database_url: str) -> None:
    engine, account, _ = services(database_url)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        for _ in range(2):
            with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
                account.challenge("LOGIN", "missing-source", login="missing_account")
        with account.sessions.begin() as session:
            rate = session.scalar(select(SelfPairingRateRow))
            assert rate is not None
            assert rate.count == 2
            rate.count = 60
        with pytest.raises(ProfilePairingError, match="rate_limited"):
            account.challenge("LOGIN", "missing-source", login="missing_account")
        command = binding_body(
            account.challenge("REGISTER", "proof-source", document=passport(key)), key
        )
        for _ in range(2):
            with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
                account.complete(
                    command,
                    proof(ec.generate_private_key(ec.SECP256R1()), command),
                    source="proof-source",
                )
        with account.sessions.begin() as session:
            rate = session.scalar(select(SelfPairingRateRow).where(SelfPairingRateRow.count != 60))
            assert rate is not None
            assert rate.count == 3
            rate.count = 60
        with pytest.raises(ProfilePairingError, match="rate_limited"):
            account.complete(command, proof(key, command), source="proof-source")
    finally:
        engine.dispose()
