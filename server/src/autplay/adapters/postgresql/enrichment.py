"""PostgreSQL P12 model registry, fenced writer, activation and exact retrieval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

import rfc8785
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.domain.enrichment import (
    ApprovedEmbeddingModel,
    EmbeddingJobTarget,
    EmbeddingResult,
    GpuBenchmarkReport,
)
from autplay.domain.jobs import LeaseFence

from .models.jobs import JobRow
from .models.ml import (
    EmbeddingBenchmarkReportRow,
    EmbeddingModelActivationRow,
    EmbeddingModelRow,
    EnrichmentJobRow,
    RecordingEmbeddingRow,
    RecordingTagSetRow,
)
from .models.types import JsonValue

type SessionFactory = Callable[[], Session]


class EnrichmentPersistenceError(RuntimeError):
    """Stable P12 persistence invariant failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SqlAlchemyEnrichmentRuntime:
    """Short-transaction P12 registry, job and immutable-result adapter."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def get(self, embedding_model_id: UUID) -> ApprovedEmbeddingModel | None:
        """Resolve one approved registry manifest by immutable ID."""

        with self._sessions() as session:
            row = session.get(EmbeddingModelRow, embedding_model_id)
            return None if row is None else _approved_model(row)

    def register(
        self,
        model: ApprovedEmbeddingModel,
        *,
        artifact_manifest: dict[str, JsonValue],
        preprocessing_manifest: dict[str, JsonValue],
        license_review_reference: str,
    ) -> bool:
        """Register one reviewed immutable BENCHMARK model; exact replay is idempotent."""

        if model.status != "BENCHMARK":
            raise EnrichmentPersistenceError("ml.new_model_must_start_in_benchmark")
        if not 1 <= len(license_review_reference) <= 500:
            raise EnrichmentPersistenceError("ml.license_review_reference_invalid")
        try:
            artifact_hash = hashlib.sha256(rfc8785.dumps(artifact_manifest)).digest()
            preprocessing_hash = hashlib.sha256(rfc8785.dumps(preprocessing_manifest)).digest()
        except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
            raise EnrichmentPersistenceError("ml.model_manifest_invalid") from error
        if artifact_hash != model.manifest_sha256:
            raise EnrichmentPersistenceError("ml.model_manifest_hash_mismatch")
        if preprocessing_hash != model.preprocessing_sha256:
            raise EnrichmentPersistenceError("ml.preprocessing_manifest_hash_mismatch")
        with self._sessions() as session, session.begin():
            existing = session.get(EmbeddingModelRow, model.embedding_model_id)
            if existing is not None:
                if (
                    _approved_model(existing) != model
                    or existing.artifact_manifest != artifact_manifest
                    or existing.preprocessing_manifest != preprocessing_manifest
                    or existing.license_review_reference != license_review_reference
                ):
                    raise EnrichmentPersistenceError("ml.model_registry_conflict")
                return False
            session.add(
                EmbeddingModelRow(
                    embedding_model_id=model.embedding_model_id,
                    model_key=model.model_key,
                    version=model.version,
                    task=model.task,
                    source=model.source,
                    source_revision=model.source_revision,
                    artifact_filename=model.artifact_filename,
                    artifact_format=model.artifact_format,
                    artifact_byte_size=model.artifact_byte_size,
                    artifact_manifest=artifact_manifest,
                    manifest_sha256=model.manifest_sha256,
                    weights_sha256=model.weights_sha256,
                    license_id=model.license_id,
                    runtime=model.runtime,
                    runtime_revision=model.runtime_revision,
                    inference_precision=model.inference_precision,
                    input_sample_rate_hz=model.input_sample_rate_hz,
                    segment_duration_ms=model.segment_duration_ms,
                    preprocessing_version=model.preprocessing_version,
                    preprocessing_manifest=preprocessing_manifest,
                    preprocessing_sha256=model.preprocessing_sha256,
                    license_review_reference=license_review_reference,
                    pooling_strategy=model.pooling_strategy,
                    dimension=model.dimension,
                    status=model.status,
                )
            )
        return True

    def get_target(self, enrichment_job_id: UUID) -> EmbeddingJobTarget | None:
        """Resolve the typed target; generic payloads never carry paths or URLs."""

        with self._sessions() as session:
            row = session.get(EnrichmentJobRow, enrichment_job_id)
            return None if row is None else _job_target(row)

    def put(
        self,
        fence: LeaseFence,
        model: ApprovedEmbeddingModel,
        result: EmbeddingResult,
    ) -> bool:
        """Publish one result under an unexpired lease fence; return whether inserted."""

        with self._sessions() as session, session.begin():
            job = session.scalar(
                select(JobRow)
                .where(
                    JobRow.job_id == fence.job_id,
                    JobRow.state == "RUNNING",
                    JobRow.lease_owner == fence.worker_id,
                    JobRow.attempt_count == fence.attempt_no,
                    JobRow.lease_deadline.is_not(None),
                    JobRow.lease_deadline > func.now(),
                )
                .with_for_update()
            )
            if job is None:
                raise EnrichmentPersistenceError("ml.stale_job_lease")
            target_row = session.scalar(
                select(EnrichmentJobRow)
                .where(
                    EnrichmentJobRow.enrichment_job_id == result.target.enrichment_job_id,
                    EnrichmentJobRow.job_id == fence.job_id,
                )
                .with_for_update()
            )
            registry = session.scalar(
                select(EmbeddingModelRow)
                .where(EmbeddingModelRow.embedding_model_id == model.embedding_model_id)
                .with_for_update()
            )
            if target_row is None or registry is None:
                raise EnrichmentPersistenceError("ml.enrichment_target_missing")
            _validate_publication(target_row, registry, model, result)
            inserted = _put_embedding(session, fence, result)
            if result.tags:
                _put_tags(session, fence, result)
            return inserted

    def save_benchmark(self, model_id: UUID, report: GpuBenchmarkReport, *, decision: str) -> bytes:
        """Persist immutable shadow evidence with the P11 dataset identity."""

        if decision not in {"EXPERIMENTAL", "APPROVED", "REJECTED", "UNAVAILABLE"}:
            raise ValueError("benchmark decision is invalid")
        report_hash = report.sha256
        document = cast(JsonValue, json.loads(report.canonical_bytes()))
        with self._sessions() as session, session.begin():
            registry = session.get(EmbeddingModelRow, model_id)
            if (
                registry is None
                or registry.manifest_sha256.hex() != report.model_manifest_sha256
                or registry.preprocessing_sha256.hex() != report.preprocessing_sha256
            ):
                raise EnrichmentPersistenceError("ml.benchmark_provenance_mismatch")
            existing = session.get(EmbeddingBenchmarkReportRow, report_hash)
            if existing is not None:
                if existing.embedding_model_id != model_id or existing.decision != decision:
                    raise EnrichmentPersistenceError("ml.benchmark_hash_conflict")
                return report_hash
            session.add(
                EmbeddingBenchmarkReportRow(
                    report_sha256=report_hash,
                    embedding_model_id=model_id,
                    dataset_id=report.dataset_id,
                    dataset_version=report.dataset_version,
                    dataset_snapshot_sha256=bytes.fromhex(report.dataset_snapshot_sha256),
                    interaction_schema_version=report.interaction_schema_version,
                    interaction_watermark=report.interaction_watermark,
                    decision=decision,
                    report_document=document,
                    created_at=report.created_at,
                )
            )
        return report_hash

    def activate(
        self,
        *,
        task: str,
        target_model_id: UUID,
        benchmark_report_sha256: bytes,
        rollback_until: datetime,
        actor_user_id: UUID | None = None,
        action: str = "ACTIVATE",
    ) -> int:
        """Atomically switch one active task projection and append rollback evidence."""

        if action not in {"ACTIVATE", "ROLLBACK"}:
            raise ValueError("activation action is invalid")
        if rollback_until.tzinfo is None or rollback_until.utcoffset() is None:
            raise ValueError("rollback_until must be timezone-aware")
        with self._sessions() as session, session.begin():
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:task, 0))"), {"task": task}
            )
            target = session.scalar(
                select(EmbeddingModelRow)
                .where(EmbeddingModelRow.embedding_model_id == target_model_id)
                .with_for_update()
            )
            report = session.get(EmbeddingBenchmarkReportRow, benchmark_report_sha256)
            if (
                target is None
                or target.task != task
                or target.status == "BLOCKED"
                or report is None
                or report.embedding_model_id != target_model_id
                or report.decision != "APPROVED"
            ):
                raise EnrichmentPersistenceError("ml.activation_gate_failed")
            previous = session.scalar(
                select(EmbeddingModelRow)
                .where(EmbeddingModelRow.task == task, EmbeddingModelRow.status == "ACTIVE")
                .with_for_update()
            )
            database_now = session.scalar(select(func.now()))
            if database_now is None:
                raise EnrichmentPersistenceError("ml.activation_clock_unavailable")
            effective_rollback_until = rollback_until
            if action == "ROLLBACK":
                latest = session.scalar(
                    select(EmbeddingModelActivationRow)
                    .where(EmbeddingModelActivationRow.task == task)
                    .order_by(EmbeddingModelActivationRow.activation_sequence.desc())
                    .limit(1)
                    .with_for_update()
                )
                if (
                    latest is None
                    or previous is None
                    or latest.target_embedding_model_id != previous.embedding_model_id
                    or latest.previous_embedding_model_id != target_model_id
                    or latest.rollback_until is None
                    or latest.rollback_until <= database_now
                ):
                    raise EnrichmentPersistenceError("ml.rollback_gate_failed")
                effective_rollback_until = latest.rollback_until
            elif rollback_until <= database_now or (
                previous is not None and previous.embedding_model_id == target_model_id
            ):
                raise EnrichmentPersistenceError("ml.activation_gate_failed")
            sequence = (
                session.scalar(
                    select(
                        func.coalesce(func.max(EmbeddingModelActivationRow.activation_sequence), 0)
                    ).where(EmbeddingModelActivationRow.task == task)
                )
                or 0
            ) + 1
            activation = EmbeddingModelActivationRow(
                task=task,
                activation_sequence=sequence,
                target_embedding_model_id=target_model_id,
                previous_embedding_model_id=(
                    None if previous is None else previous.embedding_model_id
                ),
                action=action,
                benchmark_report_sha256=benchmark_report_sha256,
                rollback_until=effective_rollback_until,
                actor_user_id=actor_user_id,
            )
            session.add(activation)
            # The database trigger requires append-only audit evidence in the same
            # transaction before either side of the lifecycle switch may change.
            session.flush((activation,))
            if previous is not None and previous.embedding_model_id != target_model_id:
                previous.status = "BENCHMARK"
                # Flush the deactivation before activating the replacement so the
                # partial unique index is never violated inside an executemany batch.
                session.flush((previous,))
            target.status = "ACTIVE"
            return sequence

    def retire_derived(
        self, *, model_id: UUID, now: datetime, limit: int = 1000
    ) -> tuple[int, int]:
        """Delete only inactive, rollback-expired derived rows in one bounded batch."""

        if not 1 <= limit <= 10_000:
            raise ValueError("retirement limit is invalid")
        with self._sessions() as session, session.begin():
            model = session.scalar(
                select(EmbeddingModelRow)
                .where(EmbeddingModelRow.embedding_model_id == model_id)
                .with_for_update()
            )
            protected = session.scalar(
                select(func.count())
                .select_from(EmbeddingModelActivationRow)
                .where(
                    (EmbeddingModelActivationRow.target_embedding_model_id == model_id)
                    | (EmbeddingModelActivationRow.previous_embedding_model_id == model_id),
                    EmbeddingModelActivationRow.rollback_until.is_not(None),
                    EmbeddingModelActivationRow.rollback_until > now,
                )
            )
            if model is None or model.status != "RETIRED" or protected:
                raise EnrichmentPersistenceError("ml.model_retirement_protected")
            embedding_ids = session.scalars(
                select(RecordingEmbeddingRow.recording_embedding_id)
                .where(RecordingEmbeddingRow.embedding_model_id == model_id)
                .order_by(RecordingEmbeddingRow.recording_embedding_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            tag_ids = session.scalars(
                select(RecordingTagSetRow.recording_tag_set_id)
                .where(RecordingTagSetRow.embedding_model_id == model_id)
                .order_by(RecordingTagSetRow.recording_tag_set_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            if embedding_ids:
                session.execute(
                    delete(RecordingEmbeddingRow).where(
                        RecordingEmbeddingRow.recording_embedding_id.in_(embedding_ids)
                    )
                )
            if tag_ids:
                # The append-only tag trigger deliberately prevents ad-hoc deletes;
                # retirement uses a transaction-local administrative flag checked by policy.
                session.execute(text("SET LOCAL autplay.allow_derived_retirement = 'on'"))
                session.execute(
                    delete(RecordingTagSetRow).where(
                        RecordingTagSetRow.recording_tag_set_id.in_(tag_ids)
                    )
                )
            return len(embedding_ids), len(tag_ids)


class SqlAlchemyTrackEmbeddingReader:
    """Owner-filtered exact pgvector baseline; HNSW is intentionally absent."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def exact_neighbors(
        self,
        user_id: UUID,
        recording_ids: Sequence[UUID],
        *,
        embedding_model_id: UUID,
        limit: int,
    ) -> tuple[UUID, ...]:
        """Return exact cosine neighbors only inside the owner's visible/available set."""

        seeds = tuple(dict.fromkeys(recording_ids))
        if not seeds or len(seeds) > 100 or not 1 <= limit <= 1000:
            raise ValueError("exact neighbor request is not bounded")
        with self._sessions() as session:
            rows = session.execute(
                text(_EXACT_NEIGHBORS_SQL),
                {
                    "user_id": user_id,
                    "seed_ids": list(seeds),
                    "model_id": embedding_model_id,
                    "limit": limit,
                },
            ).scalars()
            return tuple(cast(UUID, value) for value in rows)


