"""WebAuthn ceremonies issue existing M6 sessions through one atomic unit of work."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from autplay.domain.web_admin import WebActor, WebAdminError, WebSessionCredentials
from autplay.domain.web_passkeys import PasskeyCeremony, PasskeyMetadata
from autplay.ports.web_passkey_verifier import WebPasskeyVerifier
from autplay.ports.web_passkeys import WebPasskeyUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class PasskeyOptions:
    ceremony_id: UUID
    operation_id: UUID
    options_json: str


class WebPasskeyService:
    def __init__(
        self,
        units: WebPasskeyUnitOfWorkFactory,
        verifier: WebPasskeyVerifier,
        csrf_secret: bytes,
    ) -> None:
        if len(csrf_secret) < 16:
            raise ValueError("CSRF secret must contain at least 16 bytes")
        self._units = units
        self._verifier = verifier
        self._csrf_secret = csrf_secret

    def begin_registration(
        self,
        actor: WebActor,
        operation_id: UUID,
        *,
        now: datetime | None = None,
    ) -> PasskeyOptions:
        now = now or datetime.now(UTC)
        with self._units() as unit:
            unit.passkeys.lock_admin(actor, now)
            existing = unit.passkeys.credentials(actor.user_id)
            if len(existing) >= 8:
                raise WebAdminError("passkey_limit")
            handle = existing[0].user_handle if existing else secrets.token_bytes(32)
            ceremony = PasskeyCeremony(
                uuid4(),
                operation_id,
                "REGISTER",
                secrets.token_bytes(32),
                _actor_binding(actor),
                self._verifier.origin,
                self._verifier.rp_id,
                now + timedelta(minutes=5),
                handle,
                actor.user_id,
                actor.web_session_id,
                actor.token_generation,
            )
            ceremony = unit.passkeys.add_ceremony(ceremony, now)
            unit.commit()
        if ceremony.user_handle is None:
            raise WebAdminError("passkey_invalid")
        return PasskeyOptions(
            ceremony.ceremony_id,
            operation_id,
            self._verifier.registration_options(
                ceremony.challenge,
                ceremony.user_handle,
                tuple(value.credential_id for value in existing),
            ),
        )

    def finish_registration(
        self,
        actor: WebActor,
        ceremony_id: UUID,
        operation_id: UUID,
        document: str,
        label: str,
        *,
        now: datetime | None = None,
    ) -> UUID:
        now = now or datetime.now(UTC)
        if not 1 <= len(label.strip()) <= 80 or any(ord(char) < 32 for char in label):
            raise WebAdminError("passkey_label_invalid")
        request_hash = _digest(document.encode() + b"\0" + label.encode())
        with self._units() as unit:
            unit.passkeys.lock_admin(actor, now)
            ceremony = unit.passkeys.ceremony(ceremony_id)
            self._validate(ceremony, "REGISTER", operation_id, _actor_binding(actor), now)
            if ceremony.completed_request_sha256 is not None:
                if ceremony.completed_request_sha256 != request_hash or ceremony.result_id is None:
                    raise WebAdminError("operation_conflict")
                return ceremony.result_id
            verified = self._verifier.verify_registration(document, ceremony.challenge)
            result = unit.passkeys.register(actor, ceremony, verified, label.strip(), now)
            unit.passkeys.consume(ceremony_id, request_hash, result, now)
            unit.commit()
        return result

    def begin_login(
        self,
        preauth: bytes,
        nonce: bytes,
        *,
        now: datetime | None = None,
    ) -> PasskeyOptions:
        now = now or datetime.now(UTC)
        binding = _preauth_binding(preauth, nonce)
        ceremony = PasskeyCeremony(
            uuid4(),
            uuid4(),
            "LOGIN",
            secrets.token_bytes(32),
            binding,
            self._verifier.origin,
            self._verifier.rp_id,
            now + timedelta(minutes=5),
        )
        with self._units() as unit:
            unit.passkeys.add_ceremony(ceremony, now)
            unit.commit()
        return PasskeyOptions(
            ceremony.ceremony_id,
            ceremony.operation_id,
            self._verifier.authentication_options(ceremony.challenge),
        )

    def finish_login(
        self,
        ceremony_id: UUID,
        operation_id: UUID,
        preauth: bytes,
        nonce: bytes,
        document: str,
        *,
        now: datetime | None = None,
    ) -> WebSessionCredentials:
        now = now or datetime.now(UTC)
        binding = _preauth_binding(preauth, nonce)
        credential_id = self._verifier.credential_id(document)
        bearer = secrets.token_urlsafe(32).encode("ascii")
        csrf = hmac.digest(
            self._csrf_secret,
            b"AutPlay M6 CSRF v1\n" + bearer + (0).to_bytes(8, "big"),
            "sha256",
        )
        with self._units() as unit:
            discovered = unit.passkeys.find_credential(credential_id, lock=False)
            role = unit.passkeys.lock_account(discovered.server_instance_id, discovered.user_id)
            passkey = unit.passkeys.find_credential(credential_id, lock=True)
            ceremony = unit.passkeys.ceremony(ceremony_id)
            self._validate(ceremony, "LOGIN", operation_id, binding, now)
            if ceremony.completed_request_sha256 is not None:
                if ceremony.completed_request_sha256 == _digest(document.encode()):
                    raise WebAdminError("browser_login_outcome_unknown")
                raise WebAdminError("passkey_invalid")
            assertion = self._verifier.verify_authentication(
                document,
                ceremony.challenge,
                credential_id=credential_id,
                public_key=passkey.public_key,
                user_handle=passkey.user_handle,
                sign_count=passkey.sign_count,
            )
            if assertion.backup_eligible != passkey.backup_eligible:
                raise WebAdminError("passkey_invalid")
            unit.passkeys.update_counter(passkey, assertion, now)
            session_id = unit.passkeys.issue_session(
                passkey,
                operation_id,
                _digest(bearer),
                _digest(csrf),
                now,
            )
            unit.passkeys.consume(ceremony_id, _digest(document.encode()), session_id, now)
            unit.commit()
        return WebSessionCredentials(
            WebActor(passkey.server_instance_id, passkey.user_id, session_id, role, 0),
            now + timedelta(hours=12),
            bearer,
            csrf,
        )

    def list_passkeys(self, actor: WebActor) -> tuple[PasskeyMetadata, ...]:
        with self._units() as unit:
            unit.passkeys.lock_admin(actor, datetime.now(UTC))
            return unit.passkeys.list_metadata(actor.user_id)

    def list_local(self, user_id: UUID) -> tuple[PasskeyMetadata, ...]:
        with self._units() as unit:
            return unit.passkeys.list_metadata(user_id)

    def revoke(
        self,
        actor: WebActor,
        passkey_id: UUID,
        operation_id: UUID,
        request_hash: bytes,
    ) -> None:
        if len(request_hash) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        now = datetime.now(UTC)
        with self._units() as unit:
            unit.passkeys.lock_admin(actor, now)
            unit.passkeys.revoke(
                actor.user_id,
                passkey_id,
                operation_id,
                _digest(_actor_binding(actor) + request_hash),
                now,
                actor,
                request_hash,
            )
            unit.commit()

    def revoke_local(self, user_id: UUID, passkey_id: UUID, operation_id: UUID) -> None:
        with self._units() as unit:
            unit.passkeys.revoke(
                user_id,
                passkey_id,
                operation_id,
                _digest(b"LOCAL" + user_id.bytes),
                datetime.now(UTC),
            )
            unit.commit()

    def cleanup(self, limit: int = 1000) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("cleanup bound")
        with self._units() as unit:
            result = unit.passkeys.cleanup(datetime.now(UTC), limit)
            unit.commit()
            return result

    def _validate(
        self,
        ceremony: PasskeyCeremony,
        purpose: str,
        operation_id: UUID,
        binding: bytes,
        now: datetime,
    ) -> None:
        if (
            ceremony.purpose != purpose
            or ceremony.operation_id != operation_id
            or not hmac.compare_digest(ceremony.binding_sha256, binding)
            or ceremony.expires_at <= now
            or ceremony.origin != self._verifier.origin
            or ceremony.rp_id != self._verifier.rp_id
        ):
            raise WebAdminError("passkey_invalid")


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _actor_binding(actor: WebActor) -> bytes:
    return _digest(
        actor.server_instance_id.bytes
        + actor.user_id.bytes
        + actor.web_session_id.bytes
        + actor.token_generation.to_bytes(8, "big"),
    )


def _preauth_binding(preauth: bytes, nonce: bytes) -> bytes:
    if not 32 <= len(preauth) <= 128 or not 32 <= len(nonce) <= 128:
        raise WebAdminError("passkey_invalid")
    return _digest(_digest(preauth) + _digest(nonce))
