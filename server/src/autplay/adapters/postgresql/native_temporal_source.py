"""Exact private accepted-source read helpers, within the caller's authority locks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.domain.adaptive_recommendations import DimensionKind, TemporalDimension
from autplay.domain.native_temporal import NativeTemporalCausality, NativeTemporalFact
from autplay.domain.sync_ingress import normalized_payload_sha256


def milliseconds(value: datetime) -> int:
    # Avoid binary64 timestamp rounding near cutoff/expiry boundaries.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("NATIVE_TEMPORAL_NAIVE_TIME")
    value = value.astimezone(UTC)
    return (
        (value.toordinal() - datetime(1970, 1, 1).toordinal()) * 86_400_000
        + (value.hour * 3600 + value.minute * 60 + value.second) * 1000
        + value.microsecond // 1000
    )


def source_retained_until_ms(received_at: datetime) -> int:
    """An elapsed 180-day ceiling, independent of session timezone/DST."""
    return milliseconds(received_at) + 180 * 86_400_000


def read_native_temporal_fact(
    session: Session, *, owner: UUID, event_id: UUID, tracks: dict[UUID, dict[str, Any]]
) -> NativeTemporalFact:
    present = session.scalar(
        text("SELECT 1 FROM sync.device_event_inbox WHERE user_id=:owner AND event_id=:event"),
        {"owner": owner, "event": event_id},
    )
    if present is None:
        raise ValueError("NATIVE_TEMPORAL_ACCEPTED_UNAVAILABLE")
    # Lock/validate before reading. ERASED is a valid terminal state to the
    # installed validator, but the fresh APPLIED-only query below must refuse it.
    session.execute(
        text("SELECT app_private.assert_normalized_listen_acceptance(:event)"),
        {"event": event_id},
    )
    row = (
        session.execute(
            text("""
        SELECT s.event_type,s.payload::text AS payload,s.server_sequence,
          i.device_id,i.journal_epoch,i.device_sequence,i.occurred_at,i.received_at,
          i.request_hash,i.normalized_payload_sha256,
          l.recording_id AS listen_recording,l.event_origin,l.played_ms,l.completion_ratio,
          l.excluded_from_taste,l.explicit_feedback,l.attribution_contract_version,
          l.attribution_closed,t.recording_id AS interaction_recording,
          t.payload->>'feedback_type' AS feedback
        FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
        LEFT JOIN library.listening_event l ON l.listening_event_id=s.event_id
        LEFT JOIN library.user_interaction_event t ON t.interaction_id=s.event_id
        WHERE s.event_id=:event AND s.user_id=:owner AND i.user_id=:owner
          AND s.ingress_validation_version=2 AND i.ingress_validation_version=2
          AND i.apply_status='APPLIED' AND i.error_code IS NULL
          AND s.operation='UPSERT' AND s.server_row_version=1
          AND s.origin_device_id=i.device_id AND s.event_type=i.event_type
          AND s.schema_version=i.schema_version AND s.schema_version=1
          AND s.aggregate_type=i.aggregate_type AND s.aggregate_id=i.aggregate_id
          AND s.aggregate_id=s.event_id
          AND s.ingress_journal_epoch=i.journal_epoch
          AND s.ingress_device_sequence=i.device_sequence
          AND s.normalized_payload_sha256=i.normalized_payload_sha256
          AND i.payload='{}'::jsonb AND octet_length(s.payload::text)<=8192
          AND octet_length(i.terminal_ack::text)<=1024
          AND NOT EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt er
            WHERE er.user_id=:owner AND er.source_event_id=:event)
    """),
            {"owner": owner, "event": event_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("NATIVE_TEMPORAL_ACCEPTED_UNAVAILABLE")
    payload = json.loads(row["payload"], parse_float=Decimal)
    if normalized_payload_sha256(payload) != row["normalized_payload_sha256"]:
        raise ValueError("NATIVE_TEMPORAL_NORMALIZED_HASH")
    causal = None
    retained = source_retained_until_ms(row["received_at"])
    if row["event_type"] == "LISTENING_EVENT_RECORDED":
        recording = row["listen_recording"]
        if row["attribution_closed"]:
            raise ValueError("NATIVE_TEMPORAL_CAUSAL_UNAVAILABLE")
    elif row["event_type"] in {
        "RECOMMENDATION_IMPRESSION_RECORDED",
        "RECOMMENDATION_FEEDBACK_RECORDED",
    }:
        recording = row["interaction_recording"]
    else:
        raise ValueError("NATIVE_TEMPORAL_SOURCE_TYPE")
    causal_listen = (
        row["event_type"] == "LISTENING_EVENT_RECORDED" and row["attribution_contract_version"] == 2
    )
    if causal_listen or row["event_type"] != "LISTENING_EVENT_RECORDED":
        association = (
            session.execute(
                text("""
            SELECT a.*,p.interaction_id AS impression_event_id
            FROM ml.recommendation_attribution_association a
            LEFT JOIN ml.recommendation_attribution_association p
              ON p.association_id=a.impression_association_id
            WHERE a.owner_user_id=:owner
              AND (a.interaction_id=:event OR a.listening_event_id=:event)
        """),
                {"owner": owner, "event": event_id},
            )
            .mappings()
            .one_or_none()
        )
        expected_kind = {
            "LISTENING_EVENT_RECORDED": "LISTEN",
            "RECOMMENDATION_IMPRESSION_RECORDED": "IMPRESSION",
            "RECOMMENDATION_FEEDBACK_RECORDED": "FEEDBACK",
        }[row["event_type"]]
        if (
            association is None
            or association["kind"] != expected_kind
            or association["recording_id"] != recording
            or (
                expected_kind != "IMPRESSION"
                and (
                    association["impression_event_id"] is None
                    or association["impression_association_id"] is None
                )
            )
        ):
            raise ValueError("NATIVE_TEMPORAL_CAUSAL_UNAVAILABLE")
        retained = min(retained, milliseconds(association["attribution_expires_at"]))
        causal = NativeTemporalCausality(
            association["association_id"],
            association["association_sha256"].hex(),
            association["recommendation_request_id"],
            association["served_rank"],
            association["decision_sha256"].hex(),
            association["served_item_sha256"].hex(),
            association["serving_provenance"],
            association["request_surface"],
            association["presentation_id"],
            association["presentation_source"],
            association["presentation_surface"],
            association["impression_association_id"],
            association["impression_event_id"],
        )
    track = tracks.get(recording)
    if track is None or not track["authorized"] or track["excluded"]:
        raise ValueError("NATIVE_TEMPORAL_BASELINE_RECORDING_UNAVAILABLE")
    dimensions = [TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, track["artist_key"])]
    if track["release_key"] is not None:
        dimensions.append(
            TemporalDimension(DimensionKind.CANONICAL_RELEASE_ID, track["release_key"])
        )
    return NativeTemporalFact(
        event_id,
        owner,
        row["device_id"],
        row["journal_epoch"],
        row["device_sequence"],
        row["server_sequence"],
        row["event_type"],
        recording,
        tuple(sorted(dimensions)),
        milliseconds(row["occurred_at"]),
        milliseconds(row["received_at"]),
        row["request_hash"].hex(),
        row["normalized_payload_sha256"].hex(),
        retained,
        row["event_origin"],
        row["played_ms"],
        float(row["completion_ratio"]) if row["completion_ratio"] is not None else None,
        bool(row["excluded_from_taste"]),
        row["feedback"],
        causal,
        row["explicit_feedback"] if row["explicit_feedback"] is not None else "NONE",
    )