def _approved_model(row: EmbeddingModelRow) -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        embedding_model_id=row.embedding_model_id,
        model_key=row.model_key,
        version=row.version,
        task=row.task,
        source=row.source,
        source_revision=row.source_revision,
        artifact_filename=row.artifact_filename,
        artifact_format=row.artifact_format,
        artifact_byte_size=row.artifact_byte_size,
        weights_sha256=row.weights_sha256,
        manifest_sha256=row.manifest_sha256,
        preprocessing_sha256=row.preprocessing_sha256,
        license_id=row.license_id,
        runtime=row.runtime,
        runtime_revision=row.runtime_revision,
        inference_precision=row.inference_precision,
        input_sample_rate_hz=row.input_sample_rate_hz,
        segment_duration_ms=row.segment_duration_ms,
        preprocessing_version=row.preprocessing_version,
        pooling_strategy=row.pooling_strategy,
        dimension=row.dimension,
        status=row.status,
    )


def _job_target(row: EnrichmentJobRow) -> EmbeddingJobTarget:
    return EmbeddingJobTarget(
        enrichment_job_id=row.enrichment_job_id,
        job_kind=row.job_kind,
        recording_id=row.recording_id,
        audio_variant_id=row.audio_variant_id,
        embedding_model_id=row.embedding_model_id,
        expected_weights_sha256=row.expected_weights_sha256,
        expected_preprocessing_sha256=row.expected_preprocessing_sha256,
    )


