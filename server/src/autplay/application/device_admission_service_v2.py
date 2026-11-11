"""D1 admission/trusted transactions sharing the M5 issuer, M6 review and quotas.

Incoming originals are verified on every call. Only the domain's explicit safe
evidence projection crosses into storage; no challenge/header/response bearer
or recoverable token copy is retained. Qualification remains UNVERIFIED.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.device_identifier_reservations import device_identifier_reserved
from autplay.adapters.postgresql.device_platform_evidence import has_v2_key_evidence_conflict
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import (
    DeviceAdmissionExchangeReceiptRow,
    DeviceAdmissionNonceRow,
    DeviceAdmissionRow,
    TrustedDeviceKeyRow,
    TrustedDeviceReenrollmentChallengeRow,
)
from autplay.adapters.postgresql.resource_limits import (
    lock_resource_admission,
    require_device_capacity,
)
from autplay.application.device_profile_service_v2 import (
    _account,
    _binding,
    _bounded_result,
    _hash,
    _principal,
    _require_identity,
    _require_key,
    _require_unused_refresh,
    _storage_evidence,
    _uuid,
)
from autplay.application.profile_pairing import (
    ProfilePairingService,
    _admission_created,
    _client_timestamp,
    _database_now,
    _hash_secret,
    _secret,
    _transaction_lock,
)
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationIdentity
from autplay.domain.device_profile_v2 import (
    DeviceProfileOperation as Op,
)
from autplay.domain.device_profile_v2 import (
    DeviceReplayEvidenceV2,
    ParsedDeviceProfileV2,
    parse_device_profile_v2,
    verify_device_profile_v2,
)
from autplay.domain.profile_pairing import ProfilePairingError, iso8601


class DeviceAdmissionServiceV2:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        identity: ProfilePairingService,
        enabled: Callable[[str], bool],
    ) -> None:
        self._sessions, self._identity, self._enabled = sessions, identity, enabled

    def admission_submit(self, raw: bytes, *, source: str | None = None) -> dict[str, object]:
        parsed = parse_device_profile_v2(Op.ADMISSION_SUBMIT, raw)
        evidence = _body_evidence(parsed)
        request_id = _uuid(parsed, "request_id")
        with self._sessions.begin() as s:
            identity = self._locked(s, parsed)
            _reserve(s, request_id, admission=request_id)
            _transaction_lock(s, b"admission-key", evidence.key.thumbprint_sha256)
            _platform(s, evidence)
            row = s.get(DeviceAdmissionRow, request_id, with_for_update=True)
            now = _database_now(s)
            if row is not None:
                _exact(row, evidence)
                if row.state != "PENDING" or row.expires_at <= now:
                    raise ProfilePairingError("admission_request_unavailable")
                raise ProfilePairingError("admission_recovery_required")
            if abs(now - _client_timestamp(parsed.field("requested_at"))) > timedelta(seconds=120):
                raise ProfilePairingError("admission_request_unavailable")
            if (
                s.scalar(
                    select(DeviceAdmissionRow.request_id)
                    .where(
                        DeviceAdmissionRow.device_key_thumbprint_sha256
                        == evidence.key.thumbprint_sha256,
                        DeviceAdmissionRow.original_request_evidence["fields"][
                            "client_nonce_b64url"
                        ].astext
                        == parsed.field("client_nonce_b64url"),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise ProfilePairingError("admission_request_unavailable")
            pending = s.scalar(
                select(DeviceAdmissionRow)
                .where(
                    DeviceAdmissionRow.device_key_thumbprint_sha256
                    == evidence.key.thumbprint_sha256,
                    DeviceAdmissionRow.state == "PENDING",
                )
                .with_for_update()
            )
            if pending is not None and pending.expires_at > now:
                raise ProfilePairingError("admission_request_unavailable")
            self._require_enabled("admission")
            # Use the SAME v1 HMAC source and key/day counters, under its locks.
            self._identity._admission_submit_rate_gate(
                s, evidence.key.thumbprint_sha256, source, now
            )
            now = _database_now(s)
            if abs(now - _client_timestamp(parsed.field("requested_at"))) > timedelta(seconds=120):
                raise ProfilePairingError("admission_request_unavailable")
            if pending is not None:
                pending.state, pending.decided_at = (
                    "EXPIRED",
                    pending.expires_at if pending.contract_version == "v2" else now,
                )
                s.flush()
            locator, bearer = _secret(), _secret()
            row = DeviceAdmissionRow(
                request_id=request_id,
                request_sha256=evidence.proof_digest,
                server_instance_id=identity.server_instance_id,
                identity_epoch=identity.identity_epoch,
                identity_thumbprint_sha256=identity.thumbprint,
                device_public_key_spki=evidence.key.spki,
                device_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
                nickname=cast(str, parsed.field("nickname")),
                device_model_hint=dict(evidence.fields).get("device_model_hint"),
                platform=evidence.platform.value,
                app_version=cast(str, parsed.field("app_version")),
                api_major=1,
                requested_at=_client_timestamp(parsed.field("requested_at")),
                state="PENDING",
                expires_at=now + timedelta(seconds=900),
                review_locator_hash=_hash_secret(locator),
                poll_bearer_hash=_hash_secret(bearer),
                secret_generation=1,
                recovery_count=0,
                created_at=now,
                contract_version="v2",
                original_document_sha256=evidence.original_document_sha256,
                original_request_evidence=_storage_evidence(evidence),
            )
            s.add(row)
            return _created(row, locator, bearer)

    def admission_poll(self, raw: bytes, request_id: str, poll_bearer: str) -> dict[str, object]:
        parsed = _path(Op.ADMISSION_POLL, raw, request_id)
        with self._sessions.begin() as s:
            identity = self._locked(s, parsed)
            owner = s.scalar(
                select(DeviceAdmissionRow.approved_user_id).where(
                    DeviceAdmissionRow.request_id == _uuid(parsed, "request_id"),
                )
            )
            account = _account(s, owner) if owner is not None else None
            row, evidence = self._admission(s, parsed, identity)
            now = _database_now(s)
            if row.poll_bearer_hash != _hash_secret(poll_bearer):
                raise ProfilePairingError("admission_request_unavailable")
            if row.state == "EXCHANGED":
                receipt = s.scalar(
                    select(DeviceAdmissionExchangeReceiptRow).where(
                        DeviceAdmissionExchangeReceiptRow.request_or_challenge_id == row.request_id,
                        DeviceAdmissionExchangeReceiptRow.contract_version == "v2",
                    )
                )
                if receipt is None or receipt.receipt_expires_at <= now:
                    raise ProfilePairingError("admission_request_unavailable")
            elif row.decided_at is not None and row.state != "APPROVED":
                if row.decided_at + timedelta(days=1) <= now:
                    raise ProfilePairingError("admission_request_unavailable")
            if row.state == "APPROVED":
                if account is None or row.approved_user_id != account.user_id:
                    raise ProfilePairingError("admission_request_unavailable")
                _require_key(s, account.user_id, evidence)
                if (
                    row.approved_authority_generation != account.authority_generation
                    or row.approved_account_role != account.role
                ):
                    raise ProfilePairingError("admission_request_unavailable")
            nonce = _nonce(s, row, "POLL", cast(str, parsed.field("client_nonce_b64url")))
            now = _database_now(s)
            if row.last_poll_at is not None and now - row.last_poll_at < timedelta(seconds=2):
                raise ProfilePairingError("admission_poll_rate_limited", retryable=True)
            row.last_poll_at = now
            s.add(nonce)
            if row.state == "PENDING" and row.expires_at <= now:
                row.state, row.decided_at = "EXPIRED", row.expires_at
            # Approval facts remain immutable after M6 decided; report expiry
            # without clearing the approved account or rewriting that decision.
            state = "EXPIRED" if row.state == "APPROVED" and row.expires_at <= now else row.state
            terminal_at = row.decided_at or row.expires_at
            if state == "EXPIRED" and terminal_at + timedelta(days=1) <= now:
                raise ProfilePairingError("admission_request_unavailable")
            if state not in {"PENDING", "APPROVED", "EXCHANGED", "REJECTED", "BLOCKED", "EXPIRED"}:
                raise ProfilePairingError("admission_request_unavailable")
            result: dict[str, object] = {
                "contract_version": "v2",
                "schema_version": 2,
                "platform": row.platform,
                "request_id": str(row.request_id),
                "state": state,
                "expires_at": iso8601(row.expires_at),
            }
            if state == "APPROVED" and account is not None:
                result.update(
                    approved_account_id=str(account.user_id),
                    approved_account_label=_label(account.display_name),
                )
            return _bounded_result(result)

    def admission_recover(self, raw: bytes, request_id: str) -> dict[str, object]:
        parsed = _path(Op.ADMISSION_RECOVER, raw, request_id)
        with self._sessions.begin() as s:
            identity = self._locked(s, parsed)
            row, _ = self._admission(s, parsed, identity)
            now = _database_now(s)
            if (
                row.state != "PENDING"
                or row.expires_at <= now
                or row.recovery_count >= 3
                or row.request_sha256 != _hash(parsed, "request_sha256")
                or (
                    row.last_recovery_at is not None
                    and now - row.last_recovery_at < timedelta(seconds=2)
                )
            ):
                raise ProfilePairingError("admission_request_unavailable")
            nonce = _nonce(s, row, "RECOVERY", cast(str, parsed.field("recovery_nonce_b64url")))
            self._require_enabled("admission")
            locator, bearer = _secret(), _secret()
            row.review_locator_hash, row.poll_bearer_hash = (
                _hash_secret(locator),
                _hash_secret(bearer),
            )
            row.review_binding_hash, row.review_web_session_id, row.last_poll_at = None, None, None
            row.recovery_count += 1
            row.secret_generation += 1
            row.last_recovery_at = now
            s.add(nonce)
            return _created(row, locator, bearer)

    def admission_exchange(
        self,
        raw: bytes,
        request_id: str,
        poll_bearer: str,
    ) -> tuple[dict[str, object], bool]:
        parsed = _path(Op.ADMISSION_EXCHANGE, raw, request_id)
        with self._sessions.begin() as s:
            identity = self._locked(s, parsed)
            # Discover immutable ownership after identity/admission, then lock
            # account before ceremony/device/session, as the existing issuer.
            owner = s.scalar(
                select(DeviceAdmissionRow.approved_user_id).where(
                    DeviceAdmissionRow.request_id == _uuid(parsed, "request_id"),
                )
            )
            if owner is None:
                raise ProfilePairingError("admission_request_unavailable")
            account = _account(s, owner)
            row, evidence = self._admission(s, parsed, identity)
            if (
                row.approved_user_id != account.user_id
                or _uuid(parsed, "approved_account_id") != account.user_id
                or row.approved_authority_generation != account.authority_generation
                or row.approved_account_role != account.role
                or row.request_sha256 != _hash(parsed, "request_sha256")
                or row.poll_bearer_hash != _hash_secret(poll_bearer)
                or row.poll_bearer_hash != _hash(parsed, "poll_bearer_sha256")
            ):
                raise ProfilePairingError("admission_request_unavailable")
            _require_key(s, account.user_id, evidence)
            receipt = self._receipt(s, parsed, evidence)
            trust = s.get(
                TrustedDeviceKeyRow,
                (account.user_id, evidence.key.thumbprint_sha256),
                with_for_update=True,
            )
            now = _database_now(s)
            if receipt is not None:
                if (
                    row.state != "EXCHANGED"
                    or row.enrolled_device_id != receipt.device_id
                    or row.enrolled_session_id != receipt.session_id
                ):
                    raise ProfilePairingError("admission_request_unavailable")
                if row.decision_action == "TRUST_DEVICE":
                    if receipt.trust_revision is None:
                        raise ProfilePairingError("trusted_key_unavailable")
                    _current_trust(trust, evidence, receipt.trust_revision)
                return self._replay(s, account, receipt, evidence, identity, now), True
            if row.state != "APPROVED" or row.expires_at <= now:
                raise ProfilePairingError("admission_request_unavailable")
            if row.decision_action not in {"APPROVE_ONCE", "TRUST_DEVICE"}:
                raise ProfilePairingError("admission_request_unavailable")
            _new_binding(s, parsed)
            require_device_capacity(s, account.user_id)
            self._require_enabled("admission")
            now = _database_now(s)
            if row.expires_at <= now:
                raise ProfilePairingError("admission_request_unavailable")
            if row.decision_action == "TRUST_DEVICE":
                if trust is None:
                    trust = TrustedDeviceKeyRow(
                        user_id=account.user_id,
                        device_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
                        device_public_key_spki=evidence.key.spki,
                        approved_request_id=row.request_id,
                        key_reference=uuid4(),
                        revision=1,
                        created_at=now,
                        removed_at=None,
                        contract_version="v2",
                        platform=evidence.platform.value,
                    )
                    s.add(trust)
                else:
                    if trust.device_public_key_spki != evidence.key.spki:
                        raise DeviceCompatibilityError("device_platform_conflict", 409)
                    trust.approved_request_id, trust.removed_at = row.request_id, None
                    trust.revision += 1
                    trust.contract_version, trust.platform = "v2", evidence.platform.value
            device, session, receipt = self._issue(
                s,
                parsed,
                evidence,
                account,
                row.request_id,
                row.approved_device_name or row.device_model_hint or row.nickname,
                row.app_version,
                trust.revision
                if row.decision_action == "TRUST_DEVICE" and trust is not None
                else None,
                now,
            )
            row.state, row.enrolled_device_id, row.enrolled_session_id = (
                "EXCHANGED",
                device.device_id,
                session.session_id,
            )
            return self._result(s, account, device, session, receipt, identity, now, False), False

    def trusted_challenge(self, raw: bytes) -> dict[str, object]:
        parsed = parse_device_profile_v2(Op.TRUSTED_CHALLENGE, raw)
        evidence = _body_evidence(parsed)
        challenge_id, account_id = (
            _uuid(parsed, "challenge_request_id"),
            _uuid(parsed, "account_id"),
        )
        with self._sessions.begin() as s:
            identity = self._locked(s, parsed)
            _reserve(s, challenge_id, challenge=challenge_id)
            _transaction_lock(s, b"trusted-challenge-request", challenge_id.bytes)
            account = _account(s, account_id)
            row = s.get(TrustedDeviceReenrollmentChallengeRow, challenge_id, with_for_update=True)
            if row is not None:
                _exact(row, evidence)
                if row.user_id != account_id or row.client_nonce_sha256 != _hash_secret(
                    cast(str, parsed.field("client_nonce_b64url"))
                ):
                    raise ProfilePairingError("operation_conflict")
                raise ProfilePairingError("trusted_key_unavailable")
            _transaction_lock(s, b"trusted-challenge-account", account_id.bytes)
            trust, _, _ = _trusted(s, account, evidence)
            now = _database_now(s)
            nonce = _hash_secret(cast(str, parsed.field("client_nonce_b64url")))
            if (
                s.scalar(
                    select(TrustedDeviceReenrollmentChallengeRow.challenge_id)
                    .where(
                        TrustedDeviceReenrollmentChallengeRow.user_id == account_id,
                        TrustedDeviceReenrollmentChallengeRow.device_key_thumbprint_sha256
                        == evidence.key.thumbprint_sha256,
                        TrustedDeviceReenrollmentChallengeRow.client_nonce_sha256 == nonce,
                    )
                    .limit(1)
                )
                is not None
            ):
                raise ProfilePairingError("trusted_key_unavailable")
            active = s.scalar(
                select(func.count())
                .select_from(TrustedDeviceReenrollmentChallengeRow)
                .where(
                    TrustedDeviceReenrollmentChallengeRow.user_id == account_id,
                    TrustedDeviceReenrollmentChallengeRow.device_key_thumbprint_sha256
                    == evidence.key.thumbprint_sha256,
                    TrustedDeviceReenrollmentChallengeRow.consumed_at.is_(None),
                    TrustedDeviceReenrollmentChallengeRow.expires_at > now,
                )
            )
            if int(active or 0) >= 3:
                raise ProfilePairingError("trusted_reenrollment_rate_limited", retryable=True)
            self._require_enabled("trusted")
            expiry = now.replace(second=0, microsecond=0) + timedelta(minutes=15)
            for key, scope in (
                (
                    hashlib.sha256(
                        b"trusted-key\x00" + account_id.bytes + evidence.key.thumbprint_sha256
                    ).digest(),
                    "TRUSTED_KEY_15M",
                ),
                (
                    hashlib.sha256(b"trusted-account\x00" + account_id.bytes).digest(),
                    "TRUSTED_ACCOUNT_15M",
                ),
            ):
                self._identity._advance_admission_rate_window(
                    s,
                    key,
                    scope,
                    now,
                    expiry,
                    5,
                    error_code="trusted_reenrollment_rate_limited",
                )
            now = _database_now(s)
            secret = _secret()
            row = TrustedDeviceReenrollmentChallengeRow(
                challenge_id=challenge_id,
                user_id=account_id,
                device_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
                request_sha256=evidence.proof_digest,
                client_nonce_sha256=nonce,
                challenge_hash=_hash_secret(secret),
                created_at=now,
                expires_at=now + timedelta(seconds=300),
                contract_version="v2",
                platform=evidence.platform.value,
                original_document_sha256=evidence.original_document_sha256,
                original_request_evidence=_storage_evidence(evidence),
                authority_generation=account.authority_generation,
                trust_revision=trust.revision,
                account_role=account.role,
            )
            s.add(row)
            return _bounded_result(
                {
                    "contract_version": "v2",
                    "schema_version": 2,
                    "platform": evidence.platform.value,
                    "challenge_id": str(challenge_id),
                    "challenge": secret,
                    "expires_at": iso8601(row.expires_at),
                    "device_key_thumbprint_sha256": evidence.key.thumbprint_sha256.hex(),
                    "expected_server_instance_id": str(identity.server_instance_id),
                    "expected_identity_epoch": identity.identity_epoch,
                    "expected_identity_thumbprint_sha256": identity.thumbprint.hex(),
                    "expected_api_origin": identity.api_origin,
                    "expected_stream_origin": identity.stream_origin,
                }
            )

    def trusted_exchange(self, raw: bytes) -> tuple[dict[str, object], bool]:
        parsed = parse_device_profile_v2(Op.TRUSTED_EXCHANGE, raw)
        evidence = _body_evidence(parsed)
        account_id, challenge_id = _uuid(parsed, "account_id"), _uuid(parsed, "challenge_id")
        with self._sessions.begin() as s:
            identity = self._locked(s, parsed)
            account = _account(s, account_id)
            challenge = s.get(
                TrustedDeviceReenrollmentChallengeRow, challenge_id, with_for_update=True
            )
            if challenge is None or challenge.contract_version != "v2":
                raise ProfilePairingError("trusted_key_unavailable")
            trust, name, app_version = _trusted(s, account, evidence)
            _stored_challenge(challenge, evidence, identity)
            if (
                challenge.user_id != account_id
                or challenge.authority_generation != account.authority_generation
                or challenge.account_role != account.role
                or challenge.trust_revision != trust.revision
                or challenge.challenge_hash != dict(evidence.credential_hashes)["challenge_sha256"]
            ):
                raise ProfilePairingError("trusted_key_unavailable")
            receipt = self._receipt(s, parsed, evidence)
            now = _database_now(s)
            if receipt is not None:
                if challenge.consumed_at is None:
                    raise ProfilePairingError("trusted_key_unavailable")
                _current_trust(trust, evidence, receipt.trust_revision)
                return self._replay(s, account, receipt, evidence, identity, now), True
            if challenge.consumed_at is not None or challenge.expires_at <= now:
                raise ProfilePairingError("trusted_key_unavailable")
            _new_binding(s, parsed)
            require_device_capacity(s, account_id)
            self._require_enabled("trusted")
            now = _database_now(s)
            if challenge.expires_at <= now:
                raise ProfilePairingError("trusted_key_unavailable")
            device, session, receipt = self._issue(
                s,
                parsed,
                evidence,
                account,
                challenge_id,
                name,
                app_version,
                trust.revision,
                now,
            )
            challenge.consumed_at = now
            return self._result(s, account, device, session, receipt, identity, now, False), False

    def _locked(self, s: Session, parsed: ParsedDeviceProfileV2) -> NegotiationIdentity:
        try:
            identity = self._identity.device_contract_identity(s, _now())
        except RuntimeError, ValueError:
            raise DeviceCompatibilityError() from None
        lock_resource_admission(s)
        _require_identity(parsed, identity)
        return identity

    def _require_enabled(self, family: str) -> None:
        if not self._enabled(family):
            raise DeviceCompatibilityError()

    def _admission(
        self,
        s: Session,
        parsed: ParsedDeviceProfileV2,
        identity: NegotiationIdentity,
    ) -> tuple[DeviceAdmissionRow, DeviceReplayEvidenceV2]:
        row = s.get(DeviceAdmissionRow, _uuid(parsed, "request_id"), with_for_update=True)
        if row is None or row.contract_version != "v2" or row.platform != parsed.platform.value:
            raise ProfilePairingError("admission_request_unavailable")
        require_admission_review_v2(s, row, identity)
        evidence = verify_device_profile_v2(parsed, row.device_public_key_spki).safe_evidence()
        _platform(s, evidence)
        return row, evidence

    def _receipt(
        self,
        s: Session,
        parsed: ParsedDeviceProfileV2,
        evidence: DeviceReplayEvidenceV2,
    ) -> DeviceAdmissionExchangeReceiptRow | None:
        exchange_id, binding_id = _uuid(parsed, "exchange_id"), _uuid(parsed, "binding_commit_id")
        if exchange_id == binding_id:
            raise ProfilePairingError("operation_conflict")
        for identifier in (exchange_id, binding_id):
            if device_identifier_reserved(
                s,
                identifier,
                own_profile_receipt=DeviceAdmissionExchangeReceiptRow,
                own_profile_operation_id=exchange_id,
            ):
                raise ProfilePairingError("operation_conflict")
        receipt = s.get(DeviceAdmissionExchangeReceiptRow, exchange_id, with_for_update=True)
        if receipt is not None:
            _exact(receipt, evidence)
            parent_field = (
                "request_id" if parsed.operation is Op.ADMISSION_EXCHANGE else "challenge_id"
            )
            if (
                receipt.request_or_challenge_id != _uuid(parsed, parent_field)
                or receipt.binding_commit_id != binding_id
            ):
                raise ProfilePairingError("operation_conflict")
        return receipt

    def _replay(
        self,
        s: Session,
        account: UserAccountRow,
        receipt: DeviceAdmissionExchangeReceiptRow,
        evidence: DeviceReplayEvidenceV2,
        identity: NegotiationIdentity,
        now: datetime,
    ) -> dict[str, object]:
        if (
            receipt.authority_generation != account.authority_generation
            or receipt.account_role != account.role
            or receipt.receipt_expires_at <= now
        ):
            raise ProfilePairingError("admission_request_unavailable")
        device, session = _binding(s, account, receipt.device_id, receipt.session_id, evidence, now)
        fields = dict(evidence.fields)
        if (
            session.generation != 0
            or session.family_id != session.session_id
            or session.refresh_token_hash.hex() != fields["next_refresh_token_sha256"]
            or session.expires_at != session.issued_at + timedelta(days=90)
        ):
            raise ProfilePairingError("session_revoked")
        return self._result(s, account, device, session, receipt, identity, now, True)

    def _issue(
        self,
        s: Session,
        parsed: ParsedDeviceProfileV2,
        evidence: DeviceReplayEvidenceV2,
        account: UserAccountRow,
        parent_id: UUID,
        name: str,
        app_version: str,
        trust_revision: int | None,
        now: datetime,
    ) -> tuple[DeviceRow, UserSessionRow, DeviceAdmissionExchangeReceiptRow]:
        device_id, session_id = uuid4(), uuid4()
        device = DeviceRow(
            device_id=device_id,
            user_id=account.user_id,
            device_name=name,
            app_version=app_version,
            platform=evidence.platform.value,
            public_key=evidence.key.spki,
            public_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
        )
        session = UserSessionRow(
            session_id=session_id,
            device_id=device_id,
            user_id=account.user_id,
            refresh_token_hash=_hash(parsed, "next_refresh_token_sha256"),
            issued_at=now,
            expires_at=now + timedelta(days=90),
            last_rotated_at=now,
            family_id=session_id,
            generation=0,
            session_mode="V2",
        )
        receipt = DeviceAdmissionExchangeReceiptRow(
            exchange_id=_uuid(parsed, "exchange_id"),
            binding_commit_id=_uuid(parsed, "binding_commit_id"),
            request_or_challenge_id=parent_id,
            request_sha256=evidence.proof_digest,
            device_key_thumbprint_sha256=evidence.key.thumbprint_sha256,
            device_id=device_id,
            session_id=session_id,
            created_at=now,
            receipt_expires_at=now + timedelta(days=30),
            contract_version="v2",
            platform=evidence.platform.value,
            original_document_sha256=evidence.original_document_sha256,
            original_request_evidence=_storage_evidence(evidence),
            authority_generation=account.authority_generation,
            trust_revision=trust_revision,
            account_role=account.role,
        )
        s.add_all((device, session))
        s.flush()
        s.add(receipt)
        return device, session, receipt

    def _result(
        self,
        s: Session,
        account: UserAccountRow,
        device: DeviceRow,
        session: UserSessionRow,
        receipt: DeviceAdmissionExchangeReceiptRow,
        identity: NegotiationIdentity,
        now: datetime,
        replayed: bool,
    ) -> dict[str, object]:
        now = _database_now(s)
        if (
            receipt.receipt_expires_at <= now
            or session.expires_at <= now
            or session.revoked_at is not None
        ):
            raise ProfilePairingError("session_revoked")
        expires = min(now + self._identity._access_ttl, session.expires_at)
        token = self._identity._access.issue(
            _principal(account, session), token_id=uuid4(), issued_at=now, expires_at=expires
        )
        if not 32 <= len(token) <= 8192:
            raise DeviceCompatibilityError()
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
                "access_token": token,
                "access_expires_at": iso8601(expires),
            }
        )


def require_admission_review_v2(
    s: Session, row: DeviceAdmissionRow, identity: NegotiationIdentity
) -> None:
    """Dispatch ONLY persisted v2 submissions in M6; projection is never re-signed.

    Admission creation verified the full original proof. Its immutable columns
    and closed evidence bind this exact row to the reviewed key/hash/platform.
    This check grants no actor/account authority; M6 supplies that separately.
    """
    evidence = row.original_request_evidence
    if (
        row.contract_version != "v2"
        or row.platform not in {"ANDROID", "MACOS"}
        or not isinstance(evidence, dict)
        or evidence.get("operation") != "admission.submit"
        or evidence.get("contract_version") != "v2"
        or evidence.get("schema_version") != 2
        or evidence.get("platform") != row.platform
        or row.original_document_sha256 is None
        or evidence.get("original_document_sha256") != row.original_document_sha256.hex()
        or evidence.get("proof_digest") != row.request_sha256.hex()
        or evidence.get("device_key_thumbprint_sha256") != row.device_key_thumbprint_sha256.hex()
        or evidence.get("device_public_key_spki_hex") != row.device_public_key_spki.hex()
        or evidence.get("domain") != "autplay:device-compatibility:admission:submit:v2\n"
        or evidence.get("signature_algorithm") != "ES256-P1363"
        or evidence.get("server_instance_id") != str(identity.server_instance_id)
        or evidence.get("identity_epoch") != identity.identity_epoch
        or evidence.get("identity_thumbprint_sha256") != identity.thumbprint.hex()
        or evidence.get("api_origin") != identity.api_origin
        or evidence.get("stream_origin") != identity.stream_origin
        or row.server_instance_id != identity.server_instance_id
        or row.identity_epoch != identity.identity_epoch
        or row.identity_thumbprint_sha256 != identity.thumbprint
    ):
        raise ProfilePairingError("admission_request_unavailable")
    fields = evidence.get("fields")
    if not isinstance(fields, dict) or any(
        fields.get(name) != value
        for name, value in (
            ("request_id", str(row.request_id)),
            ("nickname", row.nickname),
            ("app_version", row.app_version),
            ("api_major", row.api_major),
            ("device_model_hint", row.device_model_hint),
        )
    ):
        raise ProfilePairingError("admission_request_unavailable")


def _stored_challenge(
    row: TrustedDeviceReenrollmentChallengeRow,
    evidence: DeviceReplayEvidenceV2,
    identity: NegotiationIdentity,
) -> None:
    stored = row.original_request_evidence
    if (
        row.platform != evidence.platform.value
        or not isinstance(stored, dict)
        or stored.get("operation") != "trusted.challenge"
        or stored.get("platform") != row.platform
        or stored.get("device_public_key_spki_hex") != evidence.key.spki.hex()
        or stored.get("device_key_thumbprint_sha256") != evidence.key.thumbprint_sha256.hex()
        or stored.get("server_instance_id") != str(identity.server_instance_id)
        or stored.get("identity_epoch") != identity.identity_epoch
        or stored.get("identity_thumbprint_sha256") != identity.thumbprint.hex()
        or stored.get("api_origin") != identity.api_origin
        or stored.get("stream_origin") != identity.stream_origin
        or row.device_key_thumbprint_sha256 != evidence.key.thumbprint_sha256
    ):
        raise ProfilePairingError("trusted_key_unavailable")


def _trusted(
    s: Session, account: UserAccountRow, evidence: DeviceReplayEvidenceV2
) -> tuple[TrustedDeviceKeyRow, str, str]:
    _require_key(s, account.user_id, evidence)
    trust = s.get(
        TrustedDeviceKeyRow, (account.user_id, evidence.key.thumbprint_sha256), with_for_update=True
    )
    _current_trust(trust, evidence, None)
    assert trust is not None
    approved = s.get(DeviceAdmissionRow, trust.approved_request_id, with_for_update=True)
    if approved is not None and (
        approved.approved_user_id != account.user_id
        or approved.state != "EXCHANGED"
        or approved.decision_action != "TRUST_DEVICE"
        or approved.platform != evidence.platform.value
        or approved.device_public_key_spki != evidence.key.spki
        or approved.device_key_thumbprint_sha256 != evidence.key.thumbprint_sha256
    ):
        raise ProfilePairingError("trusted_key_unavailable")
    known = s.scalar(
        select(DeviceRow)
        .where(
            DeviceRow.user_id == account.user_id,
            or_(
                DeviceRow.public_key_thumbprint_sha256 == evidence.key.thumbprint_sha256,
                DeviceRow.public_key == evidence.key.spki,
            ),
        )
        .order_by(DeviceRow.created_at.desc(), DeviceRow.device_id)
        .with_for_update()
        .limit(1)
    )
    if approved is None and (
        known is None
        or known.platform != evidence.platform.value
        or known.public_key != evidence.key.spki
    ):
        raise ProfilePairingError("trusted_key_unavailable")
    if approved is not None:
        return trust, approved.approved_device_name or approved.nickname, approved.app_version
    assert known is not None
    return trust, known.device_name, known.app_version


def _current_trust(
    trust: TrustedDeviceKeyRow | None, evidence: DeviceReplayEvidenceV2, revision: int | None
) -> None:
    if (
        trust is None
        or trust.removed_at is not None
        or trust.device_public_key_spki != evidence.key.spki
        or trust.device_key_thumbprint_sha256 != evidence.key.thumbprint_sha256
        or trust.contract_version not in {"v1", "v2"}
        or (trust.contract_version == "v2" and trust.platform != evidence.platform.value)
        or (revision is not None and trust.revision != revision)
    ):
        raise ProfilePairingError("trusted_key_unavailable")


def _platform(s: Session, evidence: DeviceReplayEvidenceV2) -> None:
    if has_v2_key_evidence_conflict(
        s,
        evidence.key.thumbprint_sha256,
        public_key=evidence.key.spki,
        platform=evidence.platform.value,
    ):
        raise DeviceCompatibilityError("device_platform_conflict", 409)


def _body_evidence(parsed: ParsedDeviceProfileV2) -> DeviceReplayEvidenceV2:
    if parsed.body_spki is None:
        raise ProfilePairingError("admission_request_unavailable")
    return verify_device_profile_v2(parsed, parsed.body_spki).safe_evidence()


def _exact(
    row: DeviceAdmissionRow
    | DeviceAdmissionExchangeReceiptRow
    | TrustedDeviceReenrollmentChallengeRow,
    evidence: DeviceReplayEvidenceV2,
) -> None:
    if (
        row.contract_version != "v2"
        or row.platform != evidence.platform.value
        or row.request_sha256 != evidence.proof_digest
        or row.original_document_sha256 != evidence.original_document_sha256
        or row.original_request_evidence != _storage_evidence(evidence)
        or row.device_key_thumbprint_sha256 != evidence.key.thumbprint_sha256
    ):
        raise ProfilePairingError("operation_conflict")


def _reserve(
    s: Session, identifier: UUID, *, admission: UUID | None = None, challenge: UUID | None = None
) -> None:
    if device_identifier_reserved(
        s, identifier, own_admission_request_id=admission, own_trusted_challenge_id=challenge
    ):
        raise ProfilePairingError("operation_conflict")


def _new_binding(s: Session, parsed: ParsedDeviceProfileV2) -> None:
    _reserve(s, _uuid(parsed, "binding_commit_id"))
    _require_unused_refresh(s, _hash(parsed, "next_refresh_token_sha256"))


def _nonce(s: Session, row: DeviceAdmissionRow, scope: str, value: str) -> DeviceAdmissionNonceRow:
    digest = _hash_secret(value)
    if s.get(DeviceAdmissionNonceRow, (row.request_id, scope, digest)) is not None:
        raise ProfilePairingError("admission_request_unavailable")
    return DeviceAdmissionNonceRow(
        request_id=row.request_id, scope=scope, nonce_sha256=digest, used_at=_now()
    )


def _path(operation: Op, raw: bytes, request_id: str) -> ParsedDeviceProfileV2:
    parsed = parse_device_profile_v2(operation, raw)
    if parsed.field("request_id") != request_id:
        raise DeviceCompatibilityError("device_contract_invalid", 400)
    return parsed


def _created(row: DeviceAdmissionRow, locator: str, bearer: str) -> dict[str, object]:
    return _bounded_result(
        {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": row.platform,
            **_admission_created(row, locator, bearer),
        }
    )


def _label(value: str) -> str:
    return (
        "".join(
            character for character in value if not unicodedata.category(character).startswith("C")
        )[:120]
        or "Account"
    )


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = ("DeviceAdmissionServiceV2",)
