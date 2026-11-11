"""Required read-only reservation dependency, explicitly owned by a session factory.

Composition must inject the SAME independently owned, validated request ledger and
pure readiness snapshot used by deletion/restore. An absent dependency fails closed,
including with feature switches off. This source does not mount that dependency.
The ledger read can wait: every issuer must sample its final authority/freshness/
expiry clock AFTER reservation reads, before effects or grants. Common integration
also owns sanitized HTTP 503 handling of DeletionEvidenceError at every caller.
No SQL shadow, network I/O, new ledger layout/retention or process-global registry.

Repeated global-ID omission research (2026-10-16): one injected guard is selected;
duplicating family lists repeats the omission; a SQL tombstone registry would change
storage/retention; random UUIDs cannot establish global uniqueness. PostgreSQL's
transaction advisory lock supplies serialization that MVCC reads alone cannot:
https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS
https://www.postgresql.org/docs/18/transaction-iso.html
Unique constraints would need a shared SQL owner; UUID generation is not that owner:
https://www.postgresql.org/docs/18/ddl-constraints.html#DDL-CONSTRAINTS-UNIQUE-CONSTRAINTS
https://www.postgresql.org/docs/18/datatype-uuid.html
Explicit per-factory injection uses the documented Session.info dictionary:
https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.info
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence
from autplay.ports.privacy_deletion import DeletionRequestLedger

from .resource_limits import holds_resource_admission_lock

RETAINED_DELETION_RESERVATIONS_INFO_KEY = "autplay.retained_deletion_reservations"
_MAX_RETAINED_REQUESTS = 200_000  # Existing independent ledger event ceiling.


@dataclass(frozen=True, slots=True)
class DeletionReservationReplay:
    """Already verified original facts; an ID alone never excludes a reservation.

    Request retry additionally compares the complete initial private receipt digest.
    Cancel retry compares the exact original cancel digest and parent owner. This
    is only a collision exception, never proof/current authority or a SEALED bypass.
    Callers still verify signatures, code/refresh, full originals and live results.
    """

    owner_id: UUID
    request_id: UUID
    request_sha256: str
    receipt_sha256: str | None = None
    cancel_operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RetainedDeletionReservations:
    ledger: DeletionRequestLedger
    evidence_ready: Callable[[], bool]

    def reserved(
        self,
        session: Session,
        identifier: UUID,
        *,
        replay: DeletionReservationReplay | None = None,
    ) -> bool:
        # All request/cancel ledger writes take M5 -> admission -> sync-owner ->
        # account -> file. Keep admission until the issuer transaction ends. A new
        # deletion cannot enter between this fresh ledger read and issuance/commit.
        # Never cache across calls: this same transaction can append a deletion.
        if not holds_resource_admission_lock(session) or not self.evidence_ready():
            raise DeletionEvidenceError()
        records = self.ledger.request_read()  # Adapter validates both keyed chains/head/identity.
        if type(records) is not tuple or len(records) > _MAX_RETAINED_REQUESTS:
            raise DeletionEvidenceError()
        seen: set[UUID] = set()
        collision = False
        for item in records:
            if (
                type(item) is not DeletionRequestEvidence
                or type(item.request_id) is not UUID
                or item.request_id in seen
                or item.decision not in {"ATTEMPTED", "SEALED"}
                or not all(
                    type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in (item.owner_tag, item.request_sha256, item.receipt_sha256)
                )
                or item.receipt_sha256 == "0" * 64
                or type(item.decided_at) is not datetime
                or item.decided_at.utcoffset() != timedelta(0)
            ):
                raise DeletionEvidenceError()
            seen.add(item.request_id)
            if item.cancel_operation_id is None:
                if item.cancel_request_sha256 is not None or item.cancelled_at is not None:
                    raise DeletionEvidenceError()
            else:
                if (
                    type(item.cancel_operation_id) is not UUID
                    or item.cancel_operation_id in seen
                    or item.decision != "ATTEMPTED"
                    or type(item.cancelled_at) is not datetime
                    or item.cancelled_at.utcoffset() != timedelta(0)
                    or not item.decided_at
                    <= item.cancelled_at
                    < item.decided_at + timedelta(days=30)
                    or type(item.cancel_request_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", item.cancel_request_sha256) is None
                ):
                    raise DeletionEvidenceError()
                seen.add(item.cancel_operation_id)
            if identifier not in {item.request_id, item.cancel_operation_id}:
                continue
            exact = (
                type(replay) is DeletionReservationReplay
                and item.decision == "ATTEMPTED"
                and item.request_id == replay.request_id
                and item.owner_tag == self.ledger.owner_tag(replay.owner_id)
            )
            if exact and replay is not None:
                if (
                    identifier == item.request_id
                    and replay.cancel_operation_id is None
                    and item.cancel_operation_id is None
                    and item.request_sha256 == replay.request_sha256
                    and item.receipt_sha256 == replay.receipt_sha256
                ):
                    continue
                if (
                    identifier == item.cancel_operation_id == replay.cancel_operation_id
                    and item.cancel_request_sha256 == replay.request_sha256
                ):
                    continue
            collision = True
        if not self.evidence_ready():
            raise DeletionEvidenceError()
        return collision


def retained_deletion_identifier_reserved(
    session: Session,
    identifier: UUID,
    *,
    replay: DeletionReservationReplay | None = None,
) -> bool:
    guard = session.info.get(RETAINED_DELETION_RESERVATIONS_INFO_KEY)
    if type(guard) is not RetainedDeletionReservations:
        raise DeletionEvidenceError()
    return guard.reserved(session, identifier, replay=replay)


def configure_retained_deletion_reservations(
    sessions: sessionmaker[Session],
    *,
    ledger: DeletionRequestLedger,
    evidence_ready: Callable[[], bool],
) -> None:
    """Composition-time injection for ALL future sessions, before accepting work.

    Preserve unrelated info. This does not retrofit already-open sessions or claim
    startup/restore readiness; the supplied callback must be a pure owned snapshot.
    """
    info = dict(sessions.kw.get("info", {}))
    info[RETAINED_DELETION_RESERVATIONS_INFO_KEY] = RetainedDeletionReservations(
        ledger, evidence_ready
    )
    sessions.configure(info=info)
