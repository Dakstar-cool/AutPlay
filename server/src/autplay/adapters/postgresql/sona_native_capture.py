"""Original P11 collection on the production schema; never a training admission.

The envelope retains raw accepted sync provenance and portable facts. A later,
versioned dataset adapter must resolve attribution and produce temporal schema 2.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

import rfc8785
from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.application.recommendations import (
    RECOMMENDATION_MAX_SNAPSHOT_TRACKS,
    recommendation_input_snapshot_document,
    recommendation_snapshot_limit,
)
from autplay.application.sona_native_catalogue import (
    COLLECTION_V2,
    COLLECTION_V3,
    EXTENDED_CATALOGUE_TRACKS,
    CatalogueSizeExceeded,
    catalogue_document,
    catalogue_matches_served,
    pack_extended_catalogue,
)
from autplay.application.sona_shadow import recommendation_ranking_document
from autplay.application.training_consent import TrainingConsentService
from autplay.domain.recommendations import (
    JsonValue,
    RecommendationInputSnapshot,
    RecommendationResponse,
    SnapshotTrack,
)

MAX_SOURCE_FACTS = 10_000
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
CAPTURE_RETENTION = timedelta(days=180)

# Retain both sides of provenance, including mismatches, rather than silently
# dropping an event through an inner join. No current-state reconstruction.
_SOURCES = """
SELECT jsonb_build_object(
 'kind', 'SYNC', 'event_id', s.event_id, 'event_type', s.event_type,
 'server_sequence', s.server_sequence, 'schema_version', s.schema_version,
 'aggregate_type', s.aggregate_type, 'aggregate_id', s.aggregate_id,
 'operation', s.operation, 'created_at', s.created_at,
 'payload', s.payload, 'device_id', s.origin_device_id,
 'device_sequence', i.device_sequence, 'occurred_at', i.occurred_at,
 'received_at', i.received_at, 'source_request_sha256', encode(i.request_hash,'hex'),
 'listening_projection', to_jsonb(listen), 'interaction_projection', to_jsonb(interaction),
 'preference_binding', CASE WHEN preference_ref.user_track_ref_id IS NULL THEN NULL ELSE
   jsonb_build_object('user_track_ref_id',preference_ref.user_track_ref_id,
     'recording_id',preference_ref.recording_id,
     'resolution_status',preference_ref.resolution_status,
     'deleted_at',preference_ref.deleted_at) END,
 'recording_context', jsonb_build_object('recording_id',recording.recording_id,
   'artist_key',recording.artist_credit_id,'release_key',release.release_id),
 'provenance_matches', COALESCE(i.user_id=s.user_id AND
   i.device_id=s.origin_device_id AND i.event_type=s.event_type AND
   i.schema_version=s.schema_version AND i.aggregate_type=s.aggregate_type AND
   i.aggregate_id=s.aggregate_id AND i.payload=s.payload AND
   i.apply_status='APPLIED' AND i.device_sequence>=1 AND
   octet_length(i.request_hash)=32, false)
) AS document, 'SYNC' AS kind, s.server_sequence AS ordinal, s.event_id AS identity
FROM sync.sync_event s LEFT JOIN sync.device_event_inbox i
 ON i.event_id=s.event_id AND i.user_id=s.user_id
LEFT JOIN library.listening_event listen
 ON s.event_type='LISTENING_EVENT_RECORDED'
 AND listen.listening_event_id=s.event_id AND listen.user_id=s.user_id
LEFT JOIN library.user_interaction_event interaction
 ON interaction.interaction_id=s.event_id AND interaction.user_id=s.user_id
LEFT JOIN library.user_track_ref preference_ref
 ON s.event_type='USER_TRACK_PREFERENCE_SET' AND preference_ref.user_id=s.user_id
 AND preference_ref.user_track_ref_id::text=s.payload->>'local_user_track_ref_id'
LEFT JOIN catalog.recording recording ON recording.recording_id=COALESCE(
 listen.recording_id,interaction.recording_id,preference_ref.recording_id)
