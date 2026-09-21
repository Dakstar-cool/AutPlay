"""Resume accepted purge and reapply independent evidence before serving a restored backup."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import UserAccountRow
from autplay.adapters.postgresql.models.account_deletion import (
    AccountDeletionRequestRow,
    AccountPurgeReceiptRow,
)
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.account_recovery import database_now
from autplay.application.deletion_request_evidence import (
    enforce_request_restore_guard,
    locked_owner_history,
)
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.privacy_deletion import DeletionEvidence, DeletionEvidenceError
from autplay.ports.privacy_deletion import DeletionLedger


class PrivacyDeletionService:
    def __init__(self, sessions: sessionmaker[Session], ledger: DeletionLedger) -> None:
        self.sessions, self.ledger = sessions, ledger
        self._due_cursor: tuple[datetime, UUID] | None = None

    @staticmethod
    def _lock(session: Session, user_id: UUID) -> None:
        session.execute(
            select(ServerInstanceRow)
            .order_by(ServerInstanceRow.server_instance_id)
            .with_for_update()
        ).all()
        lock_resource_admission(session)
        _acquire_sync_owner_publish_lock(session, user_id)

    def purge(self, user_id: UUID, request_id: UUID) -> DeletionEvidence:
        """Commit noncancellable intent, then external evidence, then the database purge."""
        # Validate independent evidence before making even the noncancellable transition.
        known = next(
            (
                item
                for item in self.ledger.read()
                if item.owner_tag == self.ledger.owner_tag(user_id)
                and item.request_id == request_id
            ),
            None,
        )
        with self.sessions.begin() as session:
            self._lock(session, user_id)
            if session.get(UserAccountRow, user_id, with_for_update=True) is not None:
                locked_owner_history(session, self.ledger, user_id)
            row = session.get(AccountDeletionRequestRow, request_id, with_for_update=True)
            if row is None and known is not None:
                accepted_at = known.accepted_at
            elif row is None or row.user_id != user_id or row.state not in {"PENDING", "PURGING"}:
                raise DeletionEvidenceError()
            else:
                now = database_now(session)
                if now < row.cancel_before:
                    raise DeletionEvidenceError()
                session.execute(
                    text("SELECT account.verify_account_purge_ready(:owner)"), {"owner": user_id}
                )
                if row.state == "PENDING":
                    row.state, row.revision, row.purge_started_at = "PURGING", 2, now
                if row.purge_started_at is None:
                    raise DeletionEvidenceError()
                accepted_at = row.purge_started_at
        evidence = self.ledger.prepare(user_id, request_id, accepted_at)
        return self._finish(user_id, evidence, restore=False)

    def _finish(
        self, user_id: UUID, evidence: DeletionEvidence, *, restore: bool
    ) -> DeletionEvidence:
        with self.sessions.begin() as session:
            self._lock(session, user_id)
            exists = session.get(UserAccountRow, user_id, with_for_update=True)
            if exists is not None:
                if not restore:
                    locked_owner_history(session, self.ledger, user_id)
                elif any(
                    item.owner_tag == evidence.owner_tag
                    and item.request_id == evidence.request_id
                    and (item.cancel_operation_id is not None or item.decision != "ATTEMPTED")
                    for item in self.ledger.request_read()
                ):
                    # An ambiguous/cancelled early intent can never be overridden by restore purge.
                    raise DeletionEvidenceError()
                if restore:
                    # Only the trusted restore process has INSERT permission on this private table.
                    # The caller matched this account against verified independent HMAC evidence.
                    session.execute(
                        text("""
                        INSERT INTO app_private.privacy_restore_authorization
                        VALUES(pg_current_xact_id(),:owner,:request)
                    """),
                        {"owner": user_id, "request": evidence.request_id},
                    )
                session.execute(
                    text("SELECT account.purge_account(:owner,:request)"),
                    {"owner": user_id, "request": evidence.request_id},
                )
                if restore:
                    session.execute(
                        text(
                            "DELETE FROM app_private.privacy_restore_authorization "
                            "WHERE transaction_id=pg_current_xact_id()"
                        )
                    )
            session.execute(
                text("SELECT account.verify_owner_absent(:owner,'{}'::uuid[])"), {"owner": user_id}
            )
            receipt = session.get(AccountPurgeReceiptRow, evidence.request_id)
            if receipt is None:
                raise DeletionEvidenceError()
            removed, completed = receipt.removed_rows, receipt.completed_at
        # If this write or its response fails, independent PREPARED still prevents resurrection.
        return self.ledger.complete(evidence.owner_tag, evidence.request_id, completed, removed)

    def restore_guard(self) -> int:
        """No API/worker starts until every matching independently deleted owner is absent."""
        evidence = {item.owner_tag: item for item in self.ledger.read()}
        restored = 0
        cursor: UUID | None = None
        while True:
            with self.sessions.begin() as session:
                query = select(UserAccountRow.user_id).order_by(UserAccountRow.user_id).limit(256)
                if cursor is not None:
                    query = query.where(UserAccountRow.user_id > cursor)
                owners = tuple(session.scalars(query))
            if not owners:
                break
            for owner in owners:
                if item := evidence.get(self.ledger.owner_tag(owner)):
                    self._finish(owner, item, restore=True)
                    restored += 1
            cursor = owners[-1]
        self._complete_receipts(tuple(evidence.values()))
        # Every completed branch needs its SQL zero-owner transaction. Missing owners
        # in an older backup cannot manufacture a completion without that receipt.
        for item in self.ledger.read():
            with self.sessions.begin() as session:
                receipt = session.get(AccountPurgeReceiptRow, item.request_id)
                if receipt is None:
                    raise DeletionEvidenceError()
        enforce_request_restore_guard(self.sessions, self.ledger)
        return restored

    def _complete_receipts(self, evidence: tuple[DeletionEvidence, ...]) -> None:
        # Finish a lost external completion write after a committed database purge.
        for item in evidence:
            if item.completed_at is not None:
                continue
            with self.sessions.begin() as session:
                receipt = session.get(AccountPurgeReceiptRow, item.request_id)
                completion = (receipt.completed_at, receipt.removed_rows) if receipt else None
            if completion is not None:
                self.ledger.complete(item.owner_tag, item.request_id, *completion)

    def run_due(self, limit: int = 10) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("privacy purge batch must be within 1..100")
        self._complete_receipts(self.ledger.read())
        with self.sessions.begin() as session:
            query = (
                select(
                    AccountDeletionRequestRow.user_id,
                    AccountDeletionRequestRow.deletion_request_id,
                    AccountDeletionRequestRow.cancel_before,
                )
                .where(
                    AccountDeletionRequestRow.state.in_(("PENDING", "PURGING")),
                    AccountDeletionRequestRow.cancel_before <= database_now(session),
                )
                .order_by(
                    AccountDeletionRequestRow.cancel_before,
                    AccountDeletionRequestRow.deletion_request_id,
                )
                .limit(limit)
            )
            if self._due_cursor is not None:
                query = query.where(
                    tuple_(
                        AccountDeletionRequestRow.cancel_before,
                        AccountDeletionRequestRow.deletion_request_id,
                    )
                    > self._due_cursor
                )
            rows = session.execute(query).all()
        if not rows:
            self._due_cursor = None
        completed = 0
        for user_id, request_id, deadline in rows:
            try:
                self.purge(user_id, request_id)
            except DBAPIError as error:
                message = getattr(getattr(error.orig, "diag", None), "message_primary", None)
                if message not in {
                    "privacy_deletion_held",
                    "privacy_process_closure_required",
                    "privacy_staging_cleanup_required",
                }:
                    raise
            else:
                completed += 1
            # A held oldest account must not starve later owners; cycle after reaching the end.
            self._due_cursor = (deadline, request_id)
        return completed
