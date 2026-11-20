"""Read the accepted normalized event stream on the original P11 connection."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.application.native_capture import NativeCaptureBoundary
from autplay.application.recommendations import recommendation_input_snapshot_document
from autplay.domain.adaptive_recommendations import MAX_SOURCE_EVENTS
from autplay.domain.native_temporal import NativeTemporalFact
from autplay.domain.recommendations import RecommendationInputSnapshot

from .native_temporal_source import read_native_temporal_fact


def lock_native_capture_owner(session: Session, owner: UUID) -> None:
    """Restore barrier and nonblocking lock pair; no half-pair wait/deadlock."""
    session.execute(text("SET LOCAL lock_timeout='5s'"))
    session.execute(text("SET LOCAL statement_timeout='30s'"))
    session.execute(text("SELECT app_private.lock_native_served_server()"))
    present = session.scalar(
        text("SELECT user_id FROM account.user_account WHERE user_id=:owner FOR UPDATE NOWAIT"),
        {"owner": owner},
    )
    acquired = session.scalar(
        text("""
        SELECT pg_try_advisory_xact_lock(('x'||substr(encode(sha256(
          convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(:owner)),
          'hex'),1,16))::bit(64)::bigint)
    """),
        {"owner": owner},
    )
    if present != owner or acquired is not True:
        raise ValueError("NATIVE_CAPTURE_AUTHORITY_BUSY")


def discover_native_capture(
    session: Session, *, owner: UUID, cutoff: int, watermark: int | None = None
) -> NativeCaptureBoundary:
    """Complete accepted stream, with fail-closed orphan detection and overflow.

    An outer join deliberately includes broken APPLIED inboxes or native sync
    orphans; they must fail validation instead of vanishing from the selection.
    Staged/erased/terminal rejected inboxes are not accepted positive facts.
    """
    rows = session.execute(
        text("""
        SELECT coalesce(i.event_id,s.event_id) AS event_id,s.server_sequence
        FROM sync.device_event_inbox i FULL JOIN sync.sync_event s USING(event_id)
        WHERE (i.user_id=:owner OR s.user_id=:owner)
          AND ((i.ingress_validation_version=2 AND i.apply_status='APPLIED')
            OR (s.ingress_validation_version=2 AND s.operation='UPSERT'))
          AND floor(extract(epoch FROM coalesce(i.received_at,s.created_at))*1000)<=:cutoff
          AND (s.created_at IS NULL OR floor(extract(epoch FROM s.created_at)*1000)<=:cutoff)
          AND (CAST(:watermark AS bigint) IS NULL OR s.server_sequence<=:watermark
            OR s.server_sequence IS NULL)
        ORDER BY coalesce(i.event_id,s.event_id)
        LIMIT :bound
    """),
        {"owner": owner, "cutoff": cutoff, "watermark": watermark, "bound": MAX_SOURCE_EVENTS + 1},
    ).all()
    if len(rows) > MAX_SOURCE_EVENTS:
        raise ValueError("NATIVE_CAPTURE_SOURCE_OVERFLOW")
    return NativeCaptureBoundary(
        owner,
        tuple(row[0] for row in rows),
        cutoff,
        watermark if watermark is not None else max((row[1] or 0 for row in rows), default=0),
    )


def read_capture_facts(
    session: Session, boundary: NativeCaptureBoundary, baseline: RecommendationInputSnapshot
) -> tuple[NativeTemporalFact, ...]:
    requests = session.scalars(
        text("""
        SELECT DISTINCT recommendation_request_id FROM ml.recommendation_attribution_association
        WHERE owner_user_id=:owner
          AND (interaction_id=ANY(:events) OR listening_event_id=ANY(:events))
        ORDER BY recommendation_request_id
    """),
        {"owner": boundary.owner, "events": list(boundary.event_ids)},
    )
    for request in requests:
        session.execute(
            text("SELECT app_private.lock_recommendation_request_v2(:request)"),
            {"request": request},
        )
    tracks = recommendation_input_snapshot_document(
        boundary.owner,
        interaction_watermark=baseline.reference.interaction_watermark,
        tracks=baseline.tracks, policy_version=baseline.policy_version,
    )["tracks"]
    assert isinstance(tracks, list)
    mapping = {
        UUID(str(track["recording_id"])): track for track in tracks if isinstance(track, dict)
    }
    facts = tuple(
        read_native_temporal_fact(session, owner=boundary.owner, event_id=event, tracks=mapping)
        for event in boundary.event_ids
    )
    # Every parent is itself APPLIED before the child, so complete discovery
    # includes it. Refuse inconsistent ancestry rather than expanding the cutoff.
    if any(
        fact.causality is not None
        and fact.causality.impression_event_id is not None
        and fact.causality.impression_event_id not in boundary.event_ids
        for fact in facts
    ):
        raise ValueError("NATIVE_CAPTURE_PARENT_CLOSURE")
    return facts
