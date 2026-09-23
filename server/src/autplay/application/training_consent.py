"""Monotonic caller-owned shared-training decisions, separate from personal adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.sona_capture import SonaCaptureBundleRow
from autplay.adapters.postgresql.models.training_consent import (
    TrainingConsentOperationRow,
    TrainingConsentRow,
)
from autplay.adapters.postgresql.models.training_work import TrainingParticipantRow, TrainingRunRow
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import canonical_sha256, iso8601
from autplay.domain.training_consent import (
    TrainingConsentEvidenceError,
    TrainingConsentHistory,
    TrainingConsentIntent,
)
from autplay.ports.training_consent import TrainingConsentLedger

MAX_REVISION = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class TrainingCaptureGrant:
    """Current independent R1B authority held under the caller's account lock."""

    revision: int
    receipt_sha256: str


class TrainingConsentError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TrainingConsentService:
    """Account locks serialize decisions with account deletion and pipeline authority checks."""

    def __init__(self, sessions: sessionmaker[Session], ledger: TrainingConsentLedger) -> None:
        self._sessions = sessions
        self._ledger = ledger

    @staticmethod
    def lock_account(session: Session, user_id: UUID) -> UserAccountRow:
        account = session.get(UserAccountRow, user_id, with_for_update=True, populate_existing=True)
        if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
            raise TrainingConsentError("training_consent_unavailable")
        return account

    @classmethod
    def _actor(cls, session: Session, principal: Principal) -> None:
        account = cls.lock_account(session, principal.user_id)
        device = session.execute(
            select(DeviceRow.user_id, DeviceRow.revoked_at)
            .where(DeviceRow.device_id == principal.device_id)
            .with_for_update()
        ).one_or_none()
        credential = session.get(
            UserSessionRow, principal.session_id, with_for_update=True, populate_existing=True
        )
        now = cls._now(session)
        if (
            principal.role.value != account.role
            or device is None
            or device[0] != account.user_id
            or device[1] is not None
            or credential is None
            or credential.user_id != account.user_id
            or credential.device_id != principal.device_id
            or credential.session_mode != "V2"
            or credential.revoked_at is not None
            or credential.expires_at <= now
        ):
            raise TrainingConsentError("auth_attention_required")

    @staticmethod
    def _now(session: Session) -> datetime:
        value = session.scalar(text("SELECT clock_timestamp()"))
        if not isinstance(value, datetime):
            raise RuntimeError("database clock unavailable")
        return value

    @staticmethod
    def _view(user_id: UUID, row: TrainingConsentRow | None) -> dict[str, object]:
        return {
            "schema_version": 1,
            "account_id": str(user_id),
            "decision": "UNKNOWN" if row is None else row.decision,
            "revision": 0 if row is None else row.revision,
            "policy_version": 1,
            "changed_at": None if row is None else iso8601(row.changed_at),
        }

    def get(self, principal: Principal) -> dict[str, object]:
        with self._sessions.begin() as session:
            self._actor(session, principal)
            row = session.get(TrainingConsentRow, principal.user_id, with_for_update=True)
            history = self._ledger.read()
            if row is not None and row.decision == "GRANTED":
                self._require_evidence(session, principal.user_id, row, history)
            return self._view(principal.user_id, row)

    def _proven_grant(
        self,
        session: Session,
        user_id: UUID,
        row: TrainingConsentRow,
        history: TrainingConsentHistory,
    ) -> bool:
        intent = history.latest.get(self._ledger.owner_tag(user_id))
        if intent is None or intent.decision != "GRANTED" or intent.actor_tag is None:
            return False
        receipt = session.get(
            TrainingConsentOperationRow, intent.operation_id, populate_existing=True
        )
        return (
            receipt is not None
            and receipt.user_id == user_id
            and self._ledger.actor_tag(receipt.actor_device_id) == intent.actor_tag
            and receipt.request_sha256.hex() == intent.request_sha256
            and receipt.applied_decision == intent.decision == row.decision
            and receipt.applied_revision == intent.revision == row.revision
            and receipt.created_at == intent.changed_at == row.changed_at
            and row.policy_version == 1
        )

    def _require_evidence(
        self,
        session: Session,
        user_id: UUID,
        row: TrainingConsentRow,
        history: TrainingConsentHistory,
    ) -> None:
        if not self._proven_grant(session, user_id, row, history):
            raise TrainingConsentError("training_consent_restore_attention")

    def decide(self, principal: Principal, body: dict[str, object]) -> dict[str, object]:
        if set(body) != {
            "operation_id",
            "account_id",
            "expected_revision",
            "decision",
            "policy_version",
        }:
            raise ValueError("invalid consent fields")
        if UUID(str(body["account_id"])) != principal.user_id:
            raise TrainingConsentError("training_consent_unavailable")
        operation = UUID(str(body["operation_id"]))
        revision, policy, decision = (
            body["expected_revision"],
            body["policy_version"],
            body["decision"],
        )
        if (
            type(revision) is not int
            or not 0 <= revision < MAX_REVISION
            or type(policy) is not int
            or policy != 1
            or not isinstance(decision, str)
            or decision not in {"GRANTED", "DENIED", "WITHDRAWN"}
        ):
            raise ValueError("invalid consent command")
        request_hash = canonical_sha256(body)
        with self._sessions.begin() as session:
            if session.scalar(text("SHOW transaction_isolation")) != "read committed":
                raise TrainingConsentError("training_consent_isolation_required")
            self._actor(session, principal)
            # Serialize cross-account operation collisions using a purpose-specific scope.
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 54))"),
                {"key": "training-consent:" + str(operation)},
            )
            row = session.get(
                TrainingConsentRow, principal.user_id, with_for_update=True, populate_existing=True
            )
            receipt = session.get(TrainingConsentOperationRow, operation, populate_existing=True)
            history = self._ledger.read()
            intent = history.operations.get(operation)
            if intent is not None and (
                intent.owner_tag != self._ledger.owner_tag(principal.user_id)
                or intent.actor_tag != self._ledger.actor_tag(principal.device_id)
                or intent.request_sha256 != request_hash.hex()
            ):
                raise TrainingConsentError("operation_conflict")
            if receipt is not None:
                if (
                    receipt.user_id != principal.user_id
                    or receipt.actor_device_id != principal.device_id
                    or receipt.request_sha256 != request_hash
                ):
                    raise TrainingConsentError("operation_conflict")
                if intent is None or (
                    receipt.applied_decision != intent.decision
                    or receipt.applied_revision != intent.revision
                    or receipt.created_at != intent.changed_at
                ):
                    raise TrainingConsentError("training_consent_restore_attention")
                if row is not None and row.decision == "GRANTED":
                    self._require_evidence(session, principal.user_id, row, history)
                return self._result(principal.user_id, row, receipt)
            current = 0 if row is None else row.revision
            previous_hash = canonical_sha256(self._view(principal.user_id, row)).hex()
            if intent is not None and (
                history.latest.get(intent.owner_tag) != intent
                or intent.revision != current + 1
                or intent.previous_policy_sha256 != previous_hash
            ):
                raise TrainingConsentError("training_consent_restore_attention")
            if current >= MAX_REVISION or (
                decision == "GRANTED" and (current != revision or current >= MAX_REVISION - 1)
            ):
                raise TrainingConsentError("consent_revision_conflict")
            if (
                intent is None
                and decision == "GRANTED"
                and row is not None
                and row.decision == "GRANTED"
            ):
                self._require_evidence(session, principal.user_id, row, history)
            # A stale refusal can only make the account more private, never restore a grant.
            applied = (
                "WITHDRAWN"
                if decision != "GRANTED"
                and row is not None
                and row.decision in {"GRANTED", "WITHDRAWN"}
                else decision
            )
            now = self._now(session) if intent is None else intent.changed_at
            if intent is None:
                intent = TrainingConsentIntent(
                    self._ledger.owner_tag(principal.user_id),
                    operation,
                    self._ledger.actor_tag(principal.device_id),
                    request_hash.hex(),
                    previous_hash,
                    applied,
                    current + 1,
                    now,
                )
                # Durable external intent comes first. A PG rollback never removes its barrier.
                self._ledger.record(intent)
            elif intent.decision != applied:
                raise TrainingConsentError("training_consent_restore_attention")
            if row is None:
                row = TrainingConsentRow(
                    user_id=principal.user_id,
                    decision=applied,
                    revision=1,
                    policy_version=1,
                    changed_at=now,
                )
                session.add(row)
            else:
                row.decision, row.revision, row.changed_at = applied, current + 1, now
            receipt = TrainingConsentOperationRow(
                operation_id=operation,
                user_id=principal.user_id,
                actor_device_id=principal.device_id,
                request_sha256=request_hash,
                applied_decision=applied,
                applied_revision=current + 1,
                created_at=now,
            )
            session.add(receipt)
            session.flush()
            if applied != "GRANTED" and session.scalar(
                text("SELECT to_regclass('ml.sona_capture_bundle') IS NOT NULL")
            ):
                # The independent private intent was durable before this transaction.
                # Remove native shadow inputs under the same account lock as withdrawal.
                session.execute(
                    delete(SonaCaptureBundleRow).where(
                        SonaCaptureBundleRow.user_id == principal.user_id
                    )
                )
            return self._result(principal.user_id, row, receipt)

    @classmethod
    def _result(
        cls, user_id: UUID, row: TrainingConsentRow | None, receipt: TrainingConsentOperationRow
    ) -> dict[str, object]:
        # Replay reports current policy, so an old grant receipt cannot resurrect the UI's switch.
        return {
            **cls._view(user_id, row),
            "operation_id": str(receipt.operation_id),
            "applied_revision": receipt.applied_revision,
            "applied_decision": receipt.applied_decision,
        }

    def require_granted(
        self,
        session: Session,
        user_id: UUID,
        expected_revision: int,
        *,
        history: TrainingConsentHistory | None = None,
    ) -> None:
        """Hold the same account lock through preparation/start/publication authority commits."""
        self.lock_account(session, user_id)
        row = session.get(TrainingConsentRow, user_id, with_for_update=True, populate_existing=True)
        if (
            row is None
            or row.decision != "GRANTED"
            or row.revision != expected_revision
            or row.policy_version != 1
        ):
            raise TrainingConsentError("training_consent_required")
        self._require_evidence(session, user_id, row, history or self._ledger.read())

    def capture_grant(self, session: Session, user_id: UUID) -> TrainingCaptureGrant | None:
        """Return a provenance-bound receipt only for a proven current R1B grant.

        The caller must keep this transaction open through native P11 capture.
        A concurrent withdrawal uses the same account lock and cannot cross it.
        """

        if not session.in_transaction():
            raise TrainingConsentError("training_consent_transaction_required")
        self.lock_account(session, user_id)
        row = session.get(TrainingConsentRow, user_id, with_for_update=True, populate_existing=True)
        if row is None or row.decision != "GRANTED":
            return None
        try:
            history = self._ledger.read()
        except TrainingConsentEvidenceError:
            return None
        if not self._proven_grant(session, user_id, row, history):
            return None
        intent = history.latest[self._ledger.owner_tag(user_id)]
        return TrainingCaptureGrant(
            revision=row.revision,
            receipt_sha256=canonical_sha256(
                {
                    "kind": "SONA_R1B_TRAINING_CAPTURE_CONSENT_RECEIPT_V1",
                    "owner_user_id": str(user_id),
                    "operation_id": str(intent.operation_id),
                    "actor_tag": intent.actor_tag,
                    "request_sha256": intent.request_sha256,
                    "revision": intent.revision,
                    "changed_at": iso8601(intent.changed_at),
                }
            ).hex(),
        )

    def restore_guard(self) -> int:
        """Conservatively retire unmatched grants using ordinary +1 private policy mutations.

        A system barrier is independent sequence authority, never a user's operation receipt.
        A crash before a grant commit may therefore require another explicit user decision.
        """
        cursor: UUID | None = None
        reconciled = 0
        while True:
            with self._sessions.begin() as session:
                if session.scalar(text("SHOW transaction_isolation")) != "read committed":
                    raise TrainingConsentError("training_consent_isolation_required")
                # Same identity -> sorted account/policy -> run order as training and purge.
                list(
                    session.scalars(
                        select(ServerInstanceRow)
                        .order_by(ServerInstanceRow.server_instance_id)
                        .with_for_update()
                    )
                )
                query = select(UserAccountRow).order_by(UserAccountRow.user_id).limit(256)
                if cursor is not None:
                    query = query.where(UserAccountRow.user_id > cursor)
                accounts = list(session.scalars(query.with_for_update()))
                if not accounts:
                    self._ledger.read()
                    return reconciled
                policies = {
                    account.user_id: session.get(
                        TrainingConsentRow,
                        account.user_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                    for account in accounts
                }
                history = self._ledger.read()
                has_capture_table = bool(
                    session.scalar(text("SELECT to_regclass('ml.sona_capture_bundle') IS NOT NULL"))
                )
                # One batch may share several runs with an owner outside the batch. Lock their
                # complete union in UUID order before any per-owner invalidation callback.
                list(
                    session.scalars(
                        select(TrainingRunRow)
                        .where(
                            TrainingRunRow.run_id.in_(
                                select(TrainingParticipantRow.run_id).where(
                                    TrainingParticipantRow.user_id.in_(
                                        [account.user_id for account in accounts]
                                    )
                                )
                            )
                        )
                        .order_by(TrainingRunRow.run_id)
                        .with_for_update()
                    )
                )
                for account in accounts:
                    row = policies[account.user_id]
                    latest = history.latest.get(self._ledger.owner_tag(account.user_id))
                    if (
                        account.status == "ACTIVE"
                        and account.deleted_at is None
                        and row is not None
                        and row.decision == "GRANTED"
                        and self._proven_grant(session, account.user_id, row, history)
                    ):
                        continue
                    if has_capture_table:
                        # A restored row without a current independent grant cannot retain
                        # any native shadow input, even if its old policy was already private.
                        session.execute(
                            delete(SonaCaptureBundleRow).where(
                                SonaCaptureBundleRow.user_id == account.user_id
                            )
                        )
                    if (
                        row is not None
                        and row.decision != "GRANTED"
                        and (latest is None or latest.decision != "GRANTED")
                    ):
                        # Even already-private rows must retire restored unfinished contributions.
                        session.execute(
                            text("SELECT account.invalidate_training_contributions(:u)"),
                            {"u": account.user_id},
                        )
                        continue
                    if row is None and latest is None:
                        continue
                    current = 0 if row is None else row.revision
                    target = min(current + 1, MAX_REVISION)
                    now = self._now(session)
                    if row is not None:
                        now = max(now, row.changed_at)
                    decision = "DENIED" if row is None else "WITHDRAWN"
                    previous_hash = canonical_sha256(self._view(account.user_id, row)).hex()
                    operation = uuid4()
                    if latest is None or latest.decision == "GRANTED":
                        self._ledger.record(
                            TrainingConsentIntent(
                                self._ledger.owner_tag(account.user_id),
                                operation,
                                None,
                                canonical_sha256(
                                    {
                                        "restore_barrier": str(operation),
                                        "previous_policy_sha256": previous_hash,
                                    }
                                ).hex(),
                                previous_hash,
                                decision,
                                target,
                                now,
                            )
                        )
                    if row is None:
                        session.add(
                            TrainingConsentRow(
                                user_id=account.user_id,
                                decision=decision,
                                revision=target,
                                policy_version=1,
                                changed_at=now,
                            )
                        )
                    elif current < MAX_REVISION:
                        row.decision, row.revision, row.changed_at = decision, target, now
                    else:
                        session.execute(
                            text("SELECT account.invalidate_training_contributions(:u)"),
                            {"u": account.user_id},
                        )
                    session.flush()
                    reconciled += 1
                cursor = accounts[-1].user_id
