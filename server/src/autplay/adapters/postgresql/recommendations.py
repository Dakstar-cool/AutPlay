"""PostgreSQL snapshot, trace and offline-pack adapters for P11."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid7

import rfc8785
from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from autplay.application.recommendations import pipeline_manifest_document
from autplay.domain.recommendations import (
    CandidateContribution,
    ComponentVersionRef,
    JsonValue,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)

from .models import (
    OfflineRecommendationPackRow,
    RecommendationInputSnapshotRow,
    RecommendationItemRow,
    RecommendationPipelineVersionRow,
    RecommendationRequestRow,
)

_MAX_SNAPSHOT_TRACKS = 5_000
_SNAPSHOT_CLEANUP_BATCH = 100


class SqlAlchemyRecommendationRuntime:
    """Short-transaction owner-filtered P11 persistence adapter."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def capture(self, user_id: UUID, *, retained_until: datetime) -> RecommendationInputSnapshot:
        """Capture one bounded immutable ACL/catalog/history snapshot."""
        with self._sessions() as session:
            # Keep the catalog/history rows and their watermark on one MVCC view.
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            session.execute(
                text(_PURGE_EXPIRED_SNAPSHOTS_SQL),
                {"user_id": user_id, "limit": _SNAPSHOT_CLEANUP_BATCH},
            )
            rows = session.execute(
                text(_SNAPSHOT_SQL),
                {"user_id": user_id, "limit": _MAX_SNAPSHOT_TRACKS},
            ).mappings()
            tracks = tuple(_track_from_mapping(row) for row in rows)
            watermark = int(
                session.scalar(
                    text(
                        "SELECT COALESCE(max(server_sequence), 0) FROM sync.sync_event "
                        "WHERE user_id = :user_id AND event_type IN "
                        "('LISTENING_EVENT_RECORDED', 'RECOMMENDATION_IMPRESSION_RECORDED', "
                        "'RECOMMENDATION_FEEDBACK_RECORDED', 'USER_TRACK_PREFERENCE_SET')"
                    ),
                    {"user_id": user_id},
                )
                or 0
            )
            document: dict[str, JsonValue] = {
                "schema_version": 1,
                "user_id": str(user_id),
                "interaction_watermark": watermark,
                "selection_policy": "most_recent_added_then_recording_id_v1",
                "max_tracks": _MAX_SNAPSHOT_TRACKS,
                "tracks": [_track_document(track) for track in tracks],
            }
            encoded = _canonical_bytes(document)
            availability_document: dict[str, JsonValue] = {
                "tracks": [
                    {
                        "recording_id": str(track.recording_id),
                        "availability": track.availability,
                        "authorized": track.authorized,
                    }
                    for track in tracks
                ]
            }
            policy_document: dict[str, JsonValue] = {
                "tracks": [
                    {
                        "recording_id": str(track.recording_id),
                        "identity_status": track.identity_status,
                        "preference": track.preference,
                        "excluded": track.excluded,
                    }
                    for track in tracks
                ]
            }
            snapshot_id = uuid7()
            input_digest = sha256(encoded)
            availability_digest = sha256(_canonical_bytes(availability_document)).hexdigest()
            policy_digest = sha256(_canonical_bytes(policy_document))
            catalog_snapshot = max(
                (max(track.added_at_ms, track.last_played_at_ms or 0) for track in tracks),
                default=0,
            )
            row = RecommendationInputSnapshotRow(
                recommendation_input_snapshot_id=snapshot_id,
                user_id=user_id,
                input_snapshot_sha256=input_digest.digest(),
                interaction_watermark=watermark,
                catalog_snapshot=catalog_snapshot,
                availability_snapshot=availability_digest,
                policy_snapshot_sha256=policy_digest.digest(),
                snapshot_document=document,
                retained_until=retained_until,
            )
            session.add(row)
            session.commit()
            return RecommendationInputSnapshot(
                RecommendationSnapshotRef(
                    snapshot_id,
                    input_digest.hexdigest(),
                    watermark,
                    catalog_snapshot,
                    availability_digest,
                    policy_digest.hexdigest(),
                ),
                tracks,
                retained_until,
            )

    def load(self, user_id: UUID, snapshot_id: UUID) -> RecommendationInputSnapshot | None:
        """Load an unexpired retained snapshot without substituting current state."""
        with self._sessions() as session:
            row = session.scalar(
                select(RecommendationInputSnapshotRow).where(
                    RecommendationInputSnapshotRow.user_id == user_id,
                    RecommendationInputSnapshotRow.recommendation_input_snapshot_id == snapshot_id,
                    RecommendationInputSnapshotRow.retained_until > datetime.now(UTC),
                )
            )
            if row is None:
                return None
            document = _json_object(row.snapshot_document)
            track_values = document.get("tracks")
            if not isinstance(track_values, list) or len(track_values) > _MAX_SNAPSHOT_TRACKS:
                return None
            try:
                tracks = tuple(_track_from_document(_json_object(value)) for value in track_values)
            except KeyError, TypeError, ValueError:
                return None
            return RecommendationInputSnapshot(
                RecommendationSnapshotRef(
                    row.recommendation_input_snapshot_id,
                    row.input_snapshot_sha256.hex(),
                    row.interaction_watermark,
                    row.catalog_snapshot,
                    row.availability_snapshot,
                    row.policy_snapshot_sha256.hex(),
                ),
                tracks,
                row.retained_until,
            )

    def ensure_pipeline(self, pipeline: PipelineDefinition) -> None:
        """Idempotently register one immutable manifest and reject identity reuse."""
        manifest = pipeline_manifest_document(pipeline)
        digest = sha256(_canonical_bytes(manifest)).digest()
        if digest.hex() != pipeline.manifest_sha256:
            raise ValueError("pipeline manifest hash mismatch")
        with self._sessions() as session:
            session.execute(
                pg_insert(RecommendationPipelineVersionRow)
                .values(
                    pipeline_key=pipeline.pipeline_key,
                    version=pipeline.version,
                    implementation_revision=pipeline.implementation_revision,
                    request_schema_version=1,
                    canonicalization_version=1,
                    manifest=manifest,
                    manifest_sha256=digest,
                    lifecycle_status=pipeline.lifecycle_status,
                )
                .on_conflict_do_nothing(index_elements=["pipeline_key", "version"])
            )
            session.commit()
            row = session.get(
                RecommendationPipelineVersionRow,
                (pipeline.pipeline_key, pipeline.version),
            )
            if row is None or (
                row.manifest_sha256 != digest
                or row.implementation_revision != pipeline.implementation_revision
                or row.lifecycle_status != pipeline.lifecycle_status
            ):
                raise ValueError("pipeline version identity is already registered")

    def save(self, response: RecommendationResponse) -> None:
        """Atomically save the request and every final ranked item."""
        trace = response.request
        pipeline = trace.pipeline
        with self._sessions() as session:
            session.add(
                RecommendationRequestRow(
                    recommendation_request_id=trace.recommendation_request_id,
                    user_id=trace.query.user_id,
                    context=trace.query.context,
                    surface=trace.query.surface.value,
                    pipeline_key=pipeline.pipeline_key,
                    pipeline_version=pipeline.version,
                    pipeline_manifest_sha256=bytes.fromhex(pipeline.manifest_sha256),
                    request_schema_version=trace.query.schema_version,
                    request_canonicalization_version=trace.query.canonicalization_version,
                    request_sha256=bytes.fromhex(trace.request_sha256),
                    recommendation_input_snapshot_id=trace.snapshot.snapshot_id,
                    input_snapshot_sha256=bytes.fromhex(trace.snapshot.input_snapshot_sha256),
                    interaction_watermark=trace.snapshot.interaction_watermark,
                    catalog_snapshot=trace.snapshot.catalog_snapshot,
                    availability_snapshot_ref=trace.snapshot.availability_snapshot,
                    policy_snapshot_sha256=bytes.fromhex(trace.snapshot.policy_snapshot_sha256),
                    request_document=trace.canonical_request,
                    shadow=trace.query.shadow,
                    model_bundle_version=f"{pipeline.pipeline_key}:{pipeline.version}",
                    candidate_policy_version=_component_version(pipeline, "candidate_generator"),
                    filter_policy_version=_component_version(pipeline, "filter"),
                    reranker_version=_component_version(pipeline, "reranker"),
                    seed=trace.query.seed,
                    request_features={
                        "limit": trace.query.limit,
                        "exploration": trace.query.exploration,
                    },
                    created_at=trace.created_at,
                )
            )
            session.flush()
            for item in response.items:
                session.add(
                    RecommendationItemRow(
                        recommendation_request_id=trace.recommendation_request_id,
                        rank=item.source_rank,
                        recording_id=item.recording_id,
                        score=Decimal(str(item.score)),
                        candidate_sources=[value.source_key for value in item.contributions],
                        explanation_code=item.reason_code,
                        availability_snapshot={
                            "snapshot_sha256": trace.snapshot.availability_snapshot
                        },
                        contributions=[
                            _contribution_document(value) for value in item.contributions
                        ],
                        reason_codes=list(item.reason_codes),
                        item_provenance={
                            "artist_key": item.artist_key,
                            "release_key": item.release_key,
                            "section": item.section,
                        },
                    )
                )
            session.commit()

    def exact(self, user_id: UUID, request_id: UUID) -> RecommendationResponse | None:
        """Return persisted response bytes semantically, never rerunning the pipeline."""
        with self._sessions() as session:
            trace = self._trace(session, user_id, request_id)
            if trace is None:
                return None
            rows = list(
                session.scalars(
                    select(RecommendationItemRow)
                    .where(RecommendationItemRow.recommendation_request_id == request_id)
                    .order_by(RecommendationItemRow.rank)
                )
            )
            return RecommendationResponse(trace, tuple(_ranked_from_row(row) for row in rows))

    def request(self, user_id: UUID, request_id: UUID) -> RecommendationRequestTrace | None:
        """Return one replay-complete owner-filtered request trace."""
        with self._sessions() as session:
            return self._trace(session, user_id, request_id)

    def save_pack(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        response: RecommendationResponse,
        offline_pack_id: UUID,
        payload: bytes,
        payload_sha256: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Persist one owner/device-bound canonical pack."""
        trace = response.request
        with self._sessions() as session:
            session.add(
                OfflineRecommendationPackRow(
                    offline_pack_id=offline_pack_id,
                    user_id=user_id,
                    device_id=device_id,
                    recommendation_request_id=trace.recommendation_request_id,
                    pipeline_key=trace.pipeline.pipeline_key,
                    pipeline_version=trace.pipeline.version,
                    input_snapshot_sha256=bytes.fromhex(trace.snapshot.input_snapshot_sha256),
                    catalog_snapshot=trace.snapshot.catalog_snapshot,
                    model_bundle_version=(
                        f"{trace.pipeline.pipeline_key}:{trace.pipeline.version}"
                    ),
                    payload_version=1,
                    payload_encoding="RAW_JSON",
                    payload=payload,
                    payload_sha256=payload_sha256,
                    created_at=created_at,
                    expires_at=expires_at,
                )
            )
            session.commit()

    def _trace(
        self, session: Session, user_id: UUID, request_id: UUID
    ) -> RecommendationRequestTrace | None:
        statement: Select[tuple[RecommendationRequestRow]] = select(RecommendationRequestRow).where(
            RecommendationRequestRow.recommendation_request_id == request_id,
            RecommendationRequestRow.user_id == user_id,
        )
        row = session.scalar(statement)
        if row is None or not _is_replay_complete(row):
            return None
        pipeline_row = session.get(
            RecommendationPipelineVersionRow,
            (cast(str, row.pipeline_key), cast(str, row.pipeline_version)),
        )
        if pipeline_row is None:
            return None
        request_document = _json_object(row.request_document)
        snapshot_id = row.recommendation_input_snapshot_id or _snapshot_id(request_document)
        if snapshot_id is None:
            return None
        query = _query_from_document(request_document)
        pipeline = _pipeline_from_row(pipeline_row)
        return RecommendationRequestTrace(
            recommendation_request_id=row.recommendation_request_id,
            query=query,
            pipeline=pipeline,
            snapshot=RecommendationSnapshotRef(
                snapshot_id,
                cast(bytes, row.input_snapshot_sha256).hex(),
                cast(int, row.interaction_watermark),
                cast(int, row.catalog_snapshot),
                cast(str, row.availability_snapshot_ref),
                cast(bytes, row.policy_snapshot_sha256).hex(),
            ),
            request_sha256=cast(bytes, row.request_sha256).hex(),
            canonical_request=request_document,
            created_at=row.created_at,
        )


class SqlAlchemyOfflinePackRepository:
    """Named adapter avoiding ambiguity between trace and pack ``save`` methods."""

    def __init__(self, runtime: SqlAlchemyRecommendationRuntime) -> None:
        self._runtime = runtime

    def save(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        response: RecommendationResponse,
        offline_pack_id: UUID,
        payload: bytes,
        payload_sha256: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self._runtime.save_pack(
            user_id=user_id,
            device_id=device_id,
            response=response,
            offline_pack_id=offline_pack_id,
            payload=payload,
            payload_sha256=payload_sha256,
            created_at=created_at,
            expires_at=expires_at,
        )


def _is_replay_complete(row: RecommendationRequestRow) -> bool:
    return all(
        value is not None
        for value in (
            row.surface,
            row.pipeline_key,
            row.pipeline_version,
            row.pipeline_manifest_sha256,
            row.request_schema_version,
            row.request_canonicalization_version,
            row.request_sha256,
            row.input_snapshot_sha256,
            row.interaction_watermark,
            row.catalog_snapshot,
            row.availability_snapshot_ref,
            row.policy_snapshot_sha256,
            row.request_document,
        )
    )


def _snapshot_id(request_document: Mapping[str, JsonValue]) -> UUID | None:
    value = request_document.get("snapshot")
    if not isinstance(value, dict):
        return None
    raw = value.get("snapshot_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _query_from_document(value: Mapping[str, JsonValue]) -> RecommendationQuery:
    return RecommendationQuery(
        user_id=UUID(_string(value, "user_id")),
        surface=RecommendationSurface(_string(value, "surface")),
        context=_string(value, "context"),
        limit=_integer(value, "limit"),
        exploration=_number(value, "exploration"),
        seed=_integer(value, "seed"),
        schema_version=_integer(value, "schema_version"),
        canonicalization_version=_integer(value, "canonicalization_version"),
        shadow=_boolean(value, "shadow"),
    )


def _pipeline_from_row(row: RecommendationPipelineVersionRow) -> PipelineDefinition:
    manifest = _json_object(row.manifest)
    component_values = manifest.get("components")
    budget_values = manifest.get("generator_budgets")
    if not isinstance(component_values, list) or not isinstance(budget_values, list):
        raise ValueError("pipeline manifest is invalid")
    components = tuple(
        ComponentVersionRef(
            _string(_json_object(value), "key"),
            _string(_json_object(value), "kind"),
            _string(_json_object(value), "version"),
            _string(_json_object(value), "config_sha256"),
        )
        for value in component_values
    )
    budgets: list[tuple[str, int]] = []
    for value in budget_values:
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not isinstance(value[0], str)
            or not isinstance(value[1], int)
        ):
            raise ValueError("pipeline generator budget is invalid")
        budgets.append((value[0], value[1]))
    return PipelineDefinition(
        row.pipeline_key,
        row.version,
        row.implementation_revision,
        row.manifest_sha256.hex(),
        components,
        tuple(budgets),
        _integer(manifest, "max_artist_repeat"),
        _integer(manifest, "max_release_repeat"),
        row.lifecycle_status,
    )


def _ranked_from_row(row: RecommendationItemRow) -> RankedRecommendation:
    contribution_values = row.contributions
    if not isinstance(contribution_values, list):
        raise ValueError("recommendation contributions are invalid")
    contributions = tuple(
        CandidateContribution(
            _string(_json_object(value), "source_key"),
            _string(_json_object(value), "source_version"),
            _integer(_json_object(value), "source_rank"),
            _number(_json_object(value), "raw_score"),
            _json_object(_json_object(value).get("provenance")),
        )
        for value in contribution_values
    )
    provenance = _json_object(row.item_provenance)
    release = provenance.get("release_key")
    return RankedRecommendation(
        row.recording_id,
        row.rank,
        float(row.score),
        row.explanation_code,
        tuple(row.reason_codes),
        contributions,
        _string(provenance, "artist_key"),
        release if isinstance(release, str) else None,
        _string(provenance, "section"),
    )


def _component_version(pipeline: PipelineDefinition, kind: str) -> str:
    return ",".join(
        f"{value.key}:{value.version}:{value.config_sha256}"
        for value in pipeline.components
        if value.kind == kind
    )


def _contribution_document(value: CandidateContribution) -> dict[str, JsonValue]:
    return {
        "source_key": value.source_key,
        "source_version": value.source_version,
        "source_rank": value.source_rank,
        "raw_score": value.raw_score,
        "provenance": value.provenance,
    }


def _track_from_mapping(value: RowMapping) -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=cast(UUID, value["recording_id"]),
        user_track_ref_id=cast(UUID | None, value["user_track_ref_id"]),
        artist_key=str(value["artist_key"]),
        release_key=None if value["release_key"] is None else str(value["release_key"]),
        metadata_tokens=tuple(
            str(item) for item in cast(Sequence[object], value["metadata_tokens"])
        ),
        availability=str(value["availability"]),
        authorized=bool(value["authorized"]),
        identity_status=str(value["identity_status"]),
        preference=str(value["preference"]),
        excluded=bool(value["excluded"]),
        play_count=cast(int, value["play_count"]),
        organic_play_count=cast(int, value["organic_play_count"]),
        recommended_play_count=cast(int, value["recommended_play_count"]),
        last_played_at_ms=(
            None if value["last_played_at_ms"] is None else cast(int, value["last_played_at_ms"])
        ),
        added_at_ms=cast(int, value["added_at_ms"]),
        release_date_ordinal=(
            None
            if value["release_date_ordinal"] is None
            else cast(int, value["release_date_ordinal"])
        ),
    )


def _track_document(track: SnapshotTrack) -> dict[str, JsonValue]:
    return {
        "recording_id": str(track.recording_id),
        "user_track_ref_id": (
            None if track.user_track_ref_id is None else str(track.user_track_ref_id)
        ),
        "artist_key": track.artist_key,
        "release_key": track.release_key,
        "metadata_tokens": list(track.metadata_tokens),
        "availability": track.availability,
        "authorized": track.authorized,
        "identity_status": track.identity_status,
        "preference": track.preference,
        "excluded": track.excluded,
        "play_count": track.play_count,
        "organic_play_count": track.organic_play_count,
        "recommended_play_count": track.recommended_play_count,
        "last_played_at_ms": track.last_played_at_ms,
        "added_at_ms": track.added_at_ms,
        "release_date_ordinal": track.release_date_ordinal,
    }


def _track_from_document(value: Mapping[str, JsonValue]) -> SnapshotTrack:
    ref = value.get("user_track_ref_id")
    release = value.get("release_key")
    tokens = value.get("metadata_tokens")
    if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
        raise ValueError("snapshot metadata tokens are invalid")
    return SnapshotTrack(
        UUID(_string(value, "recording_id")),
        UUID(ref) if isinstance(ref, str) else None,
        _string(value, "artist_key"),
        release if isinstance(release, str) else None,
        tuple(cast(list[str], tokens)),
        _string(value, "availability"),
        _boolean(value, "authorized"),
        _string(value, "identity_status"),
        _string(value, "preference"),
        _boolean(value, "excluded"),
        _integer(value, "play_count"),
        _integer(value, "organic_play_count"),
        _integer(value, "recommended_play_count"),
        _optional_integer(value, "last_played_at_ms"),
        _integer(value, "added_at_ms"),
        _optional_integer(value, "release_date_ordinal"),
    )


def _json_object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return cast(dict[str, JsonValue], value)


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(key)
    return item


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(key)
    return item


def _optional_integer(value: Mapping[str, JsonValue], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    return _integer(value, key)


def _number(value: Mapping[str, JsonValue], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, int | float) or isinstance(item, bool):
        raise ValueError(key)
    return float(item)


def _boolean(value: Mapping[str, JsonValue], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(key)
    return item


def _canonical_bytes(value: dict[str, JsonValue]) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
        raise ValueError("recommendation persistence document is invalid") from error


_SNAPSHOT_SQL = """
WITH history AS (
    SELECT recording_id,
           count(*) FILTER (WHERE NOT excluded_from_taste) AS play_count,
           count(*) FILTER (
               WHERE NOT excluded_from_taste AND event_origin <> 'RECOMMENDED'
           ) AS organic_play_count,
           count(*) FILTER (
               WHERE NOT excluded_from_taste AND event_origin = 'RECOMMENDED'
           ) AS recommended_play_count,
           max(started_at) FILTER (WHERE NOT excluded_from_taste) AS last_played_at
    FROM library.listening_event
    WHERE user_id = :user_id AND recording_id IS NOT NULL
    GROUP BY recording_id
), release_metadata AS (
    SELECT DISTINCT ON (rt.recording_id)
           rt.recording_id, rel.release_id, rel.release_date
    FROM catalog.release_track rt
    JOIN catalog.medium medium ON medium.medium_id = rt.medium_id
    JOIN catalog.release rel ON rel.release_id = medium.release_id
    WHERE rt.deleted_at IS NULL AND medium.deleted_at IS NULL AND rel.deleted_at IS NULL
    ORDER BY rt.recording_id, rel.release_date DESC NULLS LAST, rel.release_id
), library_candidates AS (
    SELECT recording.recording_id, ref.user_track_ref_id,
           recording.artist_credit_id::text AS artist_key,
           release.release_id::text AS release_key,
           ARRAY[
               recording.recording_kind,
               coalesce(recording.version_text, ''),
               recording.normalized_title
           ]::text[] AS metadata_tokens,
           entry.availability_status AS availability,
           true AS authorized,
           recording.identity_status,
           coalesce(preference.preference, 'NEUTRAL') AS preference,
           coalesce(preference.excluded_from_taste, false) AS excluded,
           coalesce(history.play_count, 0)::bigint AS play_count,
           coalesce(history.organic_play_count, 0)::bigint AS organic_play_count,
           coalesce(history.recommended_play_count, 0)::bigint AS recommended_play_count,
           CASE WHEN history.last_played_at IS NULL THEN NULL ELSE
               floor(extract(epoch FROM history.last_played_at) * 1000)::bigint
           END AS last_played_at_ms,
           floor(extract(epoch FROM entry.added_at) * 1000)::bigint AS added_at_ms,
           CASE WHEN release.release_date IS NULL THEN NULL ELSE
               release.release_date - DATE '0001-01-01' + 1
           END::integer AS release_date_ordinal,
           0 AS source_priority
    FROM library.library_entry entry
    JOIN library.user_track_ref ref ON ref.user_track_ref_id = entry.user_track_ref_id
    JOIN catalog.recording recording ON recording.recording_id = ref.recording_id
    LEFT JOIN library.user_track_preference preference
        ON preference.user_track_ref_id = ref.user_track_ref_id
    LEFT JOIN history ON history.recording_id = recording.recording_id
    LEFT JOIN release_metadata release ON release.recording_id = recording.recording_id
    WHERE entry.user_id = :user_id
      AND entry.removed_at IS NULL
      AND ref.deleted_at IS NULL
      AND recording.deleted_at IS NULL
), vault_candidates AS (
    SELECT recording.recording_id, owned_ref.user_track_ref_id,
           recording.artist_credit_id::text AS artist_key,
           release.release_id::text AS release_key,
           ARRAY[
               recording.recording_kind,
               coalesce(recording.version_text, ''),
               recording.normalized_title
           ]::text[] AS metadata_tokens,
           'VAULT'::text AS availability,
           true AS authorized,
           recording.identity_status,
           coalesce(owned_ref.preference, 'NEUTRAL') AS preference,
           coalesce(owned_ref.excluded, false) AS excluded,
           coalesce(history.play_count, 0)::bigint AS play_count,
           coalesce(history.organic_play_count, 0)::bigint AS organic_play_count,
           coalesce(history.recommended_play_count, 0)::bigint AS recommended_play_count,
           CASE WHEN history.last_played_at IS NULL THEN NULL ELSE
               floor(extract(epoch FROM history.last_played_at) * 1000)::bigint
           END AS last_played_at_ms,
           floor(extract(epoch FROM variant.created_at) * 1000)::bigint AS added_at_ms,
           CASE WHEN release.release_date IS NULL THEN NULL ELSE
               release.release_date - DATE '0001-01-01' + 1
           END::integer AS release_date_ordinal,
           1 AS source_priority
    FROM vault.acquisition_record acquisition
    JOIN vault.audio_variant variant ON variant.audio_variant_id = acquisition.audio_variant_id
    JOIN vault.vault_object object ON object.vault_object_id = variant.vault_object_id
    JOIN catalog.recording recording ON recording.recording_id = variant.recording_id
    LEFT JOIN history ON history.recording_id = recording.recording_id
    LEFT JOIN release_metadata release ON release.recording_id = recording.recording_id
    LEFT JOIN LATERAL (
        SELECT ref.user_track_ref_id, preference.preference,
               preference.excluded_from_taste AS excluded
        FROM library.user_track_ref ref
        LEFT JOIN library.user_track_preference preference
            ON preference.user_track_ref_id = ref.user_track_ref_id
        WHERE ref.user_id = :user_id
          AND ref.recording_id = recording.recording_id
          AND ref.deleted_at IS NULL
        ORDER BY ref.updated_at DESC, ref.user_track_ref_id
        LIMIT 1
    ) owned_ref ON true
    WHERE acquisition.authorized_by_user_id = :user_id
      AND variant.validation_status = 'VALID'
      AND variant.deleted_at IS NULL
      AND object.commit_status = 'COMMITTED'
      AND EXISTS (
          SELECT 1 FROM vault.vault_replica replica
          WHERE replica.vault_object_id = object.vault_object_id
            AND replica.replica_status = 'AVAILABLE'
      )
      AND recording.deleted_at IS NULL
), combined AS (
    SELECT * FROM library_candidates
    UNION ALL
    SELECT * FROM vault_candidates
), deduped AS (
    SELECT DISTINCT ON (recording_id)
           recording_id, user_track_ref_id, artist_key, release_key, metadata_tokens,
           availability, authorized, identity_status, preference, excluded,
           play_count, organic_play_count, recommended_play_count, last_played_at_ms,
           added_at_ms, release_date_ordinal
    FROM combined
    ORDER BY recording_id, source_priority, added_at_ms DESC
), selected AS (
    SELECT *
    FROM deduped
    ORDER BY added_at_ms DESC, recording_id
    LIMIT :limit
)
SELECT
       recording_id, user_track_ref_id, artist_key, release_key, metadata_tokens,
       availability, authorized, identity_status, preference, excluded,
       play_count, organic_play_count, recommended_play_count, last_played_at_ms,
       added_at_ms, release_date_ordinal
FROM selected
ORDER BY recording_id
"""

_PURGE_EXPIRED_SNAPSHOTS_SQL = """
WITH expired AS (
    SELECT recommendation_input_snapshot_id
    FROM ml.recommendation_input_snapshot
    WHERE user_id = :user_id AND retained_until <= now()
    ORDER BY retained_until, recommendation_input_snapshot_id
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
)
DELETE FROM ml.recommendation_input_snapshot snapshot
USING expired
WHERE snapshot.recommendation_input_snapshot_id = expired.recommendation_input_snapshot_id
"""


__all__ = ("SqlAlchemyOfflinePackRepository", "SqlAlchemyRecommendationRuntime")