LEFT JOIN LATERAL (
 SELECT rel.release_id FROM catalog.release_track rt
 JOIN catalog.medium medium ON medium.medium_id=rt.medium_id
 JOIN catalog.release rel ON rel.release_id=medium.release_id
 WHERE rt.recording_id=recording.recording_id AND rt.deleted_at IS NULL
 AND medium.deleted_at IS NULL AND rel.deleted_at IS NULL
 ORDER BY rel.release_date DESC NULLS LAST,rel.release_id LIMIT 1
) release ON true
WHERE s.user_id=:owner AND s.server_sequence<=:watermark
 AND s.event_type IN ('LISTENING_EVENT_RECORDED','RECOMMENDATION_FEEDBACK_RECORDED',
 'RECOMMENDATION_IMPRESSION_RECORDED','USER_TRACK_PREFERENCE_SET')
UNION ALL
SELECT jsonb_build_object('kind','PORTABLE','record_id',p.record_id,
 'record_kind',p.kind,'document',p.document,'content_sha256',encode(p.content_hash,'hex'),
 'created_at',p.created_at,'updated_at',p.updated_at,
 'taste_projection',to_jsonb(taste),
 'track_binding',CASE WHEN ref.user_track_ref_id IS NULL THEN NULL ELSE
   jsonb_build_object('user_track_ref_id',ref.user_track_ref_id,'recording_id',ref.recording_id,
     'resolution_status',ref.resolution_status,'deleted_at',ref.deleted_at) END),
 'PORTABLE', 0, p.record_id
FROM account.portable_record p
LEFT JOIN library.portable_taste_signal taste
 ON taste.user_id=p.user_id AND taste.record_id=p.record_id
LEFT JOIN library.user_track_ref ref ON ref.user_id=taste.user_id
 AND ref.user_track_ref_id=taste.user_track_ref_id
WHERE p.user_id=:owner
 AND p.kind IN ('LISTENING','PREFERENCE','TASTE')
