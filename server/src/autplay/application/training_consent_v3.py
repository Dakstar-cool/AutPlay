"""Metered source3 current grant from one independent global ledger snapshot.

The old service and public/default composition are unchanged. A private caller
must supply its existing creation or connected-source work lease before reading
any SQL or independent evidence. No provision/repair operation occurs here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models.sona_capture import SonaEventErasureReceiptRow
from autplay.adapters.postgresql.models.training_consent import (
    TrainingConsentOperationRow,
    TrainingConsentRow,
)
from autplay.application.training_consent import TrainingCaptureGrant, TrainingConsentError
from autplay.domain.native_original_capture_v3 import NativeOriginalCaptureV3DigestMeter
from autplay.domain.profile_pairing import canonical_sha256, iso8601
from autplay.domain.training_consent_v3 import NativeTrainingConsentSnapshotV3
from autplay.ports.training_consent_v3 import (
    NativeConsentSnapshotAdmitterV3,
    TrainingConsentLedgerV3,
)


def _row_work(meter: NativeOriginalCaptureV3DigestMeter, *, rows: int = 1) -> None:
    # These queries project only fixed consent/receipt/floor scalar columns.
    # Budget precedes ORM/driver hydration; no unbounded personal JSON is read.
    meter.reserve(byte_work_bound=4096 * rows, node_visit_bound=64 * rows)
    meter.charge(bytes_read=4096 * rows, node_visits=64 * rows)


class NativeTrainingConsentServiceV3:
    """An explicit private authority; a historical root is never a current grant."""

    def __init__(self, ledger: TrainingConsentLedgerV3) -> None:
        self._ledger = ledger

    @contextmanager
    def capture_grant_v3(
        self,
        session: Session,
        owner: UUID,
        *,
        meter: NativeOriginalCaptureV3DigestMeter,
        admit_snapshot: NativeConsentSnapshotAdmitterV3,
    ) -> Iterator[TrainingCaptureGrant | None]:
        """Hold the returned receipt digest on the caller's lease until finalization."""
        if not callable(admit_snapshot):
            raise TrainingConsentError("training_consent_admission_required")
        if type(owner) is not UUID or not session.in_transaction():
            raise TrainingConsentError("training_consent_transaction_required")
        owned = 0
        grant: TrainingCaptureGrant | None = None
        error: BaseException | None = None
        try:
            # SQL hash bytes/hex and receipt crypto overlap under512; the new
            # receipt SHA owns a separate64 until this context exits.
            meter.retain_digests(576)
            owned = 576
            grant = self._capture_value(session, owner, meter, admit_snapshot)
            released = 512 if grant is not None else 576
            meter.release_digests(released)
            owned -= released
            yield grant
        except BaseException as caught:
            error = caught
        finally:
            grant = None
            if error is not None:
                error.__traceback__ = error.__context__ = error.__cause__ = None
            if owned:
                meter.release_digests(owned)
        if error is not None:
            raise error

    def _capture_value(
        self,
        session: Session,
        owner: UUID,
        meter: NativeOriginalCaptureV3DigestMeter,
        admit_snapshot: NativeConsentSnapshotAdmitterV3,
    ) -> TrainingCaptureGrant | None:
        _row_work(meter)
        present = session.execute(
            text(
                "SELECT user_id FROM account.user_account WHERE user_id=:owner "
                "AND status='ACTIVE' AND deleted_at IS NULL FOR UPDATE"
            ),
            {"owner": owner},
        ).scalar_one_or_none()
        if present != owner:
            raise TrainingConsentError("training_consent_unavailable")
        _row_work(meter)
        row = session.get(TrainingConsentRow, owner, with_for_update=True, populate_existing=True)
        if (
            row is None
            or row.decision != "GRANTED"
            or row.policy_version != 1
            or type(row.revision) is not int
            or not 1 <= row.revision < 9_007_199_254_740_991
        ):
            return None
        with self._ledger.read_snapshot_v3(meter=meter, admit_snapshot=admit_snapshot) as snapshot:
            return self._grant_from_snapshot(session, owner, row, snapshot, meter)

    def _grant_from_snapshot(
        self,
        session: Session,
        owner: UUID,
        row: TrainingConsentRow,
        snapshot: NativeTrainingConsentSnapshotV3,
        meter: NativeOriginalCaptureV3DigestMeter,
    ) -> TrainingCaptureGrant | None:
        intent: Any = None
        receipt: Any = None
        owner_tag = actor_tag = ""
        try:
            with self._ledger.owner_tag_v3(owner, meter=meter) as owner_tag:
                try:
                    intent = snapshot.history.latest.get(owner_tag)
                    if intent is None or intent.decision != "GRANTED" or intent.actor_tag is None:
                        return None
                    _row_work(meter)
                    receipt = session.get(
                        TrainingConsentOperationRow, intent.operation_id, populate_existing=True
                    )
                    if receipt is None:
                        return None
                    with self._ledger.actor_tag_v3(
                        receipt.actor_device_id, meter=meter
                    ) as actor_tag:
                        try:
                            if (
                                receipt.user_id != owner
                                or actor_tag != intent.actor_tag
                                or receipt.request_sha256.hex() != intent.request_sha256
                                or receipt.applied_decision != intent.decision
                                or receipt.applied_decision != row.decision
                                or receipt.applied_revision != intent.revision
                                or receipt.applied_revision != row.revision
                                or receipt.created_at != intent.changed_at
                                or receipt.created_at != row.changed_at
                            ):
                                return None
                        finally:
                            actor_tag = ""
                    if not self._reconciled(session, owner, owner_tag, snapshot, meter):
                        return None
                    meter.reserve(byte_work_bound=3072, node_visit_bound=64)
                    meter.charge(
                        bytes_read=1024, bytes_written=1024, sha_input_bytes=1024, node_visits=64
                    )
                    return TrainingCaptureGrant(
                        revision=row.revision,
                        receipt_sha256=canonical_sha256(
                            {
                                "kind": "SONA_R1B_TRAINING_CAPTURE_CONSENT_RECEIPT_V1",
                                "owner_user_id": str(owner),
                                "operation_id": str(intent.operation_id),
                                "actor_tag": intent.actor_tag,
                                "request_sha256": intent.request_sha256,
                                "revision": intent.revision,
                                "changed_at": iso8601(intent.changed_at),
                            }
                        ).hex(),
                    )
                finally:
                    owner_tag = ""
        finally:
            intent = receipt = None

    @staticmethod
    def _reconciled(
        session: Session,
        owner: UUID,
        owner_tag: str,
        snapshot: NativeTrainingConsentSnapshotV3,
        meter: NativeOriginalCaptureV3DigestMeter,
    ) -> bool:
        intent: Any = None
        floor: Any = None
        receipt: Any = None
        current: Any = None
        try:
            count = len(snapshot.erasures) + len(snapshot.floors)
            meter.reserve(byte_work_bound=128 * count, node_visit_bound=16 * count)
            meter.charge(bytes_read=128 * count, node_visits=16 * count)
            for intent in snapshot.erasures:
                if intent.owner_tag != owner_tag:
                    continue
                _row_work(meter)
                receipt = session.get(
                    SonaEventErasureReceiptRow,
                    (owner, intent.source_event_id),
                    populate_existing=True,
                    with_for_update=True,
                )
                if (
                    receipt is None
                    or receipt.erasure_event_id != intent.erasure_event_id
                    or receipt.source_server_sequence != intent.source_server_sequence
                    or receipt.source_watermark != intent.source_watermark
                    or receipt.ledger_sequence != intent.ledger_sequence
                    or receipt.ledger_digest.hex() != intent.ledger_digest
                ):
                    return False
            for floor in snapshot.floors:
                if floor.owner_tag != owner_tag:
                    continue
                _row_work(meter)
                current = session.execute(
                    text(
                        "SELECT floor_sequence,ledger_sequence,ledger_digest "
                        "FROM sync.owner_erasure_floor WHERE user_id=:owner FOR UPDATE"
                    ),
                    {"owner": owner},
                ).one_or_none()
                if (
                    current is None
                    or current.floor_sequence != floor.floor_sequence
                    or current.ledger_sequence != floor.ledger_sequence
                    or type(current.ledger_digest) is not bytes
                    or current.ledger_digest.hex() != floor.ledger_digest
                ):
                    return False
            return True
        finally:
            intent = floor = receipt = current = None
