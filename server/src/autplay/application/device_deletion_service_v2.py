"""Six D1 deletion operations; API source composed, executable qualification pending.

The independent ledger owns ATTEMPTED/SEALED decisions. Full nonsecret originals
live in typed owner history; transport secrets remain verifier/hash-only. Every
blocking read precedes the final SQL clock. Gate callbacks must be pure snapshots.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import rfc8785
from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.account_recovery import revoke_account_authority
from autplay.adapters.postgresql.device_identifier_reservations import device_identifier_reserved
from autplay.adapters.postgresql.device_platform_evidence import has_v2_key_evidence_conflict
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_deletion import (
    AccountDeletionProofRow,
    AccountDeletionRequestRow,
)
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryOperationRow
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.profile_pairing import DeviceKeyBlockRow, ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import (
    effective_account_limits,
    lock_resource_admission,
)
from autplay.adapters.postgresql.retained_deletion_reservations import DeletionReservationReplay
from autplay.application.account_deletion import AccountDeletionService
from autplay.application.account_recovery import database_now
from autplay.application.deletion_request_evidence import (
    initial_receipt_sha256,
    locked_owner_history,
)
from autplay.application.device_recovery_service_v2 import _refresh_hash
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.account_deletion import AccountDeletionError
from autplay.domain.account_recovery import code_verifier, requested_at
from autplay.domain.auth import AccessTokenClaims, AccountRole, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_deletion_v2 import (
    DeletionOperation as Op,
)
from autplay.domain.device_deletion_v2 import (
    DeletionV2Error,
    DeviceKeyEvidence,
    DevicePlatform,
    DeviceTrustEvidence,
    KeyEvidenceSource,
    ParsedDeletionRequest,
    parse_deletion_request,
    verify_deletion_proof,
    verify_deletion_resolution_candidate,
)
from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence
from autplay.domain.profile_pairing import iso8601


def _off() -> bool:
    return False


class DeviceDeletionServiceV2:
    def __init__(
        self,
        deletion: AccountDeletionService,
        *,
        identity: ProfilePairingService,
        enabled: Callable[[], bool] = _off,
        deletion_enabled: Callable[[], bool] = _off,
        recovery_enabled: Callable[[], bool] = _off,
        evidence_ready: Callable[[], bool] = _off,
    ) -> None:
        self._deletion, self._recovery, self._ledger = deletion, deletion.recovery, deletion.ledger
        self._identity = identity
        self._enabled, self._deletion_enabled = enabled, deletion_enabled
        self._recovery_enabled, self._evidence_ready = recovery_enabled, evidence_ready

    def rate_gate(self, source: str) -> None:
        self._recovery.rate_gate(source)

    def _ready(self, *, first_request: bool = False) -> None:
        if not self._evidence_ready():
            raise DeletionEvidenceError()
        if first_request and not (
            self._enabled() and self._deletion_enabled() and self._recovery_enabled()
        ):
            raise DeviceCompatibilityError()

    def _parse(self, operation: Op, raw: bytes) -> ParsedDeletionRequest:
        parsed = parse_deletion_request(operation, raw)
        self._recovery.rate_gate("", UUID(str(parsed.document["account_id"])))
        self._ready()
        return parsed

    def _lock(
        self,
        session: Session,
        parsed: ParsedDeletionRequest,
        *,
        historical: bool = False,
    ) -> tuple[ServerInstanceRow, UserAccountRow]:
        try:
            current = self._identity.current_device_contract_identity(session)
        except RuntimeError, ValueError:
            raise DeviceCompatibilityError() from None
        trust = DeviceTrustEvidence(
            str(current.server_instance_id),
            current.identity_epoch,
            current.thumbprint.hex(),
            current.api_origin,
            current.stream_origin,
        )
        if not historical and parsed.trust != trust:
            raise AccountDeletionError()
        identity = session.get(ServerInstanceRow, current.server_instance_id)
        if identity is None:
            raise DeviceCompatibilityError()
        lock_resource_admission(session)
        owner = UUID(str(parsed.document["account_id"]))
        _acquire_sync_owner_publish_lock(session, owner)
        account = session.get(UserAccountRow, owner, with_for_update=True, populate_existing=True)
        if account is None or account.deleted_at is not None:
            raise AccountDeletionError()
        return identity, account

    @staticmethod
    def _key(session: Session, parsed: ParsedDeletionRequest, account: UserAccountRow) -> None:
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
            raise AccountDeletionError()

    @staticmethod
    def _proof(parsed: ParsedDeletionRequest, *, actor: DeviceRow | None = None) -> None:
        verify_deletion_proof(
            parsed,
            key_evidence=DeviceKeyEvidence(
                cast(bytes, actor.public_key) if actor is not None else parsed.public_key_spki,
                DevicePlatform(actor.platform) if actor is not None else parsed.platform,
                KeyEvidenceSource.LOCKED_APPROVED
                if actor is not None
                else KeyEvidenceSource.CLIENT_SUPPLIED,
            ),
            expected_trust=parsed.trust,
        )

    def request(
        self,
        actor: Principal,
        claims: AccessTokenClaims,
        code: str,
        raw: bytes,
    ) -> dict[str, Any]:
        parsed = self._parse(Op.REQUEST, raw)
        doc = parsed.document
        operation = UUID(doc["operation_id"])
        with self._recovery.sessions.begin() as session:
            identity, account = self._lock(session, parsed)
            credential = self._recovery._credential(session, identity, account)
            device = session.get(
                DeviceRow, actor.device_id, with_for_update=True, populate_existing=True
            )
            binding = session.get(
                UserSessionRow, actor.session_id, with_for_update=True, populate_existing=True
            )
            self._key(session, parsed, account)
            if (
                device is None
                or device.public_key is None
                or device.platform not in {"ANDROID", "MACOS"}
            ):
                raise AccountDeletionError()
            self._proof(parsed, actor=device)
            self._recovery._possession(identity, account, credential.verifier_sha256, code)
            self._deletion._require_remaining_owner(session, account)
            if (
                device_identifier_reserved(session, operation)
                or session.get(AccountDeletionProofRow, operation, with_for_update=True) is not None
            ):
                raise AccountDeletionError("operation_conflict")
            history = locked_owner_history(session, self._ledger, account.user_id)
            # Read the entire independent chain: IDs are global, including SEALED.
            evidence = self._ledger.request_read()
            coverage = self._ledger.request_coverage_started_at()
            if any(operation in {item.request_id, item.cancel_operation_id} for item in evidence):
                # A rowless ATTEMPTED barrier is an uncertain effect, never a fresh
                # request or a negative. Exact accepted originals use receipt.
                raise DeletionEvidenceError()
            if any(
                item.decision == "ATTEMPTED" and item.cancel_operation_id is None
                for item in history.values()
            ):
                raise AccountDeletionError()
            self._request_facts(
                parsed, account, credential.generation, actor, claims, device, binding
            )
            now = database_now(session)
            _actor_live(claims, binding, now)
            _fresh(parsed, now)
            _coverage(parsed, coverage)
            self._ready(first_request=True)
            row = AccountDeletionRequestRow(
                deletion_request_id=operation,
                user_id=account.user_id,
                server_instance_id=identity.server_instance_id,
                identity_epoch=identity.identity_epoch,
                identity_thumbprint_sha256=identity.identity_thumbprint_sha256,
                request_sha256=parsed.request_sha256,
                actor_device_id=actor.device_id,
                actor_public_key_spki=device.public_key,
                code_generation=credential.generation,
                code_verifier_sha256=credential.verifier_sha256,
                authority_generation=account.authority_generation + 1,
                requested_at=now,
                cancel_before=now + timedelta(days=30),
                state="PENDING",
                revision=1,
                contract_version="v2",
                platform=parsed.platform.value,
            )
            proof = _history(parsed, device, credential.verifier_sha256, "ATTEMPTED", now)
            self._ledger.request_record(
                DeletionRequestEvidence(
                    self._ledger.owner_tag(account.user_id),
                    operation,
                    parsed.request_sha256.hex(),
                    initial_receipt_sha256(row),
                    "ATTEMPTED",
                    now,
                )
            )
            # Ledger fsync may wait. Re-sample after it; all locked facts are still
            # held. Failure leaves ATTEMPTED, and resolve must remain unavailable.
            final = database_now(session)
            _actor_live(claims, binding, final)
            _fresh(parsed, final)
            self._ready(first_request=True)
            session.add(proof)  # FIRST DB effect, after independent ATTEMPTED.
            session.flush()
            revoke_account_authority(session, account, final)
            account.status = "DELETION_PENDING"
            session.add(row)
            session.add(
                AuditEventRow(
                    occurred_at=final,
                    actor_type="USER",
                    actor_user_id=account.user_id,
                    actor_device_id=actor.device_id,
                    action="account_deletion.requested",
                    target_type="USER_ACCOUNT",
                    target_id=account.user_id,
                    request_id=operation,
                    reason_code="OWNER_CONFIRMED_DELETION",
                    metadata_sanitized={"revision": 1},
                )
            )
            session.flush()
            return _result(row, parsed.platform.value, replayed=False)

    @staticmethod
    def _request_facts(
        parsed: ParsedDeletionRequest,
        account: UserAccountRow,
        generation: int,
        actor: Principal,
        claims: AccessTokenClaims,
        device: DeviceRow,
        binding: UserSessionRow | None,
    ) -> None:
        doc = parsed.document
        if (
            type(claims) is not AccessTokenClaims
            or account.status != "ACTIVE"
            or actor.user_id != account.user_id
            or actor.role.value != account.role
            or (claims.user_id, claims.device_id, claims.session_id)
            != (actor.user_id, actor.device_id, actor.session_id)
            or device.user_id != account.user_id
            or device.revoked_at is not None
            or device.public_key_thumbprint_sha256
            != bytes.fromhex(str(doc["device_key_thumbprint_sha256"]))
            or binding is None
            or binding.user_id != account.user_id
            or binding.device_id != device.device_id
            or binding.session_mode != "V2"
            or binding.revoked_at is not None
        ):
            raise AccountDeletionError()
        if (
            generation != doc["expected_code_generation"]
            or account.authority_generation != doc["expected_authority_generation"]
        ):
            raise AccountDeletionError("deletion_generation_conflict")

    def request_receipt(self, code: str, raw: bytes) -> dict[str, Any]:
        return self._request_continuation(code, raw, resolve=False)

    def request_resolve(self, code: str, raw: bytes) -> dict[str, Any]:
        return self._request_continuation(code, raw, resolve=True)

    def _request_continuation(self, code: str, raw: bytes, *, resolve: bool) -> dict[str, Any]:
        parsed = self._parse(Op.REQUEST_RESOLVE if resolve else Op.REQUEST_RECEIPT, raw)
        operation = UUID(str(parsed.document["operation_id"]))
        with self._recovery.sessions.begin() as session:
            identity, account = self._lock(session, parsed, historical=True)
            proof = session.get(
                AccountDeletionProofRow, operation, with_for_update=True, populate_existing=True
            )
            row = session.get(
                AccountDeletionRequestRow, operation, with_for_update=True, populate_existing=True
            )
            locked_owner_history(session, self._ledger, account.user_id)
            item = next(
                (item for item in self._ledger.request_read() if item.request_id == operation), None
            )
            if proof is not None:
                original = _retained(proof, parsed)
                verify_deletion_proof(
                    parsed,
                    key_evidence=DeviceKeyEvidence(
                        proof.actor_public_key_spki,
                        DevicePlatform(proof.platform),
                        KeyEvidenceSource.HISTORICAL_APPROVED,
                    ),
                    expected_trust=original.trust,
                    retained_original=original,
                )
                verifier = code_verifier(
                    UUID(original.trust.server_instance_id), account.user_id, code
                )
                if not hmac.compare_digest(proof.code_verifier_sha256, verifier):
                    raise AccountDeletionError()
                if item is None or item.owner_tag != self._ledger.owner_tag(account.user_id):
                    raise DeletionEvidenceError()
                if proof.decision == "SEALED":
                    if not resolve or row is not None or item.decision != "SEALED":
                        raise AccountDeletionError()
                    if (
                        item.decided_at != proof.decided_at
                        or item.request_sha256 != parsed.request_sha256.hex()
                        or item.receipt_sha256
                        != self._deletion._seal_proof(dict(parsed.document), verifier)
                    ):
                        raise DeletionEvidenceError()
                    return _negative(parsed, item, replayed=True)
                if (
                    row is None
                    or row.contract_version != "v2"
                    or row.platform != proof.platform
                    or row.user_id != proof.user_id
                    or row.actor_device_id != proof.actor_device_id
                    or row.actor_public_key_spki != proof.actor_public_key_spki
                    or row.request_sha256 != proof.request_sha256
                    or row.requested_at != proof.decided_at
                    or row.server_instance_id != proof.server_instance_id
                    or row.identity_epoch != proof.identity_epoch
                    or row.identity_thumbprint_sha256
                    != bytes.fromhex(original.trust.identity_thumbprint_sha256)
                    or row.code_verifier_sha256 != verifier
                    or row.code_generation != original.document["expected_code_generation"]
                    or row.authority_generation
                    != original.document["expected_authority_generation"] + 1
                ):
                    raise DeletionEvidenceError()
                return _result(row, proof.platform, replayed=True)
            # Missing full history is never reconstructed from the incoming proof
            # or ledger digest. Any evidence/authoritative row makes this unknown.
            if row is not None or item is not None or not resolve:
                raise DeletionEvidenceError()
            current = DeviceTrustEvidence(
                str(identity.server_instance_id),
                identity.identity_epoch,
                identity.identity_thumbprint_sha256.hex(),
                identity.api_origin,
                identity.stream_origin,
            )
            if parsed.trust != current:
                raise AccountDeletionError()
            credential = self._recovery._credential(session, identity, account)
            device = session.scalar(
                select(DeviceRow)
                .where(
                    DeviceRow.user_id == account.user_id,
                    DeviceRow.public_key == parsed.public_key_spki,
                )
                .order_by(DeviceRow.device_id)
                .limit(1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            self._key(session, parsed, account)
            if (
                device is None
                or device.public_key is None
                or device.platform not in {"ANDROID", "MACOS"}
            ):
                raise AccountDeletionError()
            verify_deletion_resolution_candidate(
                parsed,
                key_evidence=DeviceKeyEvidence(
                    device.public_key,
                    DevicePlatform(device.platform),
                    KeyEvidenceSource.LOCKED_APPROVED,
                ),
                expected_trust=current,
            )
            self._recovery._possession(identity, account, credential.verifier_sha256, code)
            if device_identifier_reserved(session, operation) or any(
                operation in {entry.request_id, entry.cancel_operation_id}
                for entry in self._ledger.request_read()
            ):
                raise AccountDeletionError("operation_conflict")
            if (
                account.status != "ACTIVE"
                or account.authority_generation != parsed.document["expected_authority_generation"]
                or credential.generation != parsed.document["expected_code_generation"]
                or device.public_key_thumbprint_sha256
                != bytes.fromhex(str(parsed.document["device_key_thumbprint_sha256"]))
            ):
                raise AccountDeletionError()
            coverage = self._ledger.request_coverage_started_at()
            now = database_now(session)  # FINAL read before pure checks and independent veto.
            if now <= requested_at(dict(parsed.document)) + timedelta(seconds=120, microseconds=1):
                raise AccountDeletionError("deletion_resolution_not_ready")
            _coverage(parsed, coverage)
            self._ready()
            item = self._ledger.request_record(
                DeletionRequestEvidence(
                    self._ledger.owner_tag(account.user_id),
                    operation,
                    parsed.request_sha256.hex(),
                    self._deletion._seal_proof(dict(parsed.document), credential.verifier_sha256),
                    "SEALED",
                    now,
                )
            )
            session.add(
                _history(parsed, device, credential.verifier_sha256, "SEALED", item.decided_at)
            )
            session.flush()
            return _negative(parsed, item, replayed=False)

    def preview(self, code: str, raw: bytes) -> dict[str, Any]:
        parsed = self._parse(Op.PREVIEW, raw)
        with self._recovery.sessions.begin() as session:
            identity, account = self._lock(session, parsed)
            self._key(session, parsed, account)
            self._proof(parsed)
            locked_owner_history(session, self._ledger, account.user_id)
            credential = self._recovery._credential(session, identity, account)
            self._recovery._possession(identity, account, credential.verifier_sha256, code)
            identifier = UUID(str(parsed.document["operation_id"]))
            if device_identifier_reserved(session, identifier) or any(
                identifier in {item.request_id, item.cancel_operation_id}
                for item in self._ledger.request_read()
            ):
                raise AccountDeletionError("operation_conflict")
            row = session.scalar(
                select(AccountDeletionRequestRow)
                .where(
                    AccountDeletionRequestRow.user_id == account.user_id,
                    AccountDeletionRequestRow.state == "PENDING",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is not None and (
                credential.generation != row.code_generation
                or account.authority_generation != row.authority_generation
                or not hmac.compare_digest(credential.verifier_sha256, row.code_verifier_sha256)
            ):
                raise AccountDeletionError("deletion_generation_conflict")
            now = database_now(session)
            _pending(account, row, now)
            _fresh(parsed, now)
            self._ready()  # Existing accepted deletion cancellation survives feature switches.
            assert row is not None
            return {
                **_result(row, parsed.platform.value, replayed=False),
                "account_label": account.display_name,
                "code_generation": credential.generation,
                "confirmation_required": True,
            }

    def cancel(self, code: str, raw: bytes) -> dict[str, Any]:
        return self._cancel(code, raw, outcome=False)

    def cancel_outcome(self, refresh: str, raw: bytes) -> dict[str, Any]:
        return self._cancel(refresh, raw, outcome=True)

    def _cancel(self, secret: str, raw: bytes, *, outcome: bool) -> dict[str, Any]:
        parsed = self._parse(Op.CANCEL_OUTCOME if outcome else Op.CANCEL, raw)
        doc = parsed.document
        parent, operation = UUID(doc["deletion_request_id"]), UUID(doc["operation_id"])
        with self._recovery.sessions.begin() as session:
            identity, account = self._lock(session, parsed)
            credential = self._recovery._credential(session, identity, account)
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
            history = locked_owner_history(
                session,
                self._ledger,
                account.user_id,
                retry_cancel=(parent, operation, doc["request_sha256"]),
            )
            row = session.get(
                AccountDeletionRequestRow, parent, with_for_update=True, populate_existing=True
            )
            receipt = session.get(
                AccountRecoveryOperationRow, operation, with_for_update=True, populate_existing=True
            )
            self._key(session, parsed, account)
            if (
                row is None
                or row.user_id != account.user_id
                or row.server_instance_id != identity.server_instance_id
                or parent == operation
                or parent == UUID(doc["binding_commit_id"])
            ):
                raise AccountDeletionError()
            if row.state == "CANCELLED":
                if (
                    receipt is None
                    or row.cancel_operation_id != operation
                    or row.revision != doc["expected_revision"] + 1
                    or receipt.contract_version != "v2"
                    or receipt.kind != "DELETE_CANCEL"
                    or receipt.original_request_document is None
                    or receipt.platform != parsed.platform.value
                    or receipt.signature_algorithm != "ES256-P1363"
                    or receipt.proof_domain != "autplay:device-compatibility:deletion:cancel:v2\n"
                ):
                    raise AccountDeletionError()
                original = _stored_original(Op.CANCEL, receipt.original_request_document)
                verify_deletion_proof(
                    parsed,
                    key_evidence=DeviceKeyEvidence(
                        original.public_key_spki,
                        original.platform,
                        KeyEvidenceSource.HISTORICAL_APPROVED
                        if outcome
                        else KeyEvidenceSource.LOCKED_APPROVED,
                    ),
                    expected_trust=parsed.trust,
                    retained_original=original,
                )
                if (
                    receipt.user_id != account.user_id
                    or receipt.server_instance_id != identity.server_instance_id
                    or receipt.identity_epoch != identity.identity_epoch
                    or receipt.operation_id != operation
                    or receipt.original_document_sha256 != parsed.original_document_sha256
                    or receipt.original_document_sha256 != original.original_document_sha256
                    or receipt.request_sha256 != parsed.request_sha256
                    or receipt.request_sha256 != original.request_sha256
                    or receipt.binding_commit_id != UUID(doc["binding_commit_id"])
                    or receipt.previous_generation != doc["expected_code_generation"]
                    or receipt.result_generation != receipt.previous_generation + 1
                    or receipt.authority_generation != account.authority_generation
                    or receipt.next_verifier_sha256
                    != bytes.fromhex(doc["next_code_verifier_sha256"])
                    or receipt.spent_verifier_sha256 is None
                ):
                    raise AccountDeletionError("operation_conflict")
                self._reserve(session, parsed, parent=parent, replay=True)
                if outcome:
                    if not hmac.compare_digest(
                        _refresh_hash(secret), bytes.fromhex(doc["next_refresh_token_sha256"])
                    ):
                        raise AccountDeletionError()
                else:
                    self._recovery._possession(
                        identity, account, receipt.spent_verifier_sha256, secret
                    )
                self._recovery._current_receipt(credential, receipt)
                device = next(
                    (item for item in devices if item.device_id == receipt.result_device_id), None
                )
                binding = next(
                    (item for item in bindings if item.session_id == receipt.result_session_id),
                    None,
                )
                _result_binding(parsed, account, device, binding)
                assert device is not None and binding is not None
                now = database_now(
                    session
                )  # LAST read after all original/key/history/collision checks.
                if (
                    account.status != "ACTIVE"
                    or binding.expires_at <= now
                    or (not outcome and receipt.expires_at <= now)
                ):
                    raise AccountDeletionError()
                self._ready()
                return self._binding(
                    account, device, binding, receipt, now, replayed=True, outcome=outcome
                )
            if outcome or receipt is not None:
                raise AccountDeletionError()
            self._proof(parsed)
            self._recovery._possession(identity, account, credential.verifier_sha256, secret)
            self._reserve(session, parsed, parent=parent, replay=False)
            if (
                credential.generation != doc["expected_code_generation"]
                or credential.generation != row.code_generation
                or account.authority_generation != row.authority_generation
                or not hmac.compare_digest(credential.verifier_sha256, row.code_verifier_sha256)
            ):
                raise AccountDeletionError("deletion_generation_conflict")
            verifier = bytes.fromhex(doc["next_code_verifier_sha256"])
            if hmac.compare_digest(credential.verifier_sha256, verifier):
                raise AccountDeletionError("recovery_code_unchanged")
            if (
                session.scalar(
                    select(DeviceRow.device_id)
                    .where(
                        DeviceRow.user_id == account.user_id,
                        (DeviceRow.public_key == parsed.public_key_spki)
                        | (
                            DeviceRow.public_key_thumbprint_sha256
                            == bytes.fromhex(doc["device_key_thumbprint_sha256"])
                        ),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise AccountDeletionError("recovery_key_already_bound")
            refresh_hash = bytes.fromhex(doc["next_refresh_token_sha256"])
            if (
                session.scalar(
                    select(UserSessionRow.session_id)
                    .where(UserSessionRow.refresh_token_hash == refresh_hash)
                    .limit(1)
                )
                is not None
            ):
                raise AccountDeletionError("operation_conflict")
            if effective_account_limits(session, account.user_id).devices < 1:
                raise AccountDeletionError("account_device_limit_reached")
            evidence = history[parent]
            if row.revision != doc["expected_revision"]:
                raise AccountDeletionError("deletion_generation_conflict")
            device_id, session_id = uuid4(), uuid4()
            generation, spent = credential.generation, credential.verifier_sha256
            now = database_now(session)
            _pending(account, row, now)
            _fresh(parsed, now)
            self._ready()
            cancelled_at = evidence.cancelled_at or now
            if not row.requested_at <= cancelled_at < row.cancel_before:
                raise DeletionEvidenceError()
            # Conservatively record cancellation before DB acceptance. An unknown
            # external write leaves its existing retry_cancel reconciliation path.
            self._ledger.request_record(
                replace(
                    evidence,
                    cancel_operation_id=operation,
                    cancel_request_sha256=doc["request_sha256"],
                    cancelled_at=cancelled_at,
                )
            )
            final = database_now(session)  # Ledger write can wait past freshness/deadline.
            _pending(account, row, final)
            _fresh(parsed, final)
            self._ready()
            revoke_account_authority(session, account, final)  # FIRST DB effect.
            account.status = "ACTIVE"
            device = DeviceRow(
                device_id=device_id,
                user_id=account.user_id,
                device_name=doc["device_name"],
                platform=parsed.platform.value,
                app_version=doc["app_version"],
                public_key=parsed.public_key_spki,
                public_key_thumbprint_sha256=bytes.fromhex(doc["device_key_thumbprint_sha256"]),
                created_at=final,
                updated_at=final,
            )
            session.add(device)
            session.flush()
            binding = UserSessionRow(
                session_id=session_id,
                user_id=account.user_id,
                device_id=device_id,
                refresh_token_hash=refresh_hash,
                issued_at=final,
                expires_at=final + timedelta(days=90),
                last_rotated_at=final,
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
            session.add(binding)
            session.flush()
            receipt = self._recovery._new_receipt(
                "DELETE_CANCEL", dict(doc), account, generation, final
            )
            receipt.spent_verifier_sha256 = spent
            receipt.result_device_id, receipt.result_session_id = device_id, session_id
            receipt.binding_commit_id = UUID(doc["binding_commit_id"])
            receipt.contract_version, receipt.platform = "v2", parsed.platform.value
            receipt.original_document_sha256, receipt.original_request_document = (
                parsed.original_document_sha256,
                dict(doc),
            )
            receipt.signature_algorithm = "ES256-P1363"
            receipt.proof_domain = "autplay:device-compatibility:deletion:cancel:v2\n"
            self._recovery._replace_credential(
                session, identity, account, credential, generation + 1, verifier, final
            )
            session.add(receipt)
            session.flush()  # Parent trigger requires the exact cancellation receipt.
            row.state, row.revision, row.cancel_operation_id, row.cancelled_at = (
                "CANCELLED",
                row.revision + 1,
                operation,
                cancelled_at,
            )
            self._recovery._audit(session, account.user_id, device_id, receipt, final)
            session.flush()
            completed = database_now(session)
            _fresh(parsed, completed)
            if (
                completed >= row.cancel_before
                or completed >= binding.expires_at
                or completed >= receipt.expires_at
            ):
                raise AccountDeletionError()
            self._ready()
            return self._binding(
                account, device, binding, receipt, completed, replayed=False, outcome=False
            )

    def _reserve(
        self, session: Session, parsed: ParsedDeletionRequest, *, parent: UUID, replay: bool
    ) -> None:
        operation, binding = (
            UUID(str(parsed.document["operation_id"])),
            UUID(str(parsed.document["binding_commit_id"])),
        )
        if operation == binding or any(
            device_identifier_reserved(
                session,
                identifier,
                own_recovery_operation_id=operation if replay else None,
                own_deletion_request_id=parent if replay else None,
                deletion_replay=DeletionReservationReplay(
                    UUID(str(parsed.document["account_id"])),
                    parent,
                    parsed.request_sha256.hex(),
                    cancel_operation_id=operation,
                ),
            )
            for identifier in (operation, binding)
        ):
            raise AccountDeletionError("operation_conflict")

    def _binding(
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


def _history(
    parsed: ParsedDeletionRequest, actor: DeviceRow, verifier: bytes, decision: str, now: datetime
) -> AccountDeletionProofRow:
    return AccountDeletionProofRow(
        operation_id=UUID(str(parsed.document["operation_id"])),
        user_id=actor.user_id,
        server_instance_id=UUID(parsed.trust.server_instance_id),
        identity_epoch=parsed.trust.identity_epoch,
        actor_device_id=actor.device_id,
        actor_public_key_spki=actor.public_key,
        actor_key_thumbprint_sha256=actor.public_key_thumbprint_sha256,
        code_verifier_sha256=verifier,
        contract_version="v2",
        platform=parsed.platform.value,
        request_sha256=parsed.request_sha256,
        original_document_sha256=parsed.original_document_sha256,
        original_request_document=dict(parsed.document),
        signature_algorithm="ES256-P1363",
        proof_domain="autplay:device-compatibility:deletion:request:v2\n",
        decision=decision,
        decided_at=now,
    )


def _retained(row: AccountDeletionProofRow, parsed: ParsedDeletionRequest) -> ParsedDeletionRequest:
    if (
        row.contract_version != "v2"
        or row.platform not in {"ANDROID", "MACOS"}
        or row.signature_algorithm != "ES256-P1363"
        or row.decision not in {"ATTEMPTED", "SEALED"}
        or row.proof_domain != "autplay:device-compatibility:deletion:request:v2\n"
        or row.original_request_document is None
    ):
        raise DeletionEvidenceError()
    original = _stored_original(Op.REQUEST, row.original_request_document)
    if (
        row.original_document_sha256 != parsed.original_document_sha256
        or row.original_document_sha256 != original.original_document_sha256
        or row.request_sha256 != parsed.request_sha256
        or row.request_sha256 != original.request_sha256
        or row.operation_id != UUID(str(original.document["operation_id"]))
        or row.user_id != UUID(str(original.document["account_id"]))
        or row.server_instance_id != UUID(original.trust.server_instance_id)
        or row.identity_epoch != original.trust.identity_epoch
        or row.platform != original.platform.value
        or row.actor_public_key_spki != original.public_key_spki
        or row.actor_key_thumbprint_sha256
        != bytes.fromhex(str(original.document["device_key_thumbprint_sha256"]))
    ):
        raise DeletionEvidenceError()
    return original


def _stored_original(operation: Op, document: dict[str, Any]) -> ParsedDeletionRequest:
    try:
        return parse_deletion_request(operation, rfc8785.dumps(document))
    except DeletionV2Error, ValueError, TypeError, OverflowError, RecursionError:
        raise DeletionEvidenceError() from None


def _result(row: AccountDeletionRequestRow, platform: str, *, replayed: bool) -> dict[str, Any]:
    if row.state not in {"PENDING", "CANCELLED"}:
        raise AccountDeletionError()  # PURGING is not a cancellable wire result.
    return {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": platform,
        "deletion_request_id": str(row.deletion_request_id),
        "account_id": str(row.user_id),
        "state": row.state,
        "revision": row.revision,
        "requested_at": iso8601(row.requested_at),
        "cancel_before": iso8601(row.cancel_before),
        "replayed": replayed,
    }


def _negative(
    parsed: ParsedDeletionRequest, item: DeletionRequestEvidence, *, replayed: bool
) -> dict[str, Any]:
    return {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": parsed.platform.value,
        "deletion_request_id": str(item.request_id),
        "account_id": str(parsed.document["account_id"]),
        "request_sha256": item.request_sha256,
        "state": "NOT_ACCEPTED",
        "resolved_at": iso8601(item.decided_at),
        "replayed": replayed,
    }


def _pending(account: UserAccountRow, row: AccountDeletionRequestRow | None, now: datetime) -> None:
    if (
        row is None
        or account.status != "DELETION_PENDING"
        or row.state != "PENDING"
        or now >= row.cancel_before
    ):
        raise AccountDeletionError()


def _result_binding(
    parsed: ParsedDeletionRequest,
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
        raise AccountDeletionError()


def _actor_live(claims: AccessTokenClaims, binding: UserSessionRow | None, now: datetime) -> None:
    if (
        binding is None
        or binding.expires_at <= now
        or claims.expires_at <= now
        or claims.not_before > now
        or claims.issued_at > now
    ):
        raise AccountDeletionError()


def _coverage(parsed: ParsedDeletionRequest, started: datetime) -> None:
    if requested_at(dict(parsed.document)) <= started + timedelta(seconds=120, microseconds=1):
        raise DeletionEvidenceError()


def _fresh(parsed: ParsedDeletionRequest, now: datetime) -> None:
    value = str(parsed.document["requested_at"])
    base = datetime.fromisoformat(value[:19]).replace(tzinfo=UTC)
    fraction = int(value[20:-1].ljust(9, "0")) if value[19] == "." else 0
    delta = now - base
    nanoseconds = (
        (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    ) * 1000 - fraction
    if abs(nanoseconds) > 120000000000:
        raise AccountDeletionError()


__all__ = ("DeviceDeletionServiceV2",)
