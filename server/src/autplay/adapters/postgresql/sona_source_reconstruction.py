"""Read and validate accepted owner event truth from an isolated schema-0026 restore."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import Connection, text

from autplay.application.sona_source_preflight import SONA_SOURCE_EXPECTED_ALEMBIC_HEAD
from autplay.application.sona_source_reconstruction import Sona0026AcceptedEvent
from autplay.domain.adaptive_recommendations import DimensionKind, TemporalDimension

_SOURCE_EVENT_QUERY = text(
    r"""
WITH accepted AS (
    SELECT
        server_event.event_id,
        server_event.user_id,
        inbox.device_id,
        inbox.device_sequence,
        server_event.server_sequence,
        server_event.event_type,
        server_event.aggregate_id,
        server_event.payload,
        floor(extract(epoch FROM inbox.occurred_at) * 1000)::bigint AS occurred_at_ms,
        floor(extract(epoch FROM inbox.received_at) * 1000)::bigint AS received_at_ms,
        encode(inbox.request_hash, 'hex') AS source_request_sha256
    FROM sync.sync_event AS server_event
    JOIN sync.device_event_inbox AS inbox
      ON inbox.event_id = server_event.event_id
     AND inbox.user_id = server_event.user_id
     AND inbox.device_id = server_event.origin_device_id
     AND inbox.device_sequence >= 1
     AND inbox.event_type = server_event.event_type
     AND inbox.schema_version = server_event.schema_version
     AND inbox.aggregate_type = server_event.aggregate_type
     AND inbox.aggregate_id = server_event.aggregate_id
     AND inbox.payload = server_event.payload
     AND inbox.apply_status = 'APPLIED'
     AND octet_length(inbox.request_hash) = 32
    WHERE server_event.user_id = :owner_user_id
      AND server_event.server_sequence BETWEEN 1 AND :interaction_watermark
      AND server_event.schema_version = 1
      AND server_event.operation = 'UPSERT'
      AND server_event.event_type IN (
          'USER_TRACK_PREFERENCE_SET',
          'LISTENING_EVENT_RECORDED',
          'RECOMMENDATION_FEEDBACK_RECORDED'
      )
      AND inbox.received_at <= to_timestamp(:cutoff_at_ms / 1000.0)
      AND server_event.created_at <= to_timestamp(:cutoff_at_ms / 1000.0)
), release_metadata AS (
    SELECT DISTINCT ON (release_track.recording_id)
        release_track.recording_id,
        release.release_id
    FROM catalog.release_track AS release_track
    JOIN catalog.medium AS medium
      ON medium.medium_id = release_track.medium_id
    JOIN catalog.release AS release
      ON release.release_id = medium.release_id
    WHERE release_track.deleted_at IS NULL
      AND medium.deleted_at IS NULL
      AND release.deleted_at IS NULL
    ORDER BY
        release_track.recording_id,
        release.release_date DESC NULLS LAST,
        release.release_id
), listening AS (
    SELECT
        accepted.*,
        listen.recording_id,
        recording.artist_credit_id::text AS artist_key,
        release.release_id::text AS release_key,
        NULL::text AS preference,
        listen.event_origin,
        listen.played_ms,
        listen.completion_ratio::double precision AS completion_ratio,
        listen.excluded_from_taste,
        NULL::text AS feedback_type,
        listen.recommendation_request_id,
        impression.interaction_id AS impression_event_id,
        CASE
            WHEN listen.event_origin = 'RECOMMENDED'
            THEN (accepted.payload #>> '{recommendation,source_rank}')::integer
            ELSE NULL::integer
        END AS recommendation_source_rank
    FROM accepted
    JOIN library.listening_event AS listen
      ON accepted.event_type = 'LISTENING_EVENT_RECORDED'
     AND listen.listening_event_id = accepted.event_id
     AND listen.user_id = accepted.user_id
     AND listen.device_id = accepted.device_id
     AND listen.recording_id IS NOT NULL
    JOIN catalog.recording AS recording
      ON recording.recording_id = listen.recording_id
    LEFT JOIN release_metadata AS release
      ON release.recording_id = listen.recording_id
    LEFT JOIN library.user_interaction_event AS impression
      ON impression.interaction_id = COALESCE(
          NULLIF(accepted.payload #>> '{recommendation,impression_event_server_id}', '')::uuid,
          NULLIF(accepted.payload #>> '{recommendation,impression_event_local_id}', '')::uuid
      )
     AND impression.user_id = accepted.user_id
     AND impression.device_id = accepted.device_id
     AND impression.event_type = 'RECOMMENDATION_IMPRESSION_RECORDED'
     AND impression.recommendation_request_id = listen.recommendation_request_id
     AND impression.recording_id = listen.recording_id
     AND impression.source_rank =
         (accepted.payload #>> '{recommendation,source_rank}')::integer
     AND impression.created_at <= listen.created_at
    WHERE accepted.payload ->> 'interaction_type' = 'LISTENING_EVENT_RECORDED'
      AND accepted.payload ->> 'recording_id' = listen.recording_id::text
      AND accepted.payload ->> 'event_origin' = listen.event_origin
      AND accepted.payload -> 'excluded_from_taste' = to_jsonb(listen.excluded_from_taste)
      AND (
          (listen.event_origin = 'RECOMMENDED' AND impression.interaction_id IS NOT NULL)
          OR
          (listen.event_origin <> 'RECOMMENDED' AND accepted.payload -> 'recommendation' = 'null')
      )
), preference AS (
    SELECT
        accepted.*,
        track_ref.recording_id,
        recording.artist_credit_id::text AS artist_key,
        release.release_id::text AS release_key,
        accepted.payload ->> 'preference' AS preference,
        NULL::text AS event_origin,
        NULL::bigint AS played_ms,
        NULL::double precision AS completion_ratio,
        (accepted.payload ->> 'excluded_from_taste')::boolean AS excluded_from_taste,
        NULL::text AS feedback_type,
        NULL::uuid AS recommendation_request_id,
        NULL::uuid AS impression_event_id,
        NULL::integer AS recommendation_source_rank
    FROM accepted
    JOIN library.user_track_ref AS track_ref
      ON accepted.event_type = 'USER_TRACK_PREFERENCE_SET'
     AND track_ref.user_id = accepted.user_id
     AND track_ref.user_track_ref_id = accepted.aggregate_id
     AND track_ref.recording_id IS NOT NULL
    JOIN catalog.recording AS recording
      ON recording.recording_id = track_ref.recording_id
    LEFT JOIN release_metadata AS release
      ON release.recording_id = track_ref.recording_id
    WHERE accepted.payload ->> 'local_user_track_ref_id' = track_ref.user_track_ref_id::text
      AND accepted.payload ->> 'preference' IN ('NEUTRAL', 'LIKED', 'DISLIKED')
      AND jsonb_typeof(accepted.payload -> 'excluded_from_taste') = 'boolean'
), feedback AS (
    SELECT
        accepted.*,
        interaction.recording_id,
        recording.artist_credit_id::text AS artist_key,
        release.release_id::text AS release_key,
        NULL::text AS preference,
        NULL::text AS event_origin,
        NULL::bigint AS played_ms,
        NULL::double precision AS completion_ratio,
        false AS excluded_from_taste,
        accepted.payload ->> 'feedback_type' AS feedback_type,
        interaction.recommendation_request_id,
        interaction.impression_interaction_id AS impression_event_id,
        interaction.source_rank AS recommendation_source_rank
    FROM accepted
    JOIN library.user_interaction_event AS interaction
      ON accepted.event_type = 'RECOMMENDATION_FEEDBACK_RECORDED'
     AND interaction.interaction_id = accepted.event_id
     AND interaction.user_id = accepted.user_id
     AND interaction.device_id = accepted.device_id
     AND interaction.event_type = accepted.event_type
     AND interaction.recording_id IS NOT NULL
     AND interaction.recommendation_request_id IS NOT NULL
     AND interaction.impression_interaction_id IS NOT NULL
     AND interaction.source_rank BETWEEN 1 AND 1000
    JOIN library.user_interaction_event AS impression
      ON impression.interaction_id = interaction.impression_interaction_id
     AND impression.user_id = interaction.user_id
     AND impression.device_id = interaction.device_id
     AND impression.event_type = 'RECOMMENDATION_IMPRESSION_RECORDED'
     AND impression.recommendation_request_id = interaction.recommendation_request_id
     AND impression.recording_id = interaction.recording_id
     AND impression.source_rank = interaction.source_rank
     AND impression.created_at <= interaction.created_at
    JOIN catalog.recording AS recording
      ON recording.recording_id = interaction.recording_id
    LEFT JOIN release_metadata AS release
      ON release.recording_id = interaction.recording_id
    WHERE accepted.payload ->> 'interaction_type' = 'RECOMMENDATION_FEEDBACK_RECORDED'
      AND accepted.payload ->> 'feedback_type' IN ('SELECTED', 'DISMISSED')
      AND accepted.payload #>> '{recommendation,recommendation_request_id}' =
          interaction.recommendation_request_id::text
      AND accepted.payload #>> '{recommendation,recording_id}' = interaction.recording_id::text
      AND (accepted.payload #>> '{recommendation,source_rank}')::integer =
          interaction.source_rank
      AND COALESCE(
          NULLIF(accepted.payload #>> '{recommendation,impression_event_server_id}', '')::uuid,
          NULLIF(accepted.payload #>> '{recommendation,impression_event_local_id}', '')::uuid
      ) = interaction.impression_interaction_id
)
SELECT * FROM listening
UNION ALL
SELECT * FROM preference
UNION ALL
SELECT * FROM feedback
ORDER BY server_sequence, event_id
"""
)


class SqlAlchemySona0026SourceEventReader:
    """Read only exact, owner-bound applied sync facts at one P11 boundary."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def read(
        self,
        *,
        owner_user_id: UUID,
        cutoff_at_ms: int,
        interaction_watermark: int,
    ) -> tuple[Sona0026AcceptedEvent, ...]:
        if cutoff_at_ms < 0 or interaction_watermark < 0:
            raise ValueError("Sona 0026 source boundary is invalid")
        read_only = str(self._connection.scalar(text("SHOW transaction_read_only"))).lower()
        isolation = str(self._connection.scalar(text("SHOW transaction_isolation"))).lower()
        if read_only != "on" or isolation != "repeatable read":
            raise RuntimeError("sona_0026_source_transaction_not_read_only_repeatable_read")
        head = str(self._connection.scalar(text("SELECT version_num FROM alembic_version")))
        if head != SONA_SOURCE_EXPECTED_ALEMBIC_HEAD:
            raise RuntimeError("sona_0026_source_schema_not_0026")
        rows = self._connection.execute(
            _SOURCE_EVENT_QUERY,
            {
                "owner_user_id": owner_user_id,
                "cutoff_at_ms": cutoff_at_ms,
                "interaction_watermark": interaction_watermark,
            },
        ).mappings()
        return tuple(_accepted_event(cast(Mapping[str, object], row)) for row in rows)


def _accepted_event(row: Mapping[str, object]) -> Sona0026AcceptedEvent:
    try:
        event_type = _string(row, "event_type")
        payload = _object(row.get("payload"), "payload")
        recording_id = _uuid(row, "recording_id")
        dimensions = _dimensions(row)
        preference: str | None = None
        event_origin: str | None = None
        played_ms: int | None = None
        completion_ratio: float | None = None
        feedback_type: str | None = None
        recommendation_request_id: UUID | None = None
        impression_event_id: UUID | None = None
        recommendation_source_rank: int | None = None
        if event_type == "USER_TRACK_PREFERENCE_SET":
            if payload.get("preference") != row.get("preference") or payload.get(
                "excluded_from_taste"
            ) != row.get("excluded_from_taste"):
                raise ValueError("preference projection mismatch")
            preference = _string(row, "preference")
        elif event_type == "LISTENING_EVENT_RECORDED":
            _verify_listening_payload(payload, row, recording_id)
            event_origin = _string(row, "event_origin")
            played_ms = _integer(row, "played_ms")
            completion_ratio = _optional_number(row, "completion_ratio")
            (
                recommendation_request_id,
                impression_event_id,
                recommendation_source_rank,
            ) = _causal_values(row)
        elif event_type == "RECOMMENDATION_FEEDBACK_RECORDED":
            _verify_feedback_payload(payload, row, recording_id)
            feedback_type = _string(row, "feedback_type")
            (
                recommendation_request_id,
                impression_event_id,
                recommendation_source_rank,
            ) = _causal_values(row)
        else:
            raise ValueError("unsupported source event")
        return Sona0026AcceptedEvent(
            source_event_id=_uuid(row, "event_id"),
            owner_user_id=_uuid(row, "user_id"),
            device_id=_uuid(row, "device_id"),
            device_sequence=_integer(row, "device_sequence"),
            server_sequence=_integer(row, "server_sequence"),
            source_event_type=event_type,
            recording_id=recording_id,
            dimensions=dimensions,
            occurred_at_ms=_integer(row, "occurred_at_ms"),
            received_at_ms=_integer(row, "received_at_ms"),
            source_request_sha256=_string(row, "source_request_sha256"),
            preference=preference,
            event_origin=event_origin,
            played_ms=played_ms,
            completion_ratio=completion_ratio,
            excluded_from_taste=_boolean(row, "excluded_from_taste"),
            feedback_type=feedback_type,
            recommendation_request_id=recommendation_request_id,
            impression_event_id=impression_event_id,
            recommendation_source_rank=recommendation_source_rank,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("sona_0026_source_event_invalid") from error


def _verify_listening_payload(
    payload: Mapping[str, object],
    row: Mapping[str, object],
    recording_id: UUID,
) -> None:
    ratio = payload.get("completion_ratio")
    row_ratio = _optional_number(row, "completion_ratio")
    recommendation = payload.get("recommendation")
    origin = _string(row, "event_origin")
    if (
        payload.get("interaction_type") != "LISTENING_EVENT_RECORDED"
        or payload.get("recording_id") != str(recording_id)
        or payload.get("event_origin") != origin
        or payload.get("played_ms") != row.get("played_ms")
        or payload.get("excluded_from_taste") != row.get("excluded_from_taste")
        or (None if ratio is None else float(cast(int | float | Decimal, ratio))) != row_ratio
    ):
        raise ValueError("listening projection mismatch")
    if origin == "RECOMMENDED":
        _verify_recommendation_payload(_object(recommendation, "recommendation"), row, recording_id)
    elif recommendation is not None:
        raise ValueError("non-recommendation listening attribution mismatch")


def _verify_feedback_payload(
    payload: Mapping[str, object],
    row: Mapping[str, object],
    recording_id: UUID,
) -> None:
    if payload.get("interaction_type") != "RECOMMENDATION_FEEDBACK_RECORDED" or payload.get(
        "feedback_type"
    ) != row.get("feedback_type"):
        raise ValueError("recommendation feedback projection mismatch")
    _verify_recommendation_payload(
        _object(payload.get("recommendation"), "recommendation"),
        row,
        recording_id,
    )


def _verify_recommendation_payload(
    recommendation: Mapping[str, object],
    row: Mapping[str, object],
    recording_id: UUID,
) -> None:
    impression = recommendation.get("impression_event_server_id") or recommendation.get(
        "impression_event_local_id"
    )
    if (
        recommendation.get("recommendation_request_id")
        != str(_uuid(row, "recommendation_request_id"))
        or recommendation.get("recording_id") != str(recording_id)
        or recommendation.get("source_rank") != _integer(row, "recommendation_source_rank")
        or impression != str(_uuid(row, "impression_event_id"))
    ):
        raise ValueError("recommendation attribution mismatch")


def _causal_values(row: Mapping[str, object]) -> tuple[UUID | None, UUID | None, int | None]:
    if row.get("recommendation_request_id") is None:
        if (
            row.get("impression_event_id") is not None
            or row.get("recommendation_source_rank") is not None
        ):
            raise ValueError("partial causal projection")
        return (None, None, None)
    return (
        _uuid(row, "recommendation_request_id"),
        _uuid(row, "impression_event_id"),
        _integer(row, "recommendation_source_rank"),
    )


def _dimensions(row: Mapping[str, object]) -> tuple[TemporalDimension, ...]:
    values = [TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, _string(row, "artist_key"))]
    release = row.get("release_key")
    if release is not None:
        if not isinstance(release, str):
            raise ValueError("release dimension is invalid")
        values.append(TemporalDimension(DimensionKind.CANONICAL_RELEASE_ID, release))
    return tuple(values)


def _object(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(field)
    return cast(dict[str, object], value)


def _string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise ValueError(field)
    return candidate


def _integer(value: Mapping[str, object], field: str) -> int:
    candidate = value.get(field)
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ValueError(field)
    return candidate


def _optional_number(value: Mapping[str, object], field: str) -> float | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    if isinstance(candidate, bool) or not isinstance(candidate, int | float | Decimal):
        raise ValueError(field)
    return float(candidate)


def _boolean(value: Mapping[str, object], field: str) -> bool:
    candidate = value.get(field)
    if not isinstance(candidate, bool):
        raise ValueError(field)
    return candidate


def _uuid(value: Mapping[str, object], field: str) -> UUID:
    candidate = value.get(field)
    if isinstance(candidate, UUID):
        return candidate
    if not isinstance(candidate, str):
        raise ValueError(field)
    return UUID(candidate)


__all__ = ("SqlAlchemySona0026SourceEventReader",)
