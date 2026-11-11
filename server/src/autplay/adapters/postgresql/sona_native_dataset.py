"""Bounded counts-only native dataset audit; no owner payload leaves the process."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models.sona_native_capture import SonaNativeCaptureRow
from autplay.application.sona_native_dataset import (
    MAX_BATCH_BYTES,
    MAX_CAPTURES,
    NativeCapture,
    NativeCaptureRow,
    NativeDatasetRejected,
    build_native_training_cases,
    decode_native_capture,
)
from autplay.application.training_consent import TrainingConsentService
from autplay.domain.recommendations import JsonValue
from autplay.domain.training_consent import TrainingConsentHistory


def audit_native_dataset(
    session: Session,
    *,
    consent: TrainingConsentService,
    history: TrainingConsentHistory,
    train_end_ms: int,
    validation_end_ms: int,
    excluded_event_ids: frozenset[UUID] = frozenset(),
) -> dict[str, JsonValue]:
    """Caller supplies a repeatable read, read-only transaction and proven ledger."""
    if (
        session.scalar(text("SHOW transaction_read_only")) != "on"
        or session.scalar(text("SHOW transaction_isolation")) != "repeatable read"
    ):
        raise NativeDatasetRejected("READ_ONLY_SNAPSHOT_REQUIRED")
    now = session.scalar(text("SELECT transaction_timestamp()"))
    if not isinstance(now, datetime):
        raise NativeDatasetRejected("DATABASE_CLOCK_UNAVAILABLE")
    row = SonaNativeCaptureRow
    count, size = session.execute(
        select(func.count(), func.coalesce(func.sum(func.octet_length(row.payload)), 0))
    ).one()
    if count > MAX_CAPTURES or size > MAX_BATCH_BYTES:
        raise NativeDatasetRejected("BATCH_LIMIT")
    # Inspect owners/revisions before loading any retained owner-bearing payload.
    metadata = session.execute(
        select(row.request_id, row.user_id, row.consent_revision).order_by(
            row.created_at, row.request_id
        )
    ).all()
    revisions = {
        owner: consent.inspect_grant(session, owner, history)
        for owner in {m.user_id for m in metadata}
    }
    approved_ids = [m.request_id for m in metadata if revisions[m.user_id] == m.consent_revision]
    rejected: Counter[str] = Counter()
    if len(approved_ids) != len(metadata):
        rejected["CURRENT_CONSENT_UNPROVEN"] += len(metadata) - len(approved_ids)
    captures: list[NativeCapture] = []
    for value in session.scalars(
        select(row).where(row.request_id.in_(approved_ids)).order_by(row.created_at, row.request_id)
    ):
        revision = revisions[value.user_id]
        assert revision is not None
        try:
            captures.append(
                decode_native_capture(
                    NativeCaptureRow(
                        request_id=value.request_id,
                        owner_user_id=value.user_id,
                        consent_revision=value.consent_revision,
                        created_at=value.created_at,
                        retained_until=value.retained_until,
                        payload=value.payload,
                        payload_sha256=value.payload_sha256.hex(),
                    ),
                    current_revision=revision,
                    now=now,
                )
            )
        except NativeDatasetRejected as error:
            rejected[str(error)] += 1
        except ValueError, TypeError, KeyError, OverflowError:
            rejected["INVALID_CAPTURE_DOCUMENT"] += 1
    _, report = build_native_training_cases(
        captures,
        train_end_ms=train_end_ms,
        validation_end_ms=validation_end_ms,
        excluded_event_ids=excluded_event_ids,
    )
    report["stored_capture_count"] = count
    report["stored_capture_bytes"] = size
    report["capture_decode_rejections"] = dict(sorted(rejected.items()))
    report["inspected_at_utc"] = now.isoformat()
    report["owner_payload_exported"] = False
    return report
