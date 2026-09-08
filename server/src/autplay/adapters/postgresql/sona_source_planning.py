"""Read transient P11 request observations from an authorized 0026 restore."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import UUID

import rfc8785
from sqlalchemy import Connection, text

from autplay.application.sona_source_planning import (
    SonaSourcePlanningRecord,
    SonaSourceRequestObservation,
)
from autplay.application.sona_source_preflight import SONA_SOURCE_EXPECTED_ALEMBIC_HEAD
from autplay.domain.recommendations import JsonValue

_PLANNING_QUERY = text(
    r"""
WITH replay_requests AS (
    SELECT
        request.recommendation_request_id,
        request.user_id,
        request.created_at,
        request.interaction_watermark,
        request.request_sha256,
        request.request_document,
        snapshot.recommendation_input_snapshot_id,
        snapshot.input_snapshot_sha256,
        snapshot.interaction_watermark AS snapshot_interaction_watermark,
        snapshot.catalog_snapshot AS snapshot_catalog_snapshot,
        snapshot.availability_snapshot AS snapshot_availability_snapshot,
        snapshot.policy_snapshot_sha256 AS snapshot_policy_snapshot_sha256,
        snapshot.snapshot_document,
        snapshot.retained_until AS snapshot_retained_until
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
candidate_counts AS (
    SELECT
        request.recommendation_request_id,
        count(track.recording_id)::bigint AS candidate_count
    FROM replay_requests AS request
    LEFT JOIN mandatory_tracks AS track
      ON track.recommendation_request_id = request.recommendation_request_id
    GROUP BY request.recommendation_request_id
),
bounded_requests AS (
    SELECT request.*
    FROM replay_requests AS request
    JOIN candidate_counts AS counts
      ON counts.recommendation_request_id = request.recommendation_request_id
    WHERE counts.candidate_count BETWEEN 1 AND 1024
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
        listen.listening_event_id,
        listen.recording_id,
        listen.played_ms,
        listen.completion_ratio::double precision AS completion_ratio,
        listen.excluded_from_taste,
        encode(outcome_inbox.request_hash, 'hex') AS outcome_source_request_sha256,
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
approved_model AS (
    SELECT model.embedding_model_id
    FROM approved_models AS model
    WHERE (SELECT count(*) FROM approved_models) = 1
),
approved_embeddings AS (
    SELECT DISTINCT embedding.embedding_model_id, embedding.recording_id
    FROM ml.recording_embedding AS embedding
    JOIN approved_model AS model
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
fully_bound_requests AS (
    SELECT request.*
    FROM history_bound_requests AS request
    CROSS JOIN approved_model AS model
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
)
SELECT
    encode(request.request_sha256, 'hex') AS request_sha256,
    request.request_document,
    encode(request.input_snapshot_sha256, 'hex') AS input_snapshot_sha256,
    request.snapshot_document,
    request.recommendation_input_snapshot_id AS baseline_snapshot_id,
    request.snapshot_interaction_watermark AS interaction_watermark,
    request.snapshot_catalog_snapshot AS catalog_snapshot,
    request.snapshot_availability_snapshot AS availability_snapshot,
    encode(request.snapshot_policy_snapshot_sha256, 'hex') AS policy_snapshot_sha256,
    request.snapshot_retained_until AS retained_until,
    request.user_id,
    floor(extract(epoch FROM request.created_at) * 1000)::bigint AS cutoff_at_ms,
    floor(extract(epoch FROM outcome.observed_at) * 1000)::bigint AS observed_at_ms,
    outcome.listening_event_id AS outcome_source_event_id,
    outcome.outcome_source_request_sha256,
    outcome.recording_id AS outcome_recording_id,
    outcome.played_ms AS outcome_played_ms,
    outcome.completion_ratio AS outcome_completion_ratio,
    outcome.excluded_from_taste AS outcome_excluded_from_taste
FROM fully_bound_requests AS request
JOIN attributed_outcomes AS outcome
  ON outcome.recommendation_request_id = request.recommendation_request_id
 AND outcome.outcome_order = 1
ORDER BY request.created_at, request.request_sha256
"""
)


class SqlAlchemySonaSourcePlanningReader:
    """Read owner-bearing observations only inside an isolated read transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def read(self, *, source_captured_at_ms: int) -> tuple[SonaSourceRequestObservation, ...]:
        return tuple(
            _observation(row)
            for row in self._read_rows(source_captured_at_ms=source_captured_at_ms)
        )

    def read_records(self, *, source_captured_at_ms: int) -> tuple[SonaSourcePlanningRecord, ...]:
        """Read transient replay inputs without serializing owner-bearing documents."""

        return tuple(
            _record(row) for row in self._read_rows(source_captured_at_ms=source_captured_at_ms)
        )

    def _read_rows(self, *, source_captured_at_ms: int) -> tuple[Mapping[str, object], ...]:
        if source_captured_at_ms < 0:
            raise ValueError("Sona source capture time is invalid")
        read_only = str(self._connection.scalar(text("SHOW transaction_read_only"))).lower()
        isolation = str(self._connection.scalar(text("SHOW transaction_isolation"))).lower()
        if read_only != "on" or isolation != "repeatable read":
            raise RuntimeError("sona_source_transaction_not_read_only_repeatable_read")
        head = str(self._connection.scalar(text("SELECT version_num FROM alembic_version")))
        if head != SONA_SOURCE_EXPECTED_ALEMBIC_HEAD:
            raise RuntimeError("sona_source_schema_not_0026")
        rows = self._connection.execute(
            _PLANNING_QUERY,
            {"source_captured_at_ms": source_captured_at_ms},
        ).mappings()
        return tuple(cast(Mapping[str, object], row) for row in rows)


def _record(row: Mapping[str, object]) -> SonaSourcePlanningRecord:
    observation = _observation(row)
    try:
        ratio = row.get("outcome_completion_ratio")
        if ratio is not None and (isinstance(ratio, bool) or not isinstance(ratio, int | float)):
            raise ValueError("outcome_completion_ratio")
        retained_until = row.get("retained_until")
        if not isinstance(retained_until, datetime):
            raise ValueError("retained_until")
        return SonaSourcePlanningRecord(
            observation=observation,
            request_document=_document(row, "request_document"),
            snapshot_document=_document(row, "snapshot_document"),
            baseline_snapshot_id=_uuid(row, "baseline_snapshot_id"),
            input_snapshot_sha256=_string(row, "input_snapshot_sha256"),
            interaction_watermark=_integer(row, "interaction_watermark"),
            catalog_snapshot=_integer(row, "catalog_snapshot"),
            availability_snapshot=_string(row, "availability_snapshot"),
            policy_snapshot_sha256=_string(row, "policy_snapshot_sha256"),
            retained_until=retained_until,
            outcome_source_event_id=_uuid(row, "outcome_source_event_id"),
            outcome_source_request_sha256=_string(
                row,
                "outcome_source_request_sha256",
            ),
            outcome_recording_id=_uuid(row, "outcome_recording_id"),
            outcome_played_ms=_integer(row, "outcome_played_ms"),
            outcome_completion_ratio=None if ratio is None else float(ratio),
            outcome_excluded_from_taste=_boolean(
                row,
                "outcome_excluded_from_taste",
            ),
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("sona_source_planning_result_invalid") from error


def _observation(row: Mapping[str, object]) -> SonaSourceRequestObservation:
    request_sha256 = row.get("request_sha256")
    request_document = row.get("request_document")
    input_snapshot_sha256 = row.get("input_snapshot_sha256")
    snapshot_document = row.get("snapshot_document")
    owner_user_id = row.get("user_id")
    cutoff_at_ms = row.get("cutoff_at_ms")
    observed_at_ms = row.get("observed_at_ms")
    if not isinstance(request_sha256, str):
        raise RuntimeError("sona_source_planning_result_invalid")
    if not isinstance(request_document, dict):
        raise RuntimeError("sona_source_planning_result_invalid")
    if not isinstance(input_snapshot_sha256, str):
        raise RuntimeError("sona_source_planning_result_invalid")
    if not isinstance(snapshot_document, dict):
        raise RuntimeError("sona_source_planning_result_invalid")
    if not isinstance(owner_user_id, UUID):
        raise RuntimeError("sona_source_planning_result_invalid")
    if isinstance(cutoff_at_ms, bool) or not isinstance(cutoff_at_ms, int):
        raise RuntimeError("sona_source_planning_result_invalid")
    if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int):
        raise RuntimeError("sona_source_planning_result_invalid")
    _verify_canonical_document_hash(cast(dict[str, JsonValue], request_document), request_sha256)
    _verify_canonical_document_hash(
        cast(dict[str, JsonValue], snapshot_document), input_snapshot_sha256
    )
    try:
        return SonaSourceRequestObservation(
            request_sha256=request_sha256,
            owner_user_id=owner_user_id,
            cutoff_at_ms=cutoff_at_ms,
            observed_at_ms=observed_at_ms,
        )
    except ValueError as error:
        raise RuntimeError("sona_source_planning_result_invalid") from error


def _verify_canonical_document_hash(document: dict[str, JsonValue], expected: str) -> None:
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeError("sona_source_planning_result_invalid")
    try:
        actual = sha256(rfc8785.dumps(document)).hexdigest()
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
        raise RuntimeError("sona_source_planning_result_invalid") from error
    if actual != expected:
        raise RuntimeError("sona_source_planning_canonical_hash_mismatch")


def _document(row: Mapping[str, object], field: str) -> dict[str, JsonValue]:
    value = row.get(field)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(field)
    return cast(dict[str, JsonValue], value)


def _string(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise ValueError(field)
    return value


def _integer(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(field)
    return value


def _boolean(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise ValueError(field)
    return value


def _uuid(row: Mapping[str, object], field: str) -> UUID:
    value = row.get(field)
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError(field)
    return UUID(value)


__all__ = ("SqlAlchemySonaSourcePlanningReader",)