"""


class SqlAlchemySonaNativeCapture:
    """Write only within the original recommendation transaction and proven grant."""

    def __init__(
        self, consent: TrainingConsentService, *, catalogue_owner: UUID | None = None
    ) -> None:
        self.catalogue_owner = catalogue_owner
        self._consent = consent

    def grant(self, session: Session, owner: UUID) -> int | None:
        return self._consent.capture_grant(session, owner)

    def save(
        self,
        session: Session,
        response: RecommendationResponse,
        snapshot: RecommendationInputSnapshot,
        revision: int,
        *,
        catalogue: tuple[SnapshotTrack, ...] | None = None,
        require_complete_catalogue: bool = False,
    ) -> None:
        trace = response.request
        params = {
            "owner": trace.query.user_id,
            "watermark": snapshot.reference.interaction_watermark,
            "limit": MAX_SOURCE_FACTS + 1,
        }
        # Check size before fetching payloads into the CPU process. The limit is
        # diagnostic and explicit; it never becomes a silently truncated dataset.
        count, source_bytes = session.execute(
            text(
                "SELECT count(*), COALESCE(sum(octet_length(document::text)),0) FROM ("
                "SELECT document FROM (" + _SOURCES + ") facts LIMIT :limit) bounded"
            ),
            params,
        ).one()
        status = "COLLECTED"
        facts: list[JsonValue] = []
        if count > MAX_SOURCE_FACTS or source_bytes > MAX_CAPTURE_BYTES // 2:
            status = "SOURCE_LIMIT"
        else:
            facts = list(
                session.scalars(
                    text(
                        "SELECT document FROM ("
                        + _SOURCES
                        + ") facts ORDER BY kind,ordinal,identity"
                    ),
                    params,
                )
            )
            if any(
                isinstance(fact, dict)
                and fact.get("kind") == "SYNC"
                and fact.get("provenance_matches") is not True
                for fact in facts
            ):
                status = "SOURCE_PROVENANCE_GAP"
        cutoff = session.scalar(text("SELECT transaction_timestamp()"))
        if not isinstance(cutoff, datetime):
            raise RuntimeError("native capture database clock unavailable")
        document: dict[str, JsonValue] = {
            "schema": "AUTPLAY_P11_NATIVE_COLLECTION_V1",
            "producer": "ORIGINAL_REQUEST_TRANSACTION",
            "quality_eligible": False,
            "admission_blocker": "TEMPORAL_SCHEMA2_DATASET_ADAPTER_REQUIRED",
            "collection_status": status,
            "request_id": str(trace.recommendation_request_id),
            "owner_user_id": str(trace.query.user_id),
            "consent_revision": revision,
            "cutoff_at": cutoff.isoformat(),
            "surface": trace.query.surface.value,
            "request": trace.canonical_request,
            "request_sha256": trace.request_sha256,
            "input_snapshot_sha256": snapshot.reference.input_snapshot_sha256,
            "input_snapshot": recommendation_input_snapshot_document(
                trace.query.user_id,
                interaction_watermark=snapshot.reference.interaction_watermark,
                tracks=snapshot.tracks,
                policy_version=snapshot.policy_version,
            ),
            "ranking": recommendation_ranking_document(response.items),
            "source_facts": facts,
            "source_count_lower_bound": int(count),
            "candidate_count": len(snapshot.tracks),
            "candidate_policy_cap": recommendation_snapshot_limit(snapshot.policy_version),
            "source_fact_cap": MAX_SOURCE_FACTS,
        }
        if catalogue is not None:
            if trace.query.user_id != self.catalogue_owner or snapshot.policy_version != "2":
                raise ValueError("native catalogue owner or policy mismatch")
            extended = len(catalogue) > RECOMMENDATION_MAX_SNAPSHOT_TRACKS
            document["schema"] = COLLECTION_V3 if extended else COLLECTION_V2
            document["catalogue_count_lower_bound"] = len(catalogue)
            document["catalogue"] = None
            document["catalogue_sha256"] = None
            if len(catalogue) > EXTENDED_CATALOGUE_TRACKS:
                document["catalogue_status"] = "TRACK_LIMIT"
            elif not catalogue_matches_served(catalogue, snapshot.tracks):
                document["catalogue_status"] = "SERVING_WINDOW_MISMATCH"
            else:
                full = catalogue_document(
                    trace.query.user_id,
                    snapshot.reference.interaction_watermark,
                    catalogue,
                    extended=extended,
                )
                try:
                    if extended:
                        packed, digest = pack_extended_catalogue(full)
                        document["catalogue"] = packed
                    else:
                        digest = sha256(rfc8785.dumps(full)).hexdigest()
                        document["catalogue"] = full
                    document["catalogue_sha256"] = digest
                    document["catalogue_status"] = "COMPLETE"
                except CatalogueSizeExceeded:
                    document["catalogue_status"] = "BYTE_LIMIT"
        payload = rfc8785.dumps(document)
        if len(payload) > MAX_CAPTURE_BYTES:
            # Preserve the full P11 universe and an explicit rejected-source marker.
            document["source_facts"] = []
            document["collection_status"] = status = "SOURCE_LIMIT"
            payload = rfc8785.dumps(document)
        if len(payload) > MAX_CAPTURE_BYTES and catalogue is not None:
            document["catalogue_status"] = "BYTE_LIMIT"
            document["catalogue"] = None
            document["catalogue_sha256"] = None
            payload = rfc8785.dumps(document)
        if len(payload) > MAX_CAPTURE_BYTES:
            raise ValueError("sona_native_capture_envelope_over_cap")
        if require_complete_catalogue and document.get("catalogue_status") != "COMPLETE":
            raise ValueError("NATIVE_CATALOGUE_INCOMPLETE")
        session.execute(
            text(
                "INSERT INTO ml.sona_native_capture "
                "(request_id,user_id,consent_revision,surface,collection_status,"
                "created_at,retained_until,payload,payload_sha256) "
                "VALUES (:request,:owner,:revision,:surface,:status,:created,:retained,"
                ":payload,:digest)"
            ),
            {
                "request": trace.recommendation_request_id,
                "owner": trace.query.user_id,
                "revision": revision,
                "surface": trace.query.surface.value,
                "status": status,
                "created": cutoff,
                "retained": cutoff + CAPTURE_RETENTION,
                "payload": payload,
                "digest": sha256(payload).digest(),
            },
        )


def cleanup_native_capture(sessions: Callable[[], Session], *, limit: int = 100) -> int:
    """Run even when collection is disabled and the user makes no new requests."""
    if not 1 <= limit <= 1_000:
        raise ValueError("invalid native capture cleanup limit")
    with sessions() as session, session.begin():
        removed = session.execute(
            text(
                "DELETE FROM ml.sona_native_capture WHERE request_id IN "
                "(SELECT request_id FROM ml.sona_native_capture WHERE retained_until<=now() "
                "ORDER BY retained_until,request_id LIMIT :limit FOR UPDATE SKIP LOCKED) "
                "RETURNING request_id"
            ),
            {"limit": limit},
        ).all()
        return len(removed)
