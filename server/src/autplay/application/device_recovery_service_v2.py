"""D1 server-bound recovery; original proofs are history, headers are transient.

SOURCE ONLY / UNVERIFIED. All blocking authority reads and secret verification
precede the final database clock. Only pure checks run between that clock and
the first effect. Existing v1 code verifier, rate budgets and access semantics
are shared; an outcome never rotates a code or creates a session.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import rfc8785
from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.account_recovery import revoke_account_authority
from autplay.adapters.postgresql.device_identifier_reservations import device_identifier_reserved
from autplay.adapters.postgresql.device_platform_evidence import has_v2_key_evidence_conflict
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryOperationRow
from autplay.adapters.postgresql.models.profile_pairing import DeviceKeyBlockRow, ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import (
    effective_account_limits,
    lock_resource_admission,
)
from autplay.application.account_recovery import AccountRecoveryService, database_now
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.account_recovery import AccountRecoveryError
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_recovery_v2 import (
    DeviceKeyEvidence,
    DeviceTrustEvidence,
    KeyEvidenceSource,
    ParsedRecoveryRequest,
    parse_recovery_request,
    verify_recovery_proof,
)
from autplay.domain.device_recovery_v2 import (
    RecoveryOperation as Op,
)
from autplay.domain.profile_pairing import iso8601


@dataclass(frozen=True, slots=True, repr=False)
class RecoveryAuthoritySnapshot:
    """Immutable locked facts. require_current is pure and performs no DB read."""

    account_status: str
    deleted_at: datetime | None
    authority_generation: int
    expected_authority_generation: int
    code_generation: int
    expected_code_generation: int
    verifier: bytes
    expected_verifier: bytes
    binding_expires_at: datetime | None = None
    receipt_expires_at: datetime | None = None

    def require_current(self, now: datetime) -> None:
        if (
            self.account_status != "ACTIVE"
            or self.deleted_at is not None
            or self.authority_generation != self.expected_authority_generation
            or self.code_generation != self.expected_code_generation
            or not hmac.compare_digest(self.verifier, self.expected_verifier)
            or (self.binding_expires_at is not None and self.binding_expires_at <= now)
            or (self.receipt_expires_at is not None and self.receipt_expires_at <= now)
        ):
            raise AccountRecoveryError()


class DeviceRecoveryServiceV2:
    def __init__(
        self,
        recovery: AccountRecoveryService,
        *,
        identity: ProfilePairingService,
        enabled: Callable[[], bool],
    ) -> None:
        self._recovery, self._identity, self._enabled = recovery, identity, enabled

    def rate_gate(self, source: str) -> None:
        self._recovery.rate_gate(source)

    def _context(
        self,
        session: Session,
        parsed: ParsedRecoveryRequest,
    ) -> tuple[ServerInstanceRow, UserAccountRow]:
        try:
            current = self._identity.current_device_contract_identity(session)
        except RuntimeError, ValueError:
            # Expected configured-owner mismatch is safe/unavailable, never raw
            # key/origin evidence or an uncaught callback RuntimeError at HTTP.
            raise DeviceCompatibilityError() from None
        trust = DeviceTrustEvidence(
            str(current.server_instance_id),
            current.identity_epoch,
            current.thumbprint.hex(),
            current.api_origin,
            current.stream_origin,
        )
        if parsed.trust != trust:
            raise AccountRecoveryError()
        identity = session.get(ServerInstanceRow, current.server_instance_id)
        if identity is None:
            raise DeviceCompatibilityError()
        user_id = UUID(str(parsed.document["account_id"]))
        lock_resource_admission(session)
        _acquire_sync_owner_publish_lock(session, user_id)
        account = session.get(UserAccountRow, user_id, with_for_update=True, populate_existing=True)
        if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
            raise AccountRecoveryError()
        return identity, account

    @staticmethod
    def _key(session: Session, parsed: ParsedRecoveryRequest, account: UserAccountRow) -> None:
        thumb = bytes.fromhex(str(parsed.document["device_key_thumbprint_sha256"]))
        if has_v2_key_evidence_conflict(
            session, thumb, public_key=parsed.public_key_spki, platform=parsed.platform.value
        ):
            raise DeviceCompatibilityError("device_platform_conflict", 409)
        block = session.get(
            DeviceKeyBlockRow,
            (account.user_id, thumb),
            with_for_update=True,
            populate_existing=True,
        )
        if block is not None and block.unblocked_at is None:
            raise AccountRecoveryError()

    @staticmethod
    def _reserve(session: Session, parsed: ParsedRecoveryRequest, *, existing: bool) -> None:
        doc = parsed.document
        operation_id = UUID(str(doc["operation_id"]))
        identifiers = [operation_id]
        if parsed.operation is not Op.PREVIEW:
            identifiers.append(UUID(str(doc["binding_commit_id"])))
        if len(set(identifiers)) != len(identifiers) or any(
            device_identifier_reserved(
                session,
                identifier,
                own_recovery_operation_id=operation_id if existing else None,
            )
            for identifier in identifiers
        ):
            raise AccountRecoveryError("operation_conflict")

    @staticmethod
    def _proof(
        parsed: ParsedRecoveryRequest, original: ParsedRecoveryRequest | None = None
    ) -> None:
        verify_recovery_proof(
            parsed,
            key_evidence=DeviceKeyEvidence(
                parsed.public_key_spki,
                parsed.platform,
                KeyEvidenceSource.CLIENT_SUPPLIED
                if original is None
                else KeyEvidenceSource.LOCKED_APPROVED,
            ),
            expected_trust=parsed.trust,
            retained_original=original,
        )

    def preview(self, code: str, raw: bytes) -> dict[str, Any]:
        parsed = parse_recovery_request(Op.PREVIEW, raw)
        self._recovery.rate_gate("", UUID(str(parsed.document["account_id"])))
        with self._recovery.sessions.begin() as session:
            identity, account = self._context(session, parsed)
            credential = self._recovery._credential(session, identity, account)
            self._reserve(session, parsed, existing=False)
            self._key(session, parsed, account)
            self._proof(parsed)
            self._recovery._possession(identity, account, credential.verifier_sha256, code)
            result = {
                "contract_version": "v2",
                "schema_version": 2,
                "platform": parsed.platform.value,
                "operation_id": str(parsed.document["operation_id"]),
                "server_instance_id": str(identity.server_instance_id),
                "identity_epoch": identity.identity_epoch,
                "account_id": str(account.user_id),
                "account_label": account.display_name,
                "role": account.role,
                "code_generation": credential.generation,
                "confirmation_required": True,
            }
            authority = RecoveryAuthoritySnapshot(
                account.status,
                account.deleted_at,
                account.authority_generation,
                account.authority_generation,
                credential.generation,
                credential.generation,
                credential.verifier_sha256,
                credential.verifier_sha256,
            )
            now = database_now(session)  # LAST read; no query after freshness/gate checks.
            authority.require_current(now)
            _fresh(parsed, now)
            self._gate()
            return result

    def recover(self, code: str, raw: bytes) -> dict[str, Any]:
        return self._commit_or_outcome(code, raw, outcome=False)

    def recover_outcome(self, refresh: str, raw: bytes) -> dict[str, Any]:
        return self._commit_or_outcome(refresh, raw, outcome=True)

    def _commit_or_outcome(self, secret: str, raw: bytes, *, outcome: bool) -> dict[str, Any]:
        parsed = parse_recovery_request(Op.OUTCOME if outcome else Op.RECOVER, raw)
        doc = parsed.document
        self._recovery.rate_gate("", UUID(doc["account_id"]))
        with self._recovery.sessions.begin() as session:
            identity, account = self._context(session, parsed)
            credential = self._recovery._credential(session, identity, account)
            # Lock all replaced authority before operation evidence. Every issuer
            # also holds admission, preventing fresh devices while facts are read.
            devices = session.scalars(
                select(DeviceRow)
                .where(DeviceRow.user_id == account.user_id, DeviceRow.revoked_at.is_(None))
                .order_by(DeviceRow.device_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            bindings = session.scalars(
                select(UserSessionRow)
                .where(
                    UserSessionRow.user_id == account.user_id, UserSessionRow.revoked_at.is_(None)
                )
                .order_by(UserSessionRow.session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            receipt = session.get(
                AccountRecoveryOperationRow,
                UUID(doc["operation_id"]),
                with_for_update=True,
                populate_existing=True,
            )
            self._reserve(session, parsed, existing=receipt is not None)
            self._key(session, parsed, account)
            if receipt is not None:
                original = _original(receipt, parsed, account)
                self._proof(parsed, original)
                self._recovery._current_receipt(credential, receipt)
                if outcome:
                    if not hmac.compare_digest(
                        _refresh_hash(secret), bytes.fromhex(doc["next_refresh_token_sha256"])
                    ):
                        raise AccountRecoveryError()
                else:
                    if receipt.spent_verifier_sha256 is None:
                        raise AccountRecoveryError()
                    self._recovery._possession(
                        identity, account, receipt.spent_verifier_sha256, secret
                    )
                device = next(
                    (item for item in devices if item.device_id == receipt.result_device_id), None
                )
                binding = next(
                    (item for item in bindings if item.session_id == receipt.result_session_id),
                    None,
                )
                _result_binding(parsed, account, device, binding)
                assert device is not None and binding is not None
                authority = RecoveryAuthoritySnapshot(
                    account.status,
                    account.deleted_at,
                    account.authority_generation,
                    receipt.authority_generation,
                    credential.generation,
                    receipt.result_generation,
                    credential.verifier_sha256,
                    receipt.next_verifier_sha256,
                    binding.expires_at,
                    None if outcome else receipt.expires_at,
                )
                now = database_now(session)  # LAST read before pure deadline checks and grant.
                authority.require_current(now)
                return self._response(
                    account, device, binding, receipt, now, replayed=True, outcome=outcome
                )
            if outcome:
                raise AccountRecoveryError()
            self._proof(parsed)
            self._recovery._possession(identity, account, credential.verifier_sha256, secret)
            if credential.generation != doc["expected_code_generation"]:
                raise AccountRecoveryError("recovery_generation_conflict")
            verifier = bytes.fromhex(doc["next_code_verifier_sha256"])
            if hmac.compare_digest(credential.verifier_sha256, verifier):
                raise AccountRecoveryError("recovery_code_unchanged")
            thumb = bytes.fromhex(doc["device_key_thumbprint_sha256"])
            if (
                session.scalar(
                    select(DeviceRow.device_id)
                    .where(
                        DeviceRow.user_id == account.user_id,
                        (DeviceRow.public_key_thumbprint_sha256 == thumb)
                        | (DeviceRow.public_key == parsed.public_key_spki),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise AccountRecoveryError("recovery_key_already_bound")
            refresh_hash = bytes.fromhex(doc["next_refresh_token_sha256"])
            if (
                session.scalar(
                    select(UserSessionRow.session_id)
                    .where(UserSessionRow.refresh_token_hash == refresh_hash)
                    .limit(1)
                )
                is not None
            ):
                raise AccountRecoveryError("operation_conflict")
            # Planned replacement revokes ALL current devices. Its resulting
            # occupancy is one; current occupancy must not deny this replacement.
            limits = effective_account_limits(session, account.user_id)
            if limits.devices < 1:
                raise AccountRecoveryError("account_device_limit_reached")
            generation, spent = credential.generation, credential.verifier_sha256
            device_id, session_id = uuid4(), uuid4()
            authority = RecoveryAuthoritySnapshot(
                account.status,
                account.deleted_at,
                account.authority_generation,
                account.authority_generation,
                credential.generation,
                doc["expected_code_generation"],
                credential.verifier_sha256,
                spent,
            )
            now = database_now(session)  # FINAL SQL read after every blocking/secret operation.
            authority.require_current(now)
            _fresh(parsed, now)
            self._gate()
            revoke_account_authority(session, account, now)  # FIRST effect under current authority.
            device = DeviceRow(
                device_id=device_id,
                user_id=account.user_id,
                device_name=doc["device_name"],
                platform=parsed.platform.value,
                app_version=doc["app_version"],
                public_key=parsed.public_key_spki,
                public_key_thumbprint_sha256=thumb,
                created_at=now,
                updated_at=now,
            )
            session.add(device)
            session.flush()
            binding = UserSessionRow(
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
            session.add(binding)
            session.flush()
            receipt = self._recovery._new_receipt("RECOVER", dict(doc), account, generation, now)
            receipt.spent_verifier_sha256 = spent
            receipt.result_device_id, receipt.result_session_id = device_id, session_id
            receipt.binding_commit_id = UUID(doc["binding_commit_id"])
            receipt.contract_version, receipt.platform = "v2", parsed.platform.value
            receipt.original_document_sha256 = parsed.original_document_sha256
            receipt.original_request_document = dict(doc)
            receipt.signature_algorithm = "ES256-P1363"
            receipt.proof_domain = "autplay:device-compatibility:recovery:recover:v2\n"
            self._recovery._replace_credential(
                session, identity, account, credential, generation + 1, verifier, now
            )
            session.add(receipt)
            self._recovery._audit(session, account.user_id, device_id, receipt, now)
            session.flush()
            completed = database_now(session)
            _fresh(parsed, completed)
            if completed >= binding.expires_at or completed >= receipt.expires_at:
                raise AccountRecoveryError()
            self._gate()
            return self._response(
                account, device, binding, receipt, completed, replayed=False, outcome=False
            )

    def _gate(self) -> None:
        if not self._enabled():
            raise DeviceCompatibilityError()

    def _response(
        self,
        account: UserAccountRow,
        device: DeviceRow,
        binding: UserSessionRow,
        receipt: AccountRecoveryOperationRow,
        now: datetime,
        *,
        replayed: bool,
        outcome: bool,
    ) -> dict[str, Any]:
        # Preserve recovery's fresh-access-on-authorized-replay semantics. No
        # cached token/response, portable receipt TTL rule or new refresh family.
        expires = min(now + self._recovery.ttl, binding.expires_at)
        principal = Principal(
            account.user_id, device.device_id, binding.session_id, AccountRole(account.role)
        )
        return {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": receipt.platform,
            "operation_id": str(receipt.operation_id),
            "binding_commit_id": str(receipt.binding_commit_id),
            "server_instance_id": str(receipt.server_instance_id),
            "user_id": str(account.user_id),
            "account_label": account.display_name,
            "device_id": str(device.device_id),
            "session_id": str(binding.session_id),
            "refresh_generation": 0,
            "refresh_absolute_expires_at": iso8601(binding.expires_at),
            "receipt_expires_at": iso8601(receipt.expires_at),
            "code_generation": receipt.result_generation,
            "access_token": self._recovery.access.issue(
                principal, token_id=uuid4(), issued_at=now, expires_at=expires
            ),
            "access_expires_at": iso8601(expires),
            "replayed": replayed,
            "outcome_recovered": outcome,
        }


def _original(
    row: AccountRecoveryOperationRow,
    parsed: ParsedRecoveryRequest,
    account: UserAccountRow,
) -> ParsedRecoveryRequest:
    if (
        row.contract_version != "v2"
        or row.kind != "RECOVER"
        or row.user_id != account.user_id
        or row.authority_generation != account.authority_generation
        or row.original_request_document is None
        or row.signature_algorithm != "ES256-P1363"
        or row.proof_domain != "autplay:device-compatibility:recovery:recover:v2\n"
    ):
        raise AccountRecoveryError("operation_conflict")
    original = parse_recovery_request(Op.RECOVER, rfc8785.dumps(row.original_request_document))
    if (
        row.platform != parsed.platform.value
        or row.server_instance_id != UUID(str(parsed.trust.server_instance_id))
        or row.identity_epoch != parsed.trust.identity_epoch
        or row.operation_id != UUID(str(parsed.document["operation_id"]))
        or row.binding_commit_id != UUID(str(parsed.document["binding_commit_id"]))
        or row.previous_generation != parsed.document["expected_code_generation"]
        or row.result_generation != row.previous_generation + 1
        or row.next_verifier_sha256
        != bytes.fromhex(str(parsed.document["next_code_verifier_sha256"]))
        or row.spent_verifier_sha256 is None
        or len(row.spent_verifier_sha256) != 32
        or row.original_document_sha256 != parsed.original_document_sha256
        or row.request_sha256 != parsed.request_sha256
        or row.original_document_sha256 != original.original_document_sha256
        or row.request_sha256 != original.request_sha256
    ):
        raise AccountRecoveryError("operation_conflict")
    return original


def _result_binding(
    parsed: ParsedRecoveryRequest,
    account: UserAccountRow,
    device: DeviceRow | None,
    binding: UserSessionRow | None,
) -> None:
    if (
        device is None
        or binding is None
        or device.user_id != account.user_id
        or device.revoked_at is not None
        or device.platform != parsed.platform.value
        or device.public_key != parsed.public_key_spki
        or device.public_key_thumbprint_sha256
        != bytes.fromhex(str(parsed.document["device_key_thumbprint_sha256"]))
        or binding.user_id != account.user_id
        or binding.device_id != device.device_id
        or binding.revoked_at is not None
        or binding.session_mode != "V2"
        or binding.family_id != binding.session_id
        or binding.generation != 0
        or binding.refresh_token_hash
        != bytes.fromhex(str(parsed.document["next_refresh_token_sha256"]))
    ):
        raise AccountRecoveryError()


def _refresh_hash(secret: str) -> bytes:
    try:
        raw = secret.encode("ascii")
    except UnicodeEncodeError:
        raise AccountRecoveryError() from None
    if not 32 <= len(raw) <= 128:
        raise AccountRecoveryError()
    return sha256(raw).digest()


def _fresh(parsed: ParsedRecoveryRequest, now: datetime) -> None:
    value = str(parsed.document["requested_at"])
    base = datetime.fromisoformat(value[:19]).replace(tzinfo=UTC)
    fraction = int(value[20:-1].ljust(9, "0")) if value[19] == "." else 0
    delta = now - base
    nanoseconds = (
        (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    ) * 1000 - fraction
    if abs(nanoseconds) > 120000000000:
        raise AccountRecoveryError()


__all__ = ("DeviceRecoveryServiceV2",)
