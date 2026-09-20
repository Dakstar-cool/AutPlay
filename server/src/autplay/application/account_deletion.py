"""Atomic access suspension and explicitly proved thirty-day cancellation."""

from __future__ import annotations

import hmac
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.account_recovery import revoke_account_authority
from autplay.adapters.postgresql.models import AuditEventRow, DeviceRow, UserAccountRow
from autplay.adapters.postgresql.models.account_deletion import AccountDeletionRequestRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.account_recovery import AccountRecoveryService, database_now
from autplay.application.deletion_request_evidence import (
    initial_receipt_sha256,
    locked_owner_history,
)
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.account_deletion import AccountDeletionError, parse_request, verify_device
from autplay.domain.account_recovery import code_verifier, requested_at, require_fresh
from autplay.domain.auth import Principal
from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence
from autplay.domain.profile_pairing import canonical_sha256, iso8601
from autplay.ports.privacy_deletion import DeletionRequestLedger


class AccountDeletionService:
    def __init__(self, recovery: AccountRecoveryService, ledger: DeletionRequestLedger) -> None:
        self.recovery = recovery
        self.sessions = recovery.sessions
        self.ledger = ledger

    @staticmethod
    def _lock(
        session: Session, request: dict[str, Any]
    ) -> tuple[ServerInstanceRow, UserAccountRow]:
        identity = session.get(
            ServerInstanceRow,
            UUID(request["expected_server_instance_id"]),
            with_for_update=True,
            populate_existing=True,
        )
        if identity is None or (
            identity.identity_epoch != request["expected_identity_epoch"]
            or identity.identity_thumbprint_sha256.hex()
            != request["expected_identity_thumbprint_sha256"]
            or identity.api_origin != request["expected_api_origin"]
            or identity.stream_origin != request["expected_stream_origin"]
        ):
            raise AccountDeletionError()
        lock_resource_admission(session)
        user_id = UUID(request["account_id"])
        _acquire_sync_owner_publish_lock(session, user_id)
        account = session.get(UserAccountRow, user_id, with_for_update=True, populate_existing=True)
        if account is None or account.deleted_at is not None:
            raise AccountDeletionError()
        return identity, account

    @staticmethod
    def _require_remaining_owner(session: Session, account: UserAccountRow) -> None:
        if (
            account.role == "OWNER"
            and session.scalar(
                select(UserAccountRow.user_id)
                .where(
                    UserAccountRow.user_id != account.user_id,
                    UserAccountRow.role == "OWNER",
                    UserAccountRow.status == "ACTIVE",
                    UserAccountRow.deleted_at.is_(None),
                )
                .limit(1)
            )
            is None
        ):
            raise AccountDeletionError("last_owner_required")

    def status(self, actor: Principal) -> dict[str, Any]:
        with self.sessions.begin() as session:
            identity = session.scalar(select(ServerInstanceRow).with_for_update())
            lock_resource_admission(session)
            _acquire_sync_owner_publish_lock(session, actor.user_id)
            account = session.get(
                UserAccountRow, actor.user_id, with_for_update=True, populate_existing=True
            )
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise AccountDeletionError()
            self.recovery._actor(session, account, actor)
            locked_owner_history(session, self.ledger, account.user_id)
            credential = session.get(AccountRecoveryCredentialRow, account.user_id)
            configured = (
                credential is not None
                and identity is not None
                and (
                    credential.server_instance_id,
                    credential.identity_epoch,
                    credential.identity_thumbprint_sha256,
                )
                == (
                    identity.server_instance_id,
                    identity.identity_epoch,
                    identity.identity_thumbprint_sha256,
                )
            )
            try:
                self._require_remaining_owner(session, account)
                remaining_owner = True
            except AccountDeletionError:
                remaining_owner = False
            ready = database_now(session) > self.ledger.request_coverage_started_at() + timedelta(
                seconds=240, microseconds=1
            )
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "account_id": str(account.user_id),
                "authority_generation": account.authority_generation,
                "code_generation": credential.generation if credential else 0,
                "can_request": remaining_owner and configured and ready,
                "reason": "last_owner_required"
                if not remaining_owner
                else "recovery_setup_required"
                if not configured
                else "deletion_initializing"
                if not ready
                else None,
            }

    def request(self, actor: Principal, code: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("request", document)
        self.recovery.rate_gate("", UUID(request["account_id"]))
        key = verify_device("request", request)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            if account.status != "ACTIVE":
                raise AccountDeletionError()
            self.recovery._actor(session, account, actor)
            device = session.get(DeviceRow, actor.device_id)
            if device is None or device.public_key != key:
                raise AccountDeletionError()
            now = database_now(session)
            require_fresh(request, now)
            if requested_at(request) <= self.ledger.request_coverage_started_at() + timedelta(
                seconds=120, microseconds=1
            ):
                raise AccountDeletionError("deletion_initializing")
            self._require_remaining_owner(session, account)
            credential = self.recovery._credential(session, identity, account)
            self.recovery._possession(identity, account, credential.verifier_sha256, code)
            if (
                request["expected_code_generation"] != credential.generation
                or request["expected_authority_generation"] != account.authority_generation
            ):
                raise AccountDeletionError("deletion_generation_conflict")
            operation_id = UUID(request["operation_id"])
            history = locked_owner_history(
                session, self.ledger, account.user_id, retry_request=operation_id
            )
            prior = next(
                (item for item in self.ledger.request_read() if item.request_id == operation_id),
                None,
            )
            if prior is not None and (
                prior.owner_tag != self.ledger.owner_tag(account.user_id)
                or prior.request_sha256 != request["request_sha256"]
                or prior.decision != "ATTEMPTED"
                or prior.cancel_operation_id is not None
            ):
                raise AccountDeletionError("operation_conflict")
            if session.get(AccountDeletionRequestRow, operation_id) is not None:
                raise AccountDeletionError("operation_conflict")
            accepted_at = history[operation_id].decided_at if operation_id in history else now
            row = AccountDeletionRequestRow(
                deletion_request_id=operation_id,
                user_id=account.user_id,
                server_instance_id=identity.server_instance_id,
                identity_epoch=identity.identity_epoch,
                identity_thumbprint_sha256=identity.identity_thumbprint_sha256,
                request_sha256=bytes.fromhex(request["request_sha256"]),
                actor_device_id=actor.device_id,
                actor_public_key_spki=key,
                code_generation=credential.generation,
                code_verifier_sha256=credential.verifier_sha256,
                authority_generation=account.authority_generation + 1,
                requested_at=accepted_at,
                cancel_before=accepted_at + timedelta(days=30),
                state="PENDING",
                revision=1,
            )
            self.ledger.request_record(
                DeletionRequestEvidence(
                    self.ledger.owner_tag(account.user_id),
                    operation_id,
                    request["request_sha256"],
                    initial_receipt_sha256(row),
                    "ATTEMPTED",
                    accepted_at,
                )
            )
            revoke_account_authority(session, account, now)
            account.status = "DELETION_PENDING"
            session.add(row)
            session.add(
                AuditEventRow(
                    occurred_at=now,
                    actor_type="USER",
                    actor_user_id=account.user_id,
                    actor_device_id=actor.device_id,
                    action="account_deletion.requested",
                    target_type="USER_ACCOUNT",
                    target_id=account.user_id,
                    request_id=operation_id,
                    reason_code="OWNER_CONFIRMED_DELETION",
                    metadata_sanitized={"revision": 1},
                )
            )
            session.flush()
            return self._result(row, replayed=False)

    def request_receipt(self, code: str, document: dict[str, Any]) -> dict[str, Any]:
        """A revoked key proves only its exact historical request, never new authority."""
        request = parse_request("request", document)
        self.recovery.rate_gate("", UUID(request["account_id"]))
        key = verify_device("request", request)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            locked_owner_history(session, self.ledger, account.user_id)
            row = session.get(
                AccountDeletionRequestRow,
                UUID(request["operation_id"]),
                with_for_update=True,
                populate_existing=True,
            )
            if row is None or (
                row.user_id != account.user_id
                or row.server_instance_id != identity.server_instance_id
                or row.identity_epoch != identity.identity_epoch
                or row.identity_thumbprint_sha256 != identity.identity_thumbprint_sha256
                or row.actor_public_key_spki != key
                or not hmac.compare_digest(row.request_sha256.hex(), request["request_sha256"])
            ):
                raise AccountDeletionError()
            self.recovery._possession(identity, account, row.code_verifier_sha256, code)
            return self._result(row, replayed=True)

    @staticmethod
    def _seal_proof(request: dict[str, Any], verifier: bytes) -> str:
        return canonical_sha256(
            {
                "purpose": "account-deletion-sealed-proof-v1",
                "request_sha256": request["request_sha256"],
                "code_verifier_sha256": verifier.hex(),
            }
        ).hex()

    def request_resolve(self, code: str, document: dict[str, Any]) -> dict[str, Any]:
        """Close only an expired exact request without any independent acceptance attempt."""
        request = parse_request("request", document)
        self.recovery.rate_gate("", UUID(request["account_id"]))
        key = verify_device("request", request)
        operation = UUID(request["operation_id"])
        recorded = next(
            (item for item in self.ledger.request_read() if item.request_id == operation), None
        )
        if recorded is not None and recorded.decision == "SEALED":
            # This proof issues no authority. Current profile/account lifecycle cannot
            # make a lost immutable negative reply forgettable or turn it into acceptance.
            return self._sealed_result(request, code, recorded, replayed=True)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            locked_owner_history(session, self.ledger, account.user_id)
            operation = UUID(request["operation_id"])
            prior = next(
                (item for item in self.ledger.request_read() if item.request_id == operation), None
            )
            if prior is not None and (
                prior.owner_tag != self.ledger.owner_tag(account.user_id)
                or prior.request_sha256 != request["request_sha256"]
            ):
                raise AccountDeletionError("operation_conflict")
            row = session.get(AccountDeletionRequestRow, operation, with_for_update=True)
            if row is not None:
                if (
                    row.user_id != account.user_id
                    or row.server_instance_id != identity.server_instance_id
                    or row.identity_epoch != identity.identity_epoch
                    or row.identity_thumbprint_sha256 != identity.identity_thumbprint_sha256
                    or row.actor_public_key_spki != key
                    or not hmac.compare_digest(row.request_sha256.hex(), request["request_sha256"])
                ):
                    raise AccountDeletionError()
                self.recovery._possession(identity, account, row.code_verifier_sha256, code)
                return self._result(row, replayed=True)
            if prior is not None:
                if prior.decision != "SEALED":
                    raise DeletionEvidenceError()
                item = prior
            else:
                now = database_now(session)
                # Python/PG sample microseconds; original Android proofs may carry
                # nanoseconds. Keep a microsecond margin so the veto is strictly late.
                if now <= requested_at(request) + timedelta(seconds=120, microseconds=1):
                    raise AccountDeletionError("deletion_resolution_not_ready")
                if requested_at(request) <= self.ledger.request_coverage_started_at() + timedelta(
                    seconds=120, microseconds=1
                ):
                    # A previous version could have accepted this request before cutover,
                    # including within its bounded future-timestamp freshness window.
                    raise DeletionEvidenceError()
                credential = self.recovery._credential(session, identity, account)
                self.recovery._possession(identity, account, credential.verifier_sha256, code)
                if (
                    account.status != "ACTIVE"
                    or request["expected_code_generation"] != credential.generation
                    or request["expected_authority_generation"] != account.authority_generation
                    or session.scalar(
                        select(DeviceRow.device_id)
                        .where(
                            DeviceRow.user_id == account.user_id,
                            DeviceRow.public_key == key,
                        )
                        .limit(1)
                    )
                    is None
                ):
                    raise AccountDeletionError()
                item = self.ledger.request_record(
                    DeletionRequestEvidence(
                        self.ledger.owner_tag(account.user_id),
                        operation,
                        request["request_sha256"],
                        self._seal_proof(request, credential.verifier_sha256),
                        "SEALED",
                        now,
                    )
                )
            return self._sealed_result(request, code, item, replayed=prior is not None)

    def _sealed_result(
        self,
        request: dict[str, Any],
        code: str,
        item: DeletionRequestEvidence,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        if (
            item.decision != "SEALED"
            or item.request_id != UUID(request["operation_id"])
            or item.owner_tag != self.ledger.owner_tag(UUID(request["account_id"]))
            or item.request_sha256 != request["request_sha256"]
        ):
            raise AccountDeletionError("operation_conflict")
        verifier = code_verifier(
            UUID(request["expected_server_instance_id"]),
            UUID(request["account_id"]),
            code,
        )
        if not hmac.compare_digest(item.receipt_sha256, self._seal_proof(request, verifier)):
            raise AccountDeletionError()
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "deletion_request_id": request["operation_id"],
            "account_id": request["account_id"],
            "request_sha256": item.request_sha256,
            "state": "NOT_ACCEPTED",
            "resolved_at": iso8601(item.decided_at),
            "replayed": replayed,
        }

    def preview(self, code: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("preview", document)
        self.recovery.rate_gate("", UUID(request["account_id"]))
        verify_device("preview", request)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            locked_owner_history(session, self.ledger, account.user_id)
            now = database_now(session)
            require_fresh(request, now)
            credential = self.recovery._credential(session, identity, account)
            self.recovery._possession(identity, account, credential.verifier_sha256, code)
            row = session.scalar(
                select(AccountDeletionRequestRow)
                .where(
                    AccountDeletionRequestRow.user_id == account.user_id,
                    AccountDeletionRequestRow.state == "PENDING",
                )
                .with_for_update()
            )
            if row is None or account.status != "DELETION_PENDING" or now >= row.cancel_before:
                raise AccountDeletionError()
            return {
                **self._result(row, replayed=False),
                "account_label": account.display_name,
                "code_generation": credential.generation,
                "confirmation_required": True,
            }

    def cancel(self, code: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("cancel", document)
        self.recovery.rate_gate("", UUID(request["account_id"]))
        key = verify_device("cancel", request)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            parent = UUID(request["deletion_request_id"])
            operation_id = UUID(request["operation_id"])
            history = locked_owner_history(
                session,
                self.ledger,
                account.user_id,
                retry_cancel=(parent, operation_id, request["request_sha256"]),
            )
            row = session.get(
                AccountDeletionRequestRow,
                UUID(request["deletion_request_id"]),
                with_for_update=True,
                populate_existing=True,
            )
            if (
                row is None
                or row.user_id != account.user_id
                or row.server_instance_id != identity.server_instance_id
            ):
                raise AccountDeletionError()
            operation_id = UUID(request["operation_id"])
            if row.state == "CANCELLED":
                if row.cancel_operation_id != operation_id or account.status != "ACTIVE":
                    raise AccountDeletionError()
                if (
                    self.recovery._receipt(
                        session, "DELETE_CANCEL", request, account, database_now(session)
                    )
                    is None
                ):
                    # Expired receipt cleanup cannot turn a historical cancel into a new binding.
                    raise AccountDeletionError()
                return self.recovery.replace_binding(
                    session, identity, account, request, key, code, kind="DELETE_CANCEL"
                )
            now = database_now(session)
            if (
                account.status != "DELETION_PENDING"
                or row.state != "PENDING"
                or now >= row.cancel_before
                or row.revision != request["expected_revision"]
            ):
                raise AccountDeletionError()
            # A failure in proof, capacity, binding or receipt persistence rolls this back.
            account.status = "ACTIVE"
            result = self.recovery.replace_binding(
                session, identity, account, request, key, code, kind="DELETE_CANCEL"
            )
            row.state, row.revision = "CANCELLED", row.revision + 1
            evidence = history[parent]
            row.cancelled_at = evidence.cancelled_at or now
            row.cancel_operation_id = operation_id
            session.flush()
            self.ledger.request_record(
                replace(
                    evidence,
                    cancel_operation_id=operation_id,
                    cancel_request_sha256=request["request_sha256"],
                    cancelled_at=row.cancelled_at,
                )
            )
            return result

    def cancel_outcome(self, refresh: str, document: dict[str, Any]) -> dict[str, Any]:
        """Recover one exact committed cancellation after its reply receipt expired."""
        request = parse_request("cancel", document)
        self.recovery.rate_gate("", UUID(request["account_id"]))
        key = verify_device("cancel", request)
        try:
            refresh_bytes = refresh.encode("ascii")
        except UnicodeEncodeError as error:
            raise AccountDeletionError() from error
        if not 32 <= len(refresh_bytes) <= 128:
            raise AccountDeletionError()
        refresh_hash = sha256(refresh_bytes).digest()
        if not hmac.compare_digest(
            refresh_hash, bytes.fromhex(request["next_refresh_token_sha256"])
        ):
            raise AccountDeletionError()
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            parent = UUID(request["deletion_request_id"])
            operation_id = UUID(request["operation_id"])
            locked_owner_history(
                session,
                self.ledger,
                account.user_id,
                retry_cancel=(parent, operation_id, request["request_sha256"]),
            )
            row = session.get(
                AccountDeletionRequestRow,
                parent,
                with_for_update=True,
                populate_existing=True,
            )
            if (
                row is None
                or row.user_id != account.user_id
                or row.server_instance_id != identity.server_instance_id
                or row.state != "CANCELLED"
                or row.cancel_operation_id != operation_id
                or account.status != "ACTIVE"
            ):
                raise AccountDeletionError()
            receipt = self.recovery._exact_receipt(session, "DELETE_CANCEL", request, account)
            if receipt is None or receipt.spent_verifier_sha256 is None:
                raise AccountDeletionError()
            credential = self.recovery._credential(session, identity, account)
            self.recovery._current_receipt(credential, receipt)
            return self.recovery._binding_response(
                session,
                account,
                receipt,
                database_now(session),
                replayed=True,
                expected_key=key,
                expected_refresh_hash=refresh_hash,
                recovered_outcome=True,
            )

    @staticmethod
    def _result(row: AccountDeletionRequestRow, *, replayed: bool) -> dict[str, Any]:
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "deletion_request_id": str(row.deletion_request_id),
            "account_id": str(row.user_id),
            "state": row.state,
            "revision": row.revision,
            "requested_at": iso8601(row.requested_at),
            "cancel_before": iso8601(row.cancel_before),
            "replayed": replayed,
        }
