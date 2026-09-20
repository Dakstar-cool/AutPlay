"""Independent early decisions fence absent receipts and cancellation backup branches."""

from collections.abc import Mapping
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import UserAccountRow
from autplay.adapters.postgresql.models.account_deletion import (
    AccountDeletionRequestRow,
    AccountPurgeReceiptRow,
)
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence
from autplay.domain.profile_pairing import canonical_sha256, iso8601
from autplay.ports.privacy_deletion import DeletionRequestLedger


def initial_receipt_sha256(row: AccountDeletionRequestRow) -> str:
    """Include immutable private authority facts, not only the public receipt."""
    return canonical_sha256(
        {
            "purpose": "account-deletion-acceptance-v1",
            "request_id": str(row.deletion_request_id),
            "user_id": str(row.user_id),
            "server_instance_id": str(row.server_instance_id),
            "identity_epoch": row.identity_epoch,
            "identity_thumbprint_sha256": row.identity_thumbprint_sha256.hex(),
            "request_sha256": row.request_sha256.hex(),
            "actor_device_id": str(row.actor_device_id),
            "actor_public_key_spki_sha256": sha256(row.actor_public_key_spki).hexdigest(),
            "code_generation": row.code_generation,
            "code_verifier_sha256": row.code_verifier_sha256.hex(),
            "authority_generation": row.authority_generation,
            "requested_at": iso8601(row.requested_at),
            "cancel_before": iso8601(row.cancel_before),
        }
    ).hex()


def validate_row(row: AccountDeletionRequestRow, evidence: DeletionRequestEvidence) -> None:
    if (
        evidence.decision != "ATTEMPTED"
        or evidence.request_id != row.deletion_request_id
        or evidence.request_sha256 != row.request_sha256.hex()
        or evidence.receipt_sha256 != initial_receipt_sha256(row)
        or evidence.decided_at != row.requested_at
    ):
        raise DeletionEvidenceError()


def validate_cancel(row: AccountDeletionRequestRow, evidence: DeletionRequestEvidence) -> None:
    if (evidence.cancel_operation_id is None and row.state == "CANCELLED") or (
        evidence.cancel_operation_id is not None
        and (
            row.state != "CANCELLED"
            or row.cancel_operation_id != evidence.cancel_operation_id
            or row.cancelled_at != evidence.cancelled_at
        )
    ):
        raise DeletionEvidenceError()


def locked_owner_history(
    session: Session,
    ledger: DeletionRequestLedger,
    owner: UUID,
    *,
    retry_request: UUID | None = None,
    retry_cancel: tuple[UUID, UUID, str] | None = None,
    retained_history: Mapping[UUID, DeletionRequestEvidence] | None = None,
) -> Mapping[UUID, DeletionRequestEvidence]:
    """Caller already holds identity, admission, sync-owner and account lifecycle locks."""
    rows = {
        row.deletion_request_id: row
        for row in session.scalars(
            select(AccountDeletionRequestRow)
            .where(AccountDeletionRequestRow.user_id == owner)
            .order_by(AccountDeletionRequestRow.deletion_request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    history = (
        retained_history
        if retained_history is not None
        else {
            item.request_id: item
            for item in ledger.request_read()
            if item.owner_tag == ledger.owner_tag(owner)
        }
    )
    if any(operation not in history for operation in rows):
        raise DeletionEvidenceError()
    for operation, item in history.items():
        row = rows.get(operation)
        if item.decision == "SEALED":
            if row is not None:
                raise DeletionEvidenceError()
            continue
        if row is None:
            if operation == retry_request and item.cancel_operation_id is None:
                continue
            raise DeletionEvidenceError()
        validate_row(row, item)
        if (
            retry_cancel is not None
            and retry_cancel == (operation, item.cancel_operation_id, item.cancel_request_sha256)
            and row.state == "PENDING"
        ):
            continue
        validate_cancel(row, item)
    return history


def enforce_request_restore_guard(
    sessions: sessionmaker[Session],
    ledger: DeletionRequestLedger,
) -> None:
    """No missing attempt is silently adopted or converted into a negative after restore."""
    ledger.request_read()
    with sessions.begin() as session:
        if session.scalar(text("SHOW transaction_isolation")) != "read committed":
            raise DeletionEvidenceError()
        session.execute(
            select(ServerInstanceRow)
            .order_by(ServerInstanceRow.server_instance_id)
            .with_for_update()
        ).all()
        lock_resource_admission(session)
        history = ledger.request_read()
        by_owner: dict[str, dict[UUID, DeletionRequestEvidence]] = {}
        for item in history:
            by_owner.setdefault(item.owner_tag, {})[item.request_id] = item
        seen: set[str] = set()
        cursor: UUID | None = None
        while True:
            query = select(UserAccountRow.user_id).order_by(UserAccountRow.user_id).limit(256)
            if cursor is not None:
                query = query.where(UserAccountRow.user_id > cursor)
            page = tuple(session.scalars(query))
            if not page:
                break
            for owner in page:
                _acquire_sync_owner_publish_lock(session, owner)
                session.get(UserAccountRow, owner, with_for_update=True, populate_existing=True)
                tag = ledger.owner_tag(owner)
                if tag in by_owner:
                    seen.add(tag)
                locked_owner_history(session, ledger, owner, retained_history=by_owner.get(tag, {}))
            cursor = page[-1]
        # Also inspect entries whose owner/request was entirely absent from this backup.
        completed = {
            item.owner_tag: item for item in ledger.read() if item.completed_at is not None
        }
        for item in history:
            if item.decision == "SEALED" or item.owner_tag in seen:
                continue
            final = completed.get(item.owner_tag)
            receipt = session.get(AccountPurgeReceiptRow, final.request_id) if final else None
            # A restore purge verifies zero-owner again; its row count/time describe
            # that restored branch, not the original purge's independently retained metadata.
            if final is None or receipt is None:
                raise DeletionEvidenceError()