def _validate_publication(
    target: EnrichmentJobRow,
    registry: EmbeddingModelRow,
    model: ApprovedEmbeddingModel,
    result: EmbeddingResult,
) -> None:
    if (
        target.enrichment_job_id != result.target.enrichment_job_id
        or target.job_kind != "AUDIO_EMBEDDING"
        or target.job_kind != result.target.job_kind
        or target.recording_id != result.target.recording_id
        or target.audio_variant_id != result.target.audio_variant_id
        or target.embedding_model_id != model.embedding_model_id
        or target.embedding_model_id != result.target.embedding_model_id
        or registry.task != target.job_kind
        or model.task != target.job_kind
        or registry.weights_sha256 != model.weights_sha256
        or registry.preprocessing_sha256 != model.preprocessing_sha256
        or target.expected_weights_sha256 != model.weights_sha256
        or target.expected_preprocessing_sha256 != model.preprocessing_sha256
        or registry.dimension != len(result.vector)
        or registry.status not in {"BENCHMARK", "ACTIVE"}
    ):
        raise EnrichmentPersistenceError("ml.publication_provenance_mismatch")


def _put_embedding(session: Session, fence: LeaseFence, result: EmbeddingResult) -> bool:
    existing = session.scalar(
        select(RecordingEmbeddingRow)
        .where(
            RecordingEmbeddingRow.recording_id == result.target.recording_id,
            RecordingEmbeddingRow.embedding_model_id == result.target.embedding_model_id,
            RecordingEmbeddingRow.audio_variant_id == result.target.audio_variant_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.preprocessing_input_sha256 == result.preprocessing_input_sha256
            and existing.vector_sha256 == result.vector_sha256
        ):
            return False
        raise EnrichmentPersistenceError("ml.embedding_result_conflict")
    session.add(
        RecordingEmbeddingRow(
            recording_id=result.target.recording_id,
            embedding_model_id=result.target.embedding_model_id,
            audio_variant_id=result.target.audio_variant_id,
            embedding=list(result.vector),
            normalized=result.normalized,
            quality_flags={},
            preprocessing_input_sha256=result.preprocessing_input_sha256,
            vector_sha256=result.vector_sha256,
            producing_job_id=fence.job_id,
            producing_attempt_no=fence.attempt_no,
        )
    )
    return True


def _put_tags(session: Session, fence: LeaseFence, result: EmbeddingResult) -> None:
    document: list[JsonValue] = [{"key": key, "score": score} for key, score in result.tags]
    payload = json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    result_hash = hashlib.sha256(b"autplay.recording-tags.v1\0" + payload).digest()
    base_statement = insert(RecordingTagSetRow).values(
        recording_id=result.target.recording_id,
        embedding_model_id=result.target.embedding_model_id,
        audio_variant_id=result.target.audio_variant_id,
        output_schema_version=1,
        tag_document=document,
        result_sha256=result_hash,
        preprocessing_input_sha256=result.preprocessing_input_sha256,
        producing_job_id=fence.job_id,
        producing_attempt_no=fence.attempt_no,
    )
    statement = base_statement.on_conflict_do_nothing(
        constraint="uq_recording_tag_set_source"
    ).returning(RecordingTagSetRow.recording_tag_set_id)
    if session.scalar(statement) is not None:
        return
    existing = session.scalar(
        select(RecordingTagSetRow)
        .where(
            RecordingTagSetRow.recording_id == result.target.recording_id,
            RecordingTagSetRow.embedding_model_id == result.target.embedding_model_id,
            RecordingTagSetRow.audio_variant_id == result.target.audio_variant_id,
            RecordingTagSetRow.output_schema_version == 1,
        )
        .with_for_update()
    )
    if (
        existing is None
        or existing.result_sha256 != result_hash
        or existing.preprocessing_input_sha256 != result.preprocessing_input_sha256
        or existing.tag_document != document
    ):
        raise EnrichmentPersistenceError("ml.tag_result_conflict")


_EXACT_NEIGHBORS_SQL = """
WITH visible AS (
    SELECT DISTINCT variant.recording_id
    FROM vault.acquisition_record acquisition
    JOIN vault.audio_variant variant
      ON variant.audio_variant_id = acquisition.audio_variant_id
     AND variant.validation_status = 'VALID'
     AND variant.deleted_at IS NULL
    JOIN vault.vault_object object
      ON object.vault_object_id = variant.vault_object_id
     AND object.commit_status = 'COMMITTED'
    JOIN vault.vault_replica replica
      ON replica.vault_object_id = object.vault_object_id
     AND replica.replica_status = 'AVAILABLE'
    WHERE acquisition.authorized_by_user_id = :user_id
      AND acquisition.rights_capability IN (
          'AUTHORIZED_DOWNLOAD', 'USER_UPLOAD', 'LOCAL_IMPORT', 'RESTORE'
      )
      AND NOT EXISTS (
          SELECT 1
          FROM library.user_track_ref ref
          JOIN library.user_track_preference preference
            ON preference.user_track_ref_id = ref.user_track_ref_id
          WHERE ref.user_id = :user_id
            AND ref.recording_id = variant.recording_id
            AND ref.deleted_at IS NULL
            AND (
                preference.preference = 'DISLIKED'
                OR preference.excluded_from_taste = true
            )
      )
), seed AS (
    SELECT avg(embedding.embedding) AS vector
    FROM ml.recording_embedding embedding
    JOIN visible ON visible.recording_id = embedding.recording_id
    WHERE embedding.embedding_model_id = :model_id
      AND embedding.recording_id = ANY(:seed_ids)
      AND embedding.retired_at IS NULL
), candidates AS (
    SELECT embedding.recording_id,
           min(embedding.embedding <=> seed.vector) AS exact_distance
    FROM ml.recording_embedding embedding
    JOIN visible ON visible.recording_id = embedding.recording_id
    JOIN vault.audio_variant variant
      ON variant.audio_variant_id = embedding.audio_variant_id
     AND variant.validation_status = 'VALID' AND variant.deleted_at IS NULL
    JOIN vault.vault_object object
      ON object.vault_object_id = variant.vault_object_id
     AND object.commit_status = 'COMMITTED'
    JOIN vault.vault_replica replica
      ON replica.vault_object_id = object.vault_object_id
     AND replica.replica_status = 'AVAILABLE'
    CROSS JOIN seed
    WHERE embedding.embedding_model_id = :model_id
      AND embedding.retired_at IS NULL
      AND NOT (embedding.recording_id = ANY(:seed_ids))
      AND seed.vector IS NOT NULL
    GROUP BY embedding.recording_id
)
SELECT recording_id
FROM candidates
ORDER BY exact_distance ASC, recording_id ASC
LIMIT :limit
"""


__all__ = (
    "EnrichmentPersistenceError",
    "SqlAlchemyEnrichmentRuntime",
    "SqlAlchemyTrackEmbeddingReader",
)
