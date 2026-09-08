"""Read-only aggregate admission checks for an isolated R1B source restore."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from sqlalchemy import Connection, text

from autplay.application.sona_source_preflight import (
    SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    SonaSourceAggregateCounts,
    SonaSourceAggregateSnapshot,
)

_EMPTY_COUNTS = SonaSourceAggregateCounts(*(0 for _ in range(17)))

_AGGREGATE_QUERY = text(
    r"""
WITH replay_requests AS (
    SELECT
        request.recommendation_request_id,
        request.user_id,
        request.created_at,
        request.interaction_watermark,
        request.request_sha256,
        request.request_document,
        snapshot.snapshot_document
    FROM ml.recommendation_request AS request
    JOIN ml.recommendation_input_snapshot AS snapshot
      ON snapshot.recommendation_input_snapshot_id = request.recommendation_input_snapshot_id
     AND snapshot.user_id = request.user_id
    WHERE request.pipeline_key = 'cpu-baseline'
      AND request.pipeline_version = '1'
      AND request.shadow = false
      AND octet_length(request.pipeline_manifest_sha256) = 32
      AND octet_length(request.request_sha256) = 32
      AND octet_length(request.input_snapshot_sha256) = 32
      AND octet_length(request.policy_snapshot_sha256) = 32
      AND request.input_snapshot_sha256 = snapshot.input_snapshot_sha256
      AND request.interaction_watermark = snapshot.interaction_watermark
      AND request.catalog_snapshot = snapshot.catalog_snapshot
      AND request.availability_snapshot_ref = snapshot.availability_snapshot
      AND request.policy_snapshot_sha256 = snapshot.policy_snapshot_sha256
      AND request.request_schema_version >= 1
      AND request.request_canonicalization_version >= 1
      AND request.interaction_watermark >= 0
      AND request.catalog_snapshot >= 0
      AND jsonb_typeof(request.request_document) = 'object'
      AND jsonb_typeof(snapshot.snapshot_document) = 'object'
      AND jsonb_typeof(snapshot.snapshot_document -> 'tracks') = 'array'
),
all_snapshot_tracks AS (
    SELECT
        request.recommendation_request_id,
        request.user_id,
        request.created_at,
        request.interaction_watermark,
        request.request_document,
        track.value AS track_document
    FROM replay_requests AS request
    CROSS JOIN LATERAL jsonb_array_elements(request.snapshot_document -> 'tracks') AS track(value)
),
valid_snapshot_tracks AS (
    SELECT
        track.recommendation_request_id,
        track.user_id,
        track.created_at,
        track.interaction_watermark,
        track.request_document,
        (track.track_document ->> 'recording_id')::uuid AS recording_id,
        track.track_document
    FROM all_snapshot_tracks AS track
    WHERE jsonb_typeof(track.track_document) = 'object'
      AND track.track_document ->> 'recording_id'
          ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
),
mandatory_tracks AS (
    SELECT DISTINCT
        track.recommendation_request_id,
        track.user_id,
        track.created_at,
        track.interaction_watermark,
        track.recording_id
    FROM valid_snapshot_tracks AS track
    WHERE track.track_document -> 'authorized' = 'true'::jsonb
      AND track.track_document -> 'excluded' = 'false'::jsonb
      AND track.track_document ->> 'identity_status' = 'ACTIVE'
      AND track.track_document ->> 'preference' <> 'DISLIKED'
      AND (
          (
              track.request_document ->> 'surface' = 'offline_pack'
              AND track.track_document ->> 'availability' = 'LOCAL'
          )
          OR (
              track.request_document ->> 'surface' IN ('recommendations', 'home')
              AND track.track_document ->> 'availability' IN ('LOCAL', 'VAULT', 'EXTERNAL')
          )
      )
),
candidate_stats AS (
    SELECT
        request.recommendation_request_id,
        request.user_id,
        request.created_at,
        request.interaction_watermark,
        count(track.recording_id)::bigint AS candidate_count
    FROM replay_requests AS request
    LEFT JOIN mandatory_tracks AS track
      ON track.recommendation_request_id = request.recommendation_request_id
    GROUP BY request.recommendation_request_id, request.user_id,
             request.created_at, request.interaction_watermark
),
bounded_requests AS (
    SELECT * FROM candidate_stats WHERE candidate_count BETWEEN 1 AND 1024
),
causal_impressions AS (
    SELECT DISTINCT
        interaction.interaction_id,
        interaction.user_id,
        interaction.recommendation_request_id,
        interaction.recording_id,
        interaction.source_rank,
        interaction.created_at
    FROM library.user_interaction_event AS interaction
    WHERE interaction.event_type = 'RECOMMENDATION_IMPRESSION_RECORDED'
      AND interaction.recommendation_request_id IS NOT NULL
      AND interaction.recording_id IS NOT NULL
      AND interaction.source_rank BETWEEN 1 AND 1000
),
attributed_outcomes AS (
    SELECT
        request.recommendation_request_id,
        request.user_id,
        listen.listening_event_id,
        listen.recording_id,
        listen.created_at AS observed_at,
        row_number() OVER (
            PARTITION BY request.recommendation_request_id
            ORDER BY listen.created_at, listen.listening_event_id
        ) AS outcome_order
    FROM bounded_requests AS request
    JOIN library.listening_event AS listen
      ON listen.recommendation_request_id = request.recommendation_request_id
     AND listen.user_id = request.user_id
     AND listen.event_origin = 'RECOMMENDED'
     AND listen.excluded_from_taste = false
     AND listen.recording_id IS NOT NULL
     AND listen.created_at > request.created_at
     AND listen.created_at <= request.created_at + interval '7 days'
     AND listen.created_at <= to_timestamp(:source_captured_at_ms / 1000.0)
    JOIN sync.sync_event AS outcome_event
      ON outcome_event.event_id = listen.listening_event_id
     AND outcome_event.user_id = listen.user_id
     AND outcome_event.origin_device_id = listen.device_id
     AND outcome_event.event_type = 'LISTENING_EVENT_RECORDED'
     AND outcome_event.schema_version = 1
     AND outcome_event.operation = 'UPSERT'
     AND outcome_event.created_at <= to_timestamp(:source_captured_at_ms / 1000.0)
    JOIN sync.device_event_inbox AS outcome_inbox
      ON outcome_inbox.event_id = outcome_event.event_id
     AND outcome_inbox.user_id = outcome_event.user_id
     AND outcome_inbox.device_id = outcome_event.origin_device_id
     AND outcome_inbox.event_type = outcome_event.event_type
     AND outcome_inbox.schema_version = outcome_event.schema_version
     AND outcome_inbox.aggregate_type = outcome_event.aggregate_type
     AND outcome_inbox.aggregate_id = outcome_event.aggregate_id
     AND outcome_inbox.payload = outcome_event.payload
     AND outcome_inbox.apply_status = 'APPLIED'
     AND octet_length(outcome_inbox.request_hash) = 32
     AND outcome_inbox.received_at <= to_timestamp(:source_captured_at_ms / 1000.0)
    JOIN ml.recommendation_item AS item
      ON item.recommendation_request_id = request.recommendation_request_id
     AND item.recording_id = listen.recording_id
    JOIN mandatory_tracks AS candidate
      ON candidate.recommendation_request_id = request.recommendation_request_id
     AND candidate.recording_id = listen.recording_id
    WHERE EXISTS (
        SELECT 1
        FROM causal_impressions AS impression
        WHERE impression.interaction_id = COALESCE(
                  NULLIF(
                      outcome_event.payload
                          #>> '{recommendation,impression_event_server_id}',
                      ''
                  )::uuid,
                  NULLIF(
                      outcome_event.payload
                          #>> '{recommendation,impression_event_local_id}',
                      ''
                  )::uuid
              )
          AND impression.user_id = request.user_id
          AND impression.recommendation_request_id = request.recommendation_request_id
          AND impression.recording_id = listen.recording_id
          AND impression.source_rank = item.rank
          AND impression.created_at <= listen.created_at
          AND outcome_event.payload ->> 'interaction_type' = 'LISTENING_EVENT_RECORDED'
          AND outcome_event.payload ->> 'recording_id' = listen.recording_id::text
          AND outcome_event.payload ->> 'event_origin' = 'RECOMMENDED'
          AND outcome_event.payload -> 'excluded_from_taste' = 'false'::jsonb
          AND outcome_event.payload #>> '{recommendation,recommendation_request_id}' =
              request.recommendation_request_id::text
          AND outcome_event.payload #>> '{recommendation,recording_id}' =
              listen.recording_id::text
          AND (outcome_event.payload #>> '{recommendation,source_rank}')::integer =
              item.rank
    )
),
mature_requests AS (
    SELECT request.*
    FROM bounded_requests AS request
    WHERE request.created_at + interval '7 days'
          <= to_timestamp(:source_captured_at_ms / 1000.0)
      AND EXISTS (
          SELECT 1 FROM attributed_outcomes AS outcome
          WHERE outcome.recommendation_request_id = request.recommendation_request_id
            AND outcome.outcome_order = 1
      )
),
source_listens AS (
    SELECT
        listen.listening_event_id,
        listen.user_id,
        listen.recording_id,
        listen.started_at,
        inbox.received_at,
        server_event.server_sequence
    FROM library.listening_event AS listen
    JOIN sync.sync_event AS server_event
      ON server_event.event_id = listen.listening_event_id
     AND server_event.user_id = listen.user_id
     AND server_event.origin_device_id = listen.device_id
     AND server_event.event_type = 'LISTENING_EVENT_RECORDED'
    JOIN sync.device_event_inbox AS inbox
      ON inbox.event_id = listen.listening_event_id
     AND inbox.user_id = listen.user_id
     AND inbox.device_id = listen.device_id
     AND inbox.apply_status = 'APPLIED'
     AND octet_length(inbox.request_hash) = 32
    WHERE listen.recording_id IS NOT NULL
),
ranked_history AS (
    SELECT
        request.recommendation_request_id,
        history.recording_id,
        row_number() OVER (
            PARTITION BY request.recommendation_request_id
            ORDER BY history.started_at DESC, history.server_sequence DESC,
                     history.listening_event_id DESC
        ) AS reverse_history_order
    FROM mature_requests AS request
    JOIN source_listens AS history
      ON history.user_id = request.user_id
     AND history.server_sequence <= request.interaction_watermark
     AND history.received_at <= request.created_at
     AND history.started_at <= request.created_at
),
request_history AS (
    SELECT recommendation_request_id, recording_id
    FROM ranked_history
    WHERE reverse_history_order <= 512
),
history_bound_requests AS (
    SELECT request.*
    FROM mature_requests AS request
    WHERE EXISTS (
        SELECT 1 FROM request_history AS history
        WHERE history.recommendation_request_id = request.recommendation_request_id
    )
),
approved_models AS (
    SELECT model.embedding_model_id
    FROM ml.embedding_model AS model
    WHERE model.status = 'ACTIVE'
      AND model.license_review_reference IS NOT NULL
      AND octet_length(model.manifest_sha256) = 32
      AND octet_length(model.weights_sha256) = 32
      AND octet_length(model.preprocessing_sha256) = 32
      AND EXISTS (
          SELECT 1 FROM ml.embedding_benchmark_report AS report
          WHERE report.embedding_model_id = model.embedding_model_id
            AND report.decision = 'APPROVED'
      )
),
approved_embeddings AS (
    SELECT DISTINCT embedding.embedding_model_id, embedding.recording_id
    FROM ml.recording_embedding AS embedding
    JOIN approved_models AS model
      ON model.embedding_model_id = embedding.embedding_model_id
    WHERE embedding.retired_at IS NULL
      AND octet_length(embedding.preprocessing_input_sha256) = 32
      AND octet_length(embedding.vector_sha256) = 32
),
request_required_recordings AS (
    SELECT request.recommendation_request_id, track.recording_id
    FROM history_bound_requests AS request
    JOIN mandatory_tracks AS track
      ON track.recommendation_request_id = request.recommendation_request_id
    UNION
    SELECT request.recommendation_request_id, history.recording_id
    FROM history_bound_requests AS request
    JOIN request_history AS history
      ON history.recommendation_request_id = request.recommendation_request_id
),
fully_bound_request_models AS (
    SELECT request.recommendation_request_id, request.user_id, request.created_at,
           model.embedding_model_id
    FROM history_bound_requests AS request
    CROSS JOIN approved_models AS model
    WHERE NOT EXISTS (
        SELECT 1
        FROM request_required_recordings AS required
        WHERE required.recommendation_request_id = request.recommendation_request_id
          AND NOT EXISTS (
              SELECT 1 FROM approved_embeddings AS embedding
              WHERE embedding.embedding_model_id = model.embedding_model_id
                AND embedding.recording_id = required.recording_id
          )
    )
),
fully_bound_requests AS (
    SELECT DISTINCT recommendation_request_id, user_id, created_at
    FROM fully_bound_request_models
),
fully_bound_times AS (
    SELECT request.*,
           floor(extract(epoch FROM request.created_at) * 1000)::bigint AS cutoff_ms
    FROM fully_bound_requests AS request
),
time_bounds AS (
    SELECT min(cutoff_ms)::bigint AS minimum_ms,
           max(cutoff_ms)::bigint AS maximum_ms
    FROM fully_bound_times
),
split_bounds AS (
    SELECT
        minimum_ms,
        maximum_ms,
        greatest(0, maximum_ms - minimum_ms)::bigint AS span_ms,
        CASE WHEN maximum_ms - minimum_ms >= 2 * :embargo_ms
             THEN maximum_ms - minimum_ms - 2 * :embargo_ms ELSE 0 END::bigint
             AS usable_ms
    FROM time_bounds
),
split_windows AS (
    SELECT *,
        minimum_ms + (usable_ms * 60 / 100) AS train_end_ms,
        minimum_ms + (usable_ms * 60 / 100) + :embargo_ms AS validation_start_ms,
        minimum_ms + (usable_ms * 80 / 100) + :embargo_ms AS validation_end_ms,
        minimum_ms + (usable_ms * 80 / 100) + 2 * :embargo_ms AS test_start_ms
    FROM split_bounds
),
owner_violations AS (
    SELECT count(*)::bigint AS count
    FROM ml.recommendation_request AS request
    JOIN ml.recommendation_input_snapshot AS snapshot
      ON snapshot.recommendation_input_snapshot_id = request.recommendation_input_snapshot_id
    WHERE request.user_id <> snapshot.user_id
),
attribution_violations AS (
    SELECT count(*)::bigint AS count
    FROM library.listening_event AS listen
    JOIN ml.recommendation_request AS request
      ON request.recommendation_request_id = listen.recommendation_request_id
    LEFT JOIN ml.recommendation_item AS item
      ON item.recommendation_request_id = listen.recommendation_request_id
     AND item.recording_id = listen.recording_id
    WHERE listen.event_origin = 'RECOMMENDED'
      AND (listen.user_id <> request.user_id OR item.recording_id IS NULL)
),
source_chain_violations AS (
    SELECT count(*)::bigint AS count
    FROM library.listening_event AS listen
    LEFT JOIN sync.sync_event AS server_event
      ON server_event.event_id = listen.listening_event_id
    LEFT JOIN sync.device_event_inbox AS inbox
      ON inbox.event_id = listen.listening_event_id
    WHERE server_event.event_id IS NULL
       OR inbox.event_id IS NULL
       OR server_event.user_id <> listen.user_id
       OR inbox.user_id <> listen.user_id
       OR server_event.origin_device_id <> listen.device_id
       OR inbox.device_id <> listen.device_id
       OR octet_length(inbox.request_hash) <> 32
),
integrity_gaps AS (
    SELECT
        (
            SELECT count(*) FROM ml.recommendation_request AS request
            WHERE request.pipeline_key = 'cpu-baseline'
              AND request.pipeline_version = '1'
              AND (
                  request.request_sha256 IS NOT NULL
                  OR request.recommendation_input_snapshot_id IS NOT NULL
              )
              AND (
                  octet_length(request.request_sha256) IS DISTINCT FROM 32
                  OR octet_length(request.input_snapshot_sha256) IS DISTINCT FROM 32
                  OR jsonb_typeof(request.request_document) IS DISTINCT FROM 'object'
              )
        )
        + (
            SELECT count(*) FROM all_snapshot_tracks AS track
            WHERE jsonb_typeof(track.track_document) <> 'object'
               OR track.track_document ->> 'recording_id' IS NULL
               OR track.track_document ->> 'recording_id'
                  !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        ) AS count
)
SELECT
    (SELECT count(*) FROM replay_requests)::bigint AS replay_request_count,
    (SELECT count(*) FROM bounded_requests)::bigint AS candidate_bounded_request_count,
    (SELECT count(*) FROM mature_requests)::bigint AS mature_attributed_request_count,
    (SELECT count(*) FROM history_bound_requests)::bigint AS history_bound_request_count,
    (SELECT count(*) FROM fully_bound_requests)::bigint AS fully_bound_request_count,
    (SELECT count(DISTINCT user_id) FROM fully_bound_requests)::bigint AS eligible_owner_count,
    (
        SELECT count(DISTINCT required.recording_id)
        FROM request_required_recordings AS required
        JOIN fully_bound_requests AS request
          ON request.recommendation_request_id = required.recommendation_request_id
    )::bigint AS eligible_recording_count,
    (SELECT count(*) FROM approved_models)::bigint AS approved_embedding_model_count,
    (SELECT count(DISTINCT recording_id) FROM approved_embeddings)::bigint
        AS embedded_recording_count,
    (
        SELECT count(*) FROM fully_bound_times AS request CROSS JOIN split_windows AS split
        WHERE split.usable_ms > 0 AND request.cutoff_ms <= split.train_end_ms
    )::bigint AS train_request_count,
    (
        SELECT count(*) FROM fully_bound_times AS request CROSS JOIN split_windows AS split
        WHERE split.usable_ms > 0
          AND request.cutoff_ms >= split.validation_start_ms
          AND request.cutoff_ms <= split.validation_end_ms
    )::bigint AS validation_request_count,
    (
        SELECT count(*) FROM fully_bound_times AS request CROSS JOIN split_windows AS split
        WHERE split.usable_ms > 0
          AND request.cutoff_ms >= split.test_start_ms
          AND request.cutoff_ms <= split.maximum_ms
    )::bigint AS test_request_count,
    coalesce((SELECT span_ms FROM split_bounds), 0)::bigint AS eligible_span_ms,
    (SELECT count FROM owner_violations)::bigint AS owner_isolation_violation_count,
    (SELECT count FROM attribution_violations)::bigint AS attribution_violation_count,
    (SELECT count FROM source_chain_violations)::bigint AS source_chain_violation_count,
    (SELECT count FROM integrity_gaps)::bigint AS hash_or_snapshot_integrity_gap_count
"""
)


class SqlAlchemySonaSourceAggregateReader:
    """Read one aggregate row from the restored PostgreSQL snapshot."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def read(self, *, source_captured_at_ms: int) -> SonaSourceAggregateSnapshot:
        if source_captured_at_ms < 0:
            raise ValueError("Sona source capture time is invalid")
        read_only = str(self._connection.scalar(text("SHOW transaction_read_only"))).lower()
        isolation = str(self._connection.scalar(text("SHOW transaction_isolation"))).lower()
        if read_only != "on" or isolation != "repeatable read":
            raise RuntimeError("sona_source_transaction_not_read_only_repeatable_read")
        head = str(self._connection.scalar(text("SELECT version_num FROM alembic_version")))
        if head != SONA_SOURCE_EXPECTED_ALEMBIC_HEAD:
            return SonaSourceAggregateSnapshot(head, True, isolation, _EMPTY_COUNTS)
        row = (
            self._connection.execute(
                _AGGREGATE_QUERY,
                {
                    "source_captured_at_ms": source_captured_at_ms,
                    "embargo_ms": 7 * 24 * 60 * 60 * 1_000,
                },
            )
            .mappings()
            .one()
        )
        values = cast(Mapping[str, object], row)
        counts = SonaSourceAggregateCounts(
            *(_integer(values, field) for field in SonaSourceAggregateCounts.__dataclass_fields__)
        )
        return SonaSourceAggregateSnapshot(head, True, isolation, counts)


def _integer(value: Mapping[str, object], field: str) -> int:
    candidate = value.get(field)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise RuntimeError("sona_source_aggregate_result_invalid")
    return candidate


__all__ = ("SqlAlchemySonaSourceAggregateReader",)
