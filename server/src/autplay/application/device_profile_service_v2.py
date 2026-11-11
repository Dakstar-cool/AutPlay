"""D1 enrollment and rotation transactions over the existing M5 authority tables.

Only the strict domain boundary sees full incoming requests. Storage receives
verified.safe_evidence() projections; no credential-bearing request is copied.
Qualification/advertising remains a separate, empty source-owned registry.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.device_identifier_reservations import device_identifier_reserved
from autplay.adapters.postgresql.device_platform_evidence import has_v2_key_evidence_conflict
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import (
    DeviceKeyBlockRow,
    EnrollmentExchangeReceiptRow,
    EnrollmentInvitationRow,
    SessionRotationReceiptRow,
)
from autplay.adapters.postgresql.resource_limits import (
    lock_resource_admission,
    require_device_capacity,
)
from autplay.application.profile_pairing import ProfilePairingService, _database_now
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.canonical_json import canonical_json_bytes
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationIdentity
from autplay.domain.device_profile_v2 import (
    MAX_SAFE_INTEGER,
    DeviceProfileOperation,
    DeviceReplayEvidenceV2,
    ParsedDeviceProfileV2,
    parse_device_profile_v2,
    verify_device_profile_v2,
)
from autplay.domain.profile_pairing import ProfilePairingError, iso8601
from autplay.ports.auth import AccessTokenCodec

Receipt = EnrollmentExchangeReceiptRow | SessionRotationReceiptRow


class DeviceProfileServiceV2:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        identity: ProfilePairingService,
        enabled: Callable[[], bool],
        access_tokens: AccessTokenCodec,
        access_ttl: timedelta,
    ) -> None:
        self._sessions, self._identity, self._enabled = sessions, identity, enabled
        self._access, self._access_ttl = access_tokens, access_ttl

    def enrollment_exchange(self, raw: bytes) -> tuple[dict[str, object], bool]:
        parsed = parse_device_profile_v2(DeviceProfileOperation.ENROLLMENT_EXCHANGE, raw)
        exchange_id = _uuid(parsed, "exchange_id")
        invitation_id = _uuid(parsed, "invitation_id")
        account_id = self._identity._exchange_account_id(exchange_id, invitation_id)
        with self._sessions.begin() as s:
            identity = self._locked_identity(s)
            lock_resource_admission(s)
            _require_identity(parsed, identity)
            _require_operation_scope(s, exchange_id, EnrollmentExchangeReceiptRow)
            if account_id is None:
                raise ProfilePairingError("enrollment_invitation_unavailable")
            account = _account(s, account_id)
            invitation = s.get(EnrollmentInvitationRow, invitation_id, with_for_update=True)
            receipt = s.get(EnrollmentExchangeReceiptRow, exchange_id, with_for_update=True)
            # A new invitation explicitly authorizes its recipient's provided key.
            # Verification is separate from the account/invitation authority below.
            if parsed.body_spki is None:
                raise ProfilePairingError("enrollment_invitation_unavailable")
            verified = verify_device_profile_v2(parsed, parsed.body_spki)
            evidence = verified.safe_evidence()
            now = _database_now(s)
            if (
                invitation is None
                or invitation.user_id != account.user_id
                or _uuid(parsed, "expected_user_id") != account.user_id
                or invitation.server_instance_id != identity.server_instance_id
                or invitation.invitation_secret_hash
                != dict(evidence.credential_hashes)["invitation_secret_sha256"]
                or invitation.cancelled_at is not None
            ):
                raise ProfilePairingError("enrollment_invitation_unavailable")
            _require_key(s, account.user_id, evidence)
            if receipt is not None:
                _require_exact_receipt(receipt, evidence, account)
                if receipt.receipt_expires_at <= now:
                    raise ProfilePairingError("enrollment_invitation_unavailable")
                if (
                    invitation.consumed_at is None
                    or receipt.invitation_id != invitation_id
                    or receipt.binding_commit_id != _uuid(parsed, "binding_commit_id")
                ):
                    raise DeviceCompatibilityError("operation_conflict", 409)
                device, session = _binding(
                    s, account, receipt.device_id, receipt.session_id, evidence, now
                )
                if (
                    session.generation != 0
                    or session.family_id != session.session_id
                    or session.refresh_token_hash != _hash(parsed, "next_refresh_token_sha256")
                    or receipt.receipt_expires_at != session.expires_at + timedelta(seconds=300)
                ):
                    raise ProfilePairingError("session_revoked")
                return self._enrollment_result(
                    s, account, device, session, receipt, identity, now, True
                ), True
            if invitation.consumed_at is not None or invitation.expires_at <= now:
                raise ProfilePairingError("enrollment_invitation_unavailable")
            binding_id = _uuid(parsed, "binding_commit_id")
            _require_unused_binding(s, binding_id)
            refresh_hash = _hash(parsed, "next_refresh_token_sha256")
            _require_unused_refresh(s, refresh_hash)
            require_device_capacity(s, account.user_id)
            # Gate is deliberately after replay, under the same shared locks as
            # every v1 binding issuer, immediately before the first mutation.
            self._require_enabled()
            now = _database_now(s)
            if invitation.expires_at <= now:
                raise ProfilePairingError("enrollment_invitation_unavailable")
            device_id, session_id = uuid4(), uuid4()
            device = DeviceRow(
                device_id=device_id,
                user_id=account.user_id,
                device_name=cast(str, parsed.field("device_name")),
                platform=evidence.platform.value,
                app_version=cast(str, parsed.field("app_version")),
                public_key=evidence.key.spki,
                public_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            session = UserSessionRow(
                session_id=session_id,
                user_id=account.user_id,
                device_id=device_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=now + timedelta(days=90),
                last_rotated_at=now,
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
            receipt = EnrollmentExchangeReceiptRow(
                exchange_id=exchange_id,
                invitation_id=invitation_id,
                request_sha256=evidence.proof_digest,
                device_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
                device_id=device_id,
                session_id=session_id,
                binding_commit_id=binding_id,
                receipt_expires_at=session.expires_at + timedelta(seconds=300),
                created_at=now,
                contract_version="v2",
                platform=evidence.platform.value,
                original_document_sha256=evidence.original_document_sha256,
                original_request_evidence=_storage_evidence(evidence),
                authority_generation=account.authority_generation,
            )
            invitation.consumed_at = now
            s.add_all((device, session))
            s.flush()
            s.add(receipt)
            self._identity._audit(
                s,
                now,
                _principal(account, session),
                "profile.enrollment_exchanged",
                "DEVICE",
                device_id,
            )
            return self._enrollment_result(
                s, account, device, session, receipt, identity, now, False
            ), False

    def session_rotate(self, raw: bytes) -> tuple[dict[str, object], bool]:
        parsed = parse_device_profile_v2(DeviceProfileOperation.SESSION_ROTATE, raw)
        rotation_id = _uuid(parsed, "rotation_id")
        requested_parent = _uuid(parsed, "parent_session_id")
        # Read immutable ownership only; never replace the requested parent with
        # an ID found in a receipt. Reload every value under the writer locks.
        with self._sessions() as discovery:
            owner = discovery.execute(
                select(UserSessionRow.user_id, UserSessionRow.device_id).where(
                    UserSessionRow.session_id == requested_parent,
                )
            ).one_or_none()
        with self._sessions.begin() as s:
            identity = self._locked_identity(s)
            lock_resource_admission(s)
            _require_identity(parsed, identity)
            _require_operation_scope(s, rotation_id, SessionRotationReceiptRow)
            if owner is None:
                raise ProfilePairingError("session_revoked")
            account = _account(s, owner.user_id)
            receipt = s.get(SessionRotationReceiptRow, rotation_id, with_for_update=True)
            device = s.get(DeviceRow, owner.device_id, with_for_update=True)
            parent = s.get(UserSessionRow, requested_parent, with_for_update=True)
            if (
                device is None
                or parent is None
                or device.user_id != account.user_id
                or parent.user_id != account.user_id
                or parent.device_id != device.device_id
                or parent.session_mode != "V2"
                or parent.family_id is None
                or parent.generation is None
                or not 0 <= parent.generation <= MAX_SAFE_INTEGER
                or device.public_key is None
                or device.public_key_thumbprint_sha256 is None
            ):
                raise ProfilePairingError("session_revoked")
            evidence = verify_device_profile_v2(parsed, device.public_key).safe_evidence()
            now = _database_now(s)
            _require_key(s, account.user_id, evidence)
            if device.platform != evidence.platform.value:
                raise DeviceCompatibilityError("device_platform_conflict", 409)
            if device.public_key_thumbprint_sha256 != evidence.key.thumbprint_sha256:
                raise ProfilePairingError("session_revoked")
            if device.revoked_at is not None:
                raise ProfilePairingError("device_revoked")
            if parent.expires_at <= now:
                raise ProfilePairingError("session_revoked")
            # Proof AND current refresh possession are required even for replay.
            if (
                parent.refresh_token_hash
                != dict(evidence.credential_hashes)["current_refresh_token_sha256"]
            ):
                raise ProfilePairingError("session_revoked")
            if receipt is not None:
                if receipt.contract_version != "v2":
                    raise DeviceCompatibilityError("operation_conflict", 409)
                if receipt.receipt_expires_at <= now:
                    raise ProfilePairingError("session_revoked")
                if receipt.parent_session_id != parent.session_id:
                    raise DeviceCompatibilityError("operation_conflict", 409)
                if receipt.authority_generation != account.authority_generation:
                    raise ProfilePairingError("session_revoked")
                if not _receipt_matches(receipt, evidence, account):
                    self._changed_rotation(s, account, device, parent, now)
                _, successor = _binding(
                    s,
                    account,
                    device.device_id,
                    receipt.successor_session_id,
                    evidence,
                    now,
                )
                _require_successor(parent, successor, receipt)
                if successor.refresh_token_hash != _hash(parsed, "next_refresh_token_sha256"):
                    raise ProfilePairingError("session_revoked")
                return self._rotation_result(
                    s, account, device, successor, receipt, now, True
                ), True
            if parent.revoked_at is not None:
                # A valid signed refresh of a retired parent under a new ID is
                # changed replay too, never a second successor opportunity.
                self._changed_rotation(s, account, device, parent, now)
            if (
                _uuid(parsed, "device_id") != device.device_id
                or parsed.field("current_generation") != parent.generation
            ):
                self._changed_rotation(s, account, device, parent, now)
            if parent.generation >= MAX_SAFE_INTEGER:
                raise DeviceCompatibilityError("operation_conflict", 409)
            # Cross-version contract is independent from the session's V2 mode;
            # a legacy ANDROID M5 session can rotate, WEB/OTHER cannot guess it.
            refresh_hash = _hash(parsed, "next_refresh_token_sha256")
            _require_unused_refresh(s, refresh_hash)
            self._require_enabled()
            now = _database_now(s)
            if parent.expires_at <= now:
                raise ProfilePairingError("session_revoked")
            successor = UserSessionRow(
                session_id=uuid4(),
                user_id=account.user_id,
                device_id=device.device_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=parent.expires_at,
                last_rotated_at=now,
                family_id=parent.family_id,
                generation=parent.generation + 1,
                session_mode="V2",
            )
            receipt = SessionRotationReceiptRow(
                rotation_id=rotation_id,
                parent_session_id=parent.session_id,
                successor_session_id=successor.session_id,
                request_sha256=evidence.proof_digest,
                device_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
                receipt_expires_at=parent.expires_at + timedelta(seconds=300),
                created_at=now,
                contract_version="v2",
                platform=evidence.platform.value,
                original_document_sha256=evidence.original_document_sha256,
                original_request_evidence=_storage_evidence(evidence),
                authority_generation=account.authority_generation,
            )
            parent.revoked_at = now
            s.add(successor)
            s.flush()
            s.add(receipt)
            self._identity._audit(
                s,
                now,
                _principal(account, parent),
                "profile.session_rotated",
                "USER_SESSION",
                successor.session_id,
            )
            return self._rotation_result(s, account, device, successor, receipt, now, False), False

    def _locked_identity(self, s: Session) -> NegotiationIdentity:
        try:
            return self._identity.device_contract_identity(s, _now())
        except (RuntimeError, ValueError) as error:
            if isinstance(error, DeviceCompatibilityError):
                raise
        raise DeviceCompatibilityError()

    def _require_enabled(self) -> None:
        if not self._enabled():
            raise DeviceCompatibilityError()

    def _changed_rotation(
        self,
        s: Session,
        account: UserAccountRow,
        device: DeviceRow,
        parent: UserSessionRow,
        now: datetime,
    ) -> None:
        now = _database_now(s)
        if parent.expires_at <= now:
            raise ProfilePairingError("session_revoked")
        self._identity._revoke_device_sessions(s, account.user_id, device.device_id, now)
        device.revoked_at = now
        self._identity._audit(
            s,
            now,
            _principal(account, parent),
            "profile.rotation_replay_revoked",
            "DEVICE",
            device.device_id,
        )
        # Preserve the established changed-replay revocation on an error reply.
        s.commit()
        raise ProfilePairingError("session_revoked")

    def _access_result(
        self,
        account: UserAccountRow,
        session: UserSessionRow,
        now: datetime,
    ) -> dict[str, object]:
        if session.revoked_at is not None or session.expires_at <= now:
            raise ProfilePairingError("session_revoked")
        expires = min(now + self._access_ttl, session.expires_at)
        token = self._access.issue(
            _principal(account, session),
            token_id=uuid4(),
            issued_at=now,
            expires_at=expires,
        )
        if not 32 <= len(token) <= 8192:
            raise DeviceCompatibilityError()
        return {"access_token": token, "access_expires_at": iso8601(expires)}

    def _enrollment_result(
        self,
        s: Session,
        account: UserAccountRow,
        device: DeviceRow,
        session: UserSessionRow,
        receipt: EnrollmentExchangeReceiptRow,
        identity: NegotiationIdentity,
        now: datetime,
        replayed: bool,
    ) -> dict[str, object]:
        now = _database_now(s)
        if receipt.receipt_expires_at <= now:
            raise ProfilePairingError("enrollment_invitation_unavailable")
        return _bounded_result(
            {
                "contract_version": "v2",
                "schema_version": 2,
                "platform": device.platform,
                "exchange_id": str(receipt.exchange_id),
                "binding_commit_id": str(receipt.binding_commit_id),
                "server_instance_id": str(identity.server_instance_id),
                "user_id": str(account.user_id),
                "device_id": str(device.device_id),
                "session_id": str(session.session_id),
                "refresh_generation": 0,
                "refresh_absolute_expires_at": iso8601(session.expires_at),
                "receipt_expires_at": iso8601(receipt.receipt_expires_at),
                "replayed": replayed,
                **self._access_result(account, session, now),
            }
        )

    def _rotation_result(
        self,
        s: Session,
        account: UserAccountRow,
        device: DeviceRow,
        session: UserSessionRow,
        receipt: SessionRotationReceiptRow,
        now: datetime,
        replayed: bool,
    ) -> dict[str, object]:
        now = _database_now(s)
        if receipt.receipt_expires_at <= now:
            raise ProfilePairingError("session_revoked")
        return _bounded_result(
            {
                "contract_version": "v2",
                "schema_version": 2,
                "platform": device.platform,
                "rotation_id": str(receipt.rotation_id),
                "session_id": str(session.session_id),
                "parent_session_id": str(receipt.parent_session_id),
                "family_id": str(session.family_id),
                "generation": session.generation,
                "refresh_absolute_expires_at": iso8601(session.expires_at),
                "receipt_expires_at": iso8601(receipt.receipt_expires_at),
                "replayed": replayed,
                **self._access_result(account, session, now),
            }
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid(parsed: ParsedDeviceProfileV2, name: str) -> UUID:
    return UUID(cast(str, parsed.field(name)))


def _hash(parsed: ParsedDeviceProfileV2, name: str) -> bytes:
    return bytes.fromhex(cast(str, parsed.field(name)))


def _account(s: Session, user_id: UUID) -> UserAccountRow:
    account = s.get(UserAccountRow, user_id, with_for_update=True)
    if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
        raise ProfilePairingError("auth_attention_required")
    return account


def _require_identity(parsed: ParsedDeviceProfileV2, identity: NegotiationIdentity) -> None:
    identity.fields()  # M5 public evidence bounds; no second identity owner.
    trust = parsed.trust
    if (
        trust.server_instance_id != identity.server_instance_id
        or trust.identity_epoch != identity.identity_epoch
        or trust.identity_thumbprint_sha256 != identity.thumbprint
        or trust.api_origin != identity.api_origin
        or trust.stream_origin != identity.stream_origin
    ):
        raise DeviceCompatibilityError("server_identity_changed", 401)


def _require_key(s: Session, user_id: UUID, evidence: DeviceReplayEvidenceV2) -> None:
    if has_v2_key_evidence_conflict(
        s,
        evidence.key.thumbprint_sha256,
        public_key=evidence.key.spki,
        platform=evidence.platform.value,
    ):
        raise DeviceCompatibilityError("device_platform_conflict", 409)
    block = s.get(
        DeviceKeyBlockRow, (user_id, evidence.key.thumbprint_sha256), with_for_update=True
    )
    if block is not None and block.unblocked_at is None:
        raise ProfilePairingError("unauthorized")


def _storage_evidence(evidence: DeviceReplayEvidenceV2) -> dict[str, object]:
    """Only the verified domain allowlist crosses the persistence boundary."""
    return {
        "operation": evidence.operation.value,
        "contract_version": evidence.contract_version,
        "schema_version": evidence.schema_version,
        "platform": evidence.platform.value,
        "server_instance_id": str(evidence.trust.server_instance_id),
        "identity_epoch": evidence.trust.identity_epoch,
        "identity_thumbprint_sha256": evidence.trust.identity_thumbprint_sha256.hex(),
        "api_origin": evidence.trust.api_origin,
        "stream_origin": evidence.trust.stream_origin,
        "device_public_key_spki_hex": evidence.key.spki.hex(),
        "device_key_thumbprint_sha256": evidence.key.thumbprint_sha256.hex(),
        "matched_body_key": evidence.key.matched_body_key,
        "domain": evidence.domain.decode("ascii"),
        "signature_algorithm": evidence.signature_algorithm,
        "proof_digest": evidence.proof_digest.hex(),
        "original_document_sha256": evidence.original_document_sha256.hex(),
        "signature_hex": evidence.signature.hex(),
        "fields": dict(evidence.fields),
        "credential_hashes": {name: digest.hex() for name, digest in evidence.credential_hashes},
    }


def _receipt_matches(
    receipt: Receipt, evidence: DeviceReplayEvidenceV2, account: UserAccountRow
) -> bool:
    return (
        receipt.contract_version == "v2"
        and receipt.platform == evidence.platform.value
        and receipt.authority_generation == account.authority_generation
        and receipt.request_sha256 == evidence.proof_digest
        and receipt.device_key_thumbprint_sha256 == evidence.key.thumbprint_sha256
        and receipt.original_document_sha256 == evidence.original_document_sha256
        and receipt.original_request_evidence == _storage_evidence(evidence)
    )


def _require_exact_receipt(
    receipt: Receipt,
    evidence: DeviceReplayEvidenceV2,
    account: UserAccountRow,
) -> None:
    if not _receipt_matches(receipt, evidence, account):
        raise DeviceCompatibilityError("operation_conflict", 409)


def _binding(
    s: Session,
    account: UserAccountRow,
    device_id: UUID,
    session_id: UUID,
    evidence: DeviceReplayEvidenceV2,
    now: datetime,
) -> tuple[DeviceRow, UserSessionRow]:
    device = s.get(DeviceRow, device_id, with_for_update=True)
    session = s.get(UserSessionRow, session_id, with_for_update=True)
    now = _database_now(s)
    if device is None or device.user_id != account.user_id or device.revoked_at is not None:
        raise ProfilePairingError("device_revoked")
    if (
        device.platform != evidence.platform.value
        or device.public_key != evidence.key.spki
        or device.public_key_thumbprint_sha256 != evidence.key.thumbprint_sha256
    ):
        raise DeviceCompatibilityError("device_platform_conflict", 409)
    if (
        session is None
        or session.user_id != account.user_id
        or session.device_id != device_id
        or session.session_mode != "V2"
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise ProfilePairingError("session_revoked")
    return device, session


def _require_successor(
    parent: UserSessionRow,
    successor: UserSessionRow,
    receipt: SessionRotationReceiptRow,
) -> None:
    if (
        parent.revoked_at is None
        or parent.family_id != successor.family_id
        or parent.generation is None
        or parent.generation >= MAX_SAFE_INTEGER
        or successor.generation != parent.generation + 1
        or successor.expires_at != parent.expires_at
        or receipt.receipt_expires_at != parent.expires_at + timedelta(seconds=300)
    ):
        raise ProfilePairingError("session_revoked")


def _require_operation_scope(
    s: Session,
    operation_id: UUID,
    expected: type[EnrollmentExchangeReceiptRow] | type[SessionRotationReceiptRow],
) -> None:
    if device_identifier_reserved(
        s,
        operation_id,
        own_profile_receipt=expected,
        own_profile_operation_id=operation_id,
    ):
        raise DeviceCompatibilityError("operation_conflict", 409)
    if expected is EnrollmentExchangeReceiptRow:
        receipt = s.get(EnrollmentExchangeReceiptRow, operation_id)
        if receipt is not None and device_identifier_reserved(
            s,
            receipt.binding_commit_id,
            own_profile_receipt=expected,
            own_profile_operation_id=operation_id,
        ):
            raise DeviceCompatibilityError("operation_conflict", 409)


def _require_unused_binding(s: Session, binding_id: UUID) -> None:
    if device_identifier_reserved(s, binding_id):
        raise DeviceCompatibilityError("operation_conflict", 409)


def _require_unused_refresh(s: Session, digest: bytes) -> None:
    if (
        s.scalar(
            select(UserSessionRow.session_id).where(
                UserSessionRow.refresh_token_hash == digest,
            )
        )
        is not None
    ):
        raise DeviceCompatibilityError("operation_conflict", 409)


def _principal(account: UserAccountRow, session: UserSessionRow) -> Principal:
    return Principal(
        account.user_id, session.device_id, session.session_id, AccountRole(account.role)
    )


def _bounded_result(result: dict[str, object]) -> dict[str, object]:
    if len(canonical_json_bytes(result)) > 65536:
        raise DeviceCompatibilityError()
    return result


__all__ = ("DeviceProfileServiceV2",)
