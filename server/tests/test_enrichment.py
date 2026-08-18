"""Pure P12 preprocessing, hash, registry and bounded OOM evidence."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.application.enrichment import (
    EMBEDDING_JOB_KEY,
    AcceleratorOutOfMemory,
    EmbeddingJobHandler,
)
from autplay.application.job_worker import JobExecutionContext
from autplay.domain.enrichment import (
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
    EmbeddingJobTarget,
    EmbeddingResult,
    preprocessing_input_sha256,
)
from autplay.domain.jobs import JobKey, JobLease, LeaseFence, RetryableJobError, TerminalJobError

HASH_A = b"a" * 32
HASH_B = b"b" * 32


def _model() -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        embedding_model_id=UUID("00000000-0000-0000-0000-000000000101"),
        model_key="autplay.reference-audio",
        version="1",
        task="AUDIO_EMBEDDING",
        source="builtin://autplay/reference",
        source_revision="test-fixture-v1",
        artifact_filename="reference.bin",
        artifact_format="TEST_ONLY",
        artifact_byte_size=32,
        weights_sha256=HASH_A,
        manifest_sha256=b"m" * 32,
        preprocessing_sha256=HASH_B,
        license_id="AutPlay-Test-Only",
        runtime="reference",
        runtime_revision="1",
        inference_precision="float32",
        input_sample_rate_hz=48_000,
        segment_duration_ms=1_000,
        preprocessing_version="pcm-f32le-v1",
        pooling_strategy="mean-l2-v1",
        dimension=2,
        status="BENCHMARK",
    )


def _target(model: ApprovedEmbeddingModel) -> EmbeddingJobTarget:
    return EmbeddingJobTarget(
        enrichment_job_id=UUID("00000000-0000-0000-0000-000000000201"),
        job_kind="AUDIO_EMBEDDING",
        recording_id=UUID("00000000-0000-0000-0000-000000000202"),
        audio_variant_id=UUID("00000000-0000-0000-0000-000000000203"),
        embedding_model_id=model.embedding_model_id,
        expected_weights_sha256=model.weights_sha256,
        expected_preprocessing_sha256=model.preprocessing_sha256,
    )


def _segments() -> tuple[DecodedAudioSegment, ...]:
    return (
        DecodedAudioSegment(0, 0, struct.pack("<ff", 0.25, -0.5)),
        DecodedAudioSegment(1, 1_000, struct.pack("<ff", 0.5, -0.25)),
    )


def _lease(target: EmbeddingJobTarget) -> JobLease:
    return JobLease(
        LeaseFence(uuid4(), "gpu-test", 1),
        EMBEDDING_JOB_KEY,
        None,
        3,
        {"enrichment_job_id": str(target.enrichment_job_id)},
        None,
        datetime.now(UTC) + timedelta(minutes=1),
        None,
    )


@dataclass
class _Context:
    fence: LeaseFence
    checkpoints: list[dict[str, object]] = field(default_factory=list)

    def checkpoint(self, value: dict[str, object], **kwargs: object) -> None:
        del kwargs
        self.checkpoints.append(dict(value))


@dataclass
class _Jobs:
    target: EmbeddingJobTarget

    def get_target(self, enrichment_job_id: UUID) -> EmbeddingJobTarget | None:
        return self.target if enrichment_job_id == self.target.enrichment_job_id else None


@dataclass
class _Models:
    model: ApprovedEmbeddingModel

    def get(self, embedding_model_id: UUID) -> ApprovedEmbeddingModel | None:
        return self.model if embedding_model_id == self.model.embedding_model_id else None


class _Preprocessor:
    def decode(
        self, target: EmbeddingJobTarget, model: ApprovedEmbeddingModel
    ) -> tuple[DecodedAudioSegment, ...]:
        del target, model
        return _segments()


@dataclass
class _Embedder:
    model_id: UUID
    weights_sha256: bytes
    oom_until_batch_below: int = 1
    calls: list[int] = field(default_factory=list)

    def embed(
        self, segments: object, *, batch_size: int
    ) -> tuple[tuple[float, ...], tuple[tuple[str, float], ...]]:
        del segments
        self.calls.append(batch_size)
        if batch_size >= self.oom_until_batch_below:
            raise AcceleratorOutOfMemory
        return (0.6, 0.8), (("mood:focused", 0.75),)


@dataclass
class _Writer:
    results: list[EmbeddingResult] = field(default_factory=list)

    def put(
        self,
        fence: LeaseFence,
        model: ApprovedEmbeddingModel,
        result: EmbeddingResult,
    ) -> bool:
        del fence, model
        self.results.append(result)
        return True


def test_preprocessing_hash_is_deterministic_and_layout_bound() -> None:
    first = preprocessing_input_sha256(HASH_B, _segments())
    assert preprocessing_input_sha256(HASH_B, _segments()) == first
    changed = (
        DecodedAudioSegment(0, 0, _segments()[0].pcm_f32le),
        DecodedAudioSegment(1, 2_000, _segments()[1].pcm_f32le),
    )
    assert preprocessing_input_sha256(HASH_B, changed) != first


def test_handler_halves_batch_then_publishes_versioned_result() -> None:
    model = _model()
    target = _target(model)
    lease = _lease(target)
    context = _Context(lease.fence)
    embedder = _Embedder(model.embedding_model_id, model.weights_sha256, 3)
    writer = _Writer()
    handler = EmbeddingJobHandler(
        jobs=_Jobs(target),
        models=_Models(model),
        preprocessor=_Preprocessor(),
        embedder=embedder,
        writer=writer,
        initial_batch_size=8,
    )

    handler(cast(JobExecutionContext, context), lease)

    assert embedder.calls == [8, 4, 2]
    assert [item["batch_size"] for item in context.checkpoints[:2]] == [4, 2]
    assert context.checkpoints[-1]["stage"] == "PUBLISHED"
    assert writer.results[0].vector == (0.6, 0.8)
    assert writer.results[0].target.embedding_model_id == model.embedding_model_id


def test_handler_rejects_job_key_target_kind_and_model_task_mismatches() -> None:
    model = _model()
    target = _target(model)
    embedder = _Embedder(model.embedding_model_id, model.weights_sha256, 3)
    writer = _Writer()

    def handler(
        selected_target: EmbeddingJobTarget, selected_model: ApprovedEmbeddingModel
    ) -> EmbeddingJobHandler:
        return EmbeddingJobHandler(
            jobs=_Jobs(selected_target),
            models=_Models(selected_model),
            preprocessor=_Preprocessor(),
            embedder=embedder,
            writer=writer,
        )

    wrong_key_lease = replace(_lease(target), key=JobKey("ml.audio-tags", 1))
    with pytest.raises(TerminalJobError, match=r"ml\.enrichment_job_kind_mismatch"):
        handler(target, model)(
            cast(JobExecutionContext, _Context(wrong_key_lease.fence)), wrong_key_lease
        )

    with pytest.raises(ValueError, match="enrichment job kind is invalid"):
        replace(target, job_kind="AUDIO_TAGS")

    tag_model = replace(model, task="AUDIO_TAGS")
    with pytest.raises(TerminalJobError, match=r"ml\.enrichment_model_task_mismatch"):
        handler(target, tag_model)(
            cast(JobExecutionContext, _Context(_lease(target).fence)), _lease(target)
        )

    assert embedder.calls == []
    assert writer.results == []


def test_forced_oom_is_bounded_and_becomes_retryable() -> None:
    model = _model()
    target = _target(model)
    lease = _lease(target)
    context = _Context(lease.fence)
    embedder = _Embedder(model.embedding_model_id, model.weights_sha256, 1)
    handler = EmbeddingJobHandler(
        jobs=_Jobs(target),
        models=_Models(model),
        preprocessor=_Preprocessor(),
        embedder=embedder,
        writer=_Writer(),
        initial_batch_size=8,
        maximum_oom_reductions=3,
    )

    with pytest.raises(RetryableJobError, match=r"ml\.gpu_oom"):
        handler(cast(JobExecutionContext, context), lease)

    assert embedder.calls == [8, 4, 2, 1]
    assert [item["batch_size"] for item in context.checkpoints] == [4, 2, 1]


def test_restarted_worker_resumes_bounded_oom_checkpoint() -> None:
    model = _model()
    target = _target(model)
    original = _lease(target)
    lease = JobLease(
        original.fence,
        original.key,
        original.user_id,
        original.priority,
        original.payload,
        {"batch_size": 2, "oom_reductions": 2, "stage": "OOM_REDUCED"},
        original.lease_deadline,
        original.cancel_requested_at,
    )
    context = _Context(lease.fence)
    embedder = _Embedder(model.embedding_model_id, model.weights_sha256, 2)
    writer = _Writer()
    handler = EmbeddingJobHandler(
        jobs=_Jobs(target),
        models=_Models(model),
        preprocessor=_Preprocessor(),
        embedder=embedder,
        writer=writer,
        initial_batch_size=8,
        maximum_oom_reductions=3,
    )

    handler(cast(JobExecutionContext, context), lease)

    assert embedder.calls == [2, 1]
    assert context.checkpoints[0]["oom_reductions"] == 3
    assert context.checkpoints[-1]["stage"] == "PUBLISHED"
    assert len(writer.results) == 1


def test_loaded_model_hash_mismatch_is_terminal() -> None:
    model = _model()
    target = _target(model)
    lease = _lease(target)
    handler = EmbeddingJobHandler(
        jobs=_Jobs(target),
        models=_Models(model),
        preprocessor=_Preprocessor(),
        embedder=_Embedder(model.embedding_model_id, b"x" * 32, 0),
        writer=_Writer(),
    )
    with pytest.raises(TerminalJobError, match=r"ml\.loaded_model_hash_mismatch"):
        handler(cast(JobExecutionContext, _Context(lease.fence)), lease)
