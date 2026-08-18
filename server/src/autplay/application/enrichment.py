"""P12 fenced embedding job orchestration with bounded OOM degradation."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from autplay.application.job_worker import JobExecutionContext
from autplay.domain.enrichment import (
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
    EmbeddingResult,
    preprocessing_input_sha256,
    vector_sha256,
)
from autplay.domain.jobs import JobKey, JobLease, RetryableJobError, TerminalJobError
from autplay.ports.enrichment import (
    ApprovedModelRegistryReader,
    BoundedAudioPreprocessor,
    EnrichmentJobReader,
    TrackEmbedder,
    TrackEmbeddingWriter,
)


class AcceleratorOutOfMemory(RuntimeError):
    """Concrete GPU adapters raise this only for classified accelerator OOM."""


EMBEDDING_JOB_KEY = JobKey("ml.audio-embedding", 1)


class EmbeddingJobHandler:
    """Execute one approved model/Recording/source job without accepting URLs or paths."""

    def __init__(
        self,
        *,
        jobs: EnrichmentJobReader,
        models: ApprovedModelRegistryReader,
        preprocessor: BoundedAudioPreprocessor,
        embedder: TrackEmbedder,
        writer: TrackEmbeddingWriter,
        initial_batch_size: int = 8,
        maximum_oom_reductions: int = 3,
    ) -> None:
        if not 1 <= initial_batch_size <= 256:
            raise ValueError("initial_batch_size must be between one and 256")
        if not 0 <= maximum_oom_reductions <= 8:
            raise ValueError("maximum_oom_reductions must be between zero and eight")
        self._jobs = jobs
        self._models = models
        self._preprocessor = preprocessor
        self._embedder = embedder
        self._writer = writer
        self._initial_batch_size = initial_batch_size
        self._maximum_oom_reductions = maximum_oom_reductions

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        if lease.key != EMBEDDING_JOB_KEY:
            raise TerminalJobError("ml.enrichment_job_kind_mismatch")
        enrichment_job_id = _enrichment_job_id(lease.payload)
        target = self._jobs.get_target(enrichment_job_id)
        if target is None:
            raise TerminalJobError("ml.enrichment_job_not_found")
        if target.job_kind != "AUDIO_EMBEDDING":
            raise TerminalJobError("ml.enrichment_job_kind_mismatch")
        model = self._models.get(target.embedding_model_id)
        if model is None or model.status in {"BLOCKED", "RETIRED"}:
            raise TerminalJobError("ml.model_not_eligible")
        if model.task != target.job_kind:
            raise TerminalJobError("ml.enrichment_model_task_mismatch")
        _validate_model_identity(
            model,
            target.expected_weights_sha256,
            target.expected_preprocessing_sha256,
        )
        if self._embedder.model_id != model.embedding_model_id:
            raise TerminalJobError("ml.loaded_model_mismatch")
        if self._embedder.weights_sha256 != model.weights_sha256:
            raise TerminalJobError("ml.loaded_model_hash_mismatch")

        segments = tuple(self._preprocessor.decode(target, model))
        input_hash = preprocessing_input_sha256(model.preprocessing_sha256, segments)
        batch_size, prior_reductions = _resume_oom_state(lease.checkpoint, self._initial_batch_size)
        vector, tags, batch_size, reductions = self._embed_with_bounded_oom(
            context,
            segments,
            input_hash=input_hash,
            batch_size=batch_size,
            prior_reductions=prior_reductions,
        )
        if len(vector) != model.dimension:
            raise TerminalJobError(
                "ml.embedding_dimension_mismatch",
                {"actual": len(vector), "expected": model.dimension},
            )
        result = EmbeddingResult(
            target=target,
            preprocessing_input_sha256=input_hash,
            vector_sha256=vector_sha256(vector),
            vector=vector,
            normalized=True,
            tags=tags,
        )
        context.checkpoint(
            {
                "stage": "PREPARED",
                "preprocessing_input_sha256": input_hash.hex(),
                "segment_count": len(segments),
                "batch_size": batch_size,
                "oom_reductions": reductions,
            },
            progress_current=1,
            progress_total=2,
        )
        self._writer.put(context.fence, model, result)
        context.checkpoint(
            {
                "stage": "PUBLISHED",
                "preprocessing_input_sha256": input_hash.hex(),
                "vector_sha256": result.vector_sha256.hex(),
                "segment_count": len(segments),
                "batch_size": batch_size,
                "oom_reductions": reductions,
            },
            progress_current=2,
            progress_total=2,
        )

    def _embed_with_bounded_oom(
        self,
        context: JobExecutionContext,
        segments: tuple[DecodedAudioSegment, ...],
        *,
        input_hash: bytes,
        batch_size: int,
        prior_reductions: int,
    ) -> tuple[tuple[float, ...], tuple[tuple[str, float], ...], int, int]:
        reductions = prior_reductions
        while True:
            try:
                vector, tags = self._embedder.embed(segments, batch_size=batch_size)
                return vector, tags, batch_size, reductions
            except AcceleratorOutOfMemory as error:
                del error
                if batch_size == 1 or reductions >= self._maximum_oom_reductions:
                    raise RetryableJobError(
                        "ml.gpu_oom",
                        {"batch_size": batch_size, "oom_reductions": reductions},
                    ) from None
                batch_size = max(1, batch_size // 2)
                reductions += 1
                context.checkpoint(
                    {
                        "stage": "OOM_REDUCED",
                        "preprocessing_input_sha256": input_hash.hex(),
                        "segment_count": len(segments),
                        "batch_size": batch_size,
                        "oom_reductions": reductions,
                    }
                )


def _enrichment_job_id(payload: Mapping[str, object]) -> UUID:
    if set(payload) != {"enrichment_job_id"}:
        raise TerminalJobError("ml.invalid_job_payload")
    value = payload.get("enrichment_job_id")
    if not isinstance(value, str):
        raise TerminalJobError("ml.invalid_job_payload")
    try:
        return UUID(value)
    except ValueError as error:
        raise TerminalJobError("ml.invalid_job_payload") from error


def _validate_model_identity(
    model: ApprovedEmbeddingModel, expected_weights: bytes, expected_preprocessing: bytes
) -> None:
    if model.weights_sha256 != expected_weights:
        raise TerminalJobError("ml.model_hash_mismatch")
    if model.preprocessing_sha256 != expected_preprocessing:
        raise TerminalJobError("ml.preprocessing_hash_mismatch")


def _resume_oom_state(
    checkpoint: Mapping[str, object] | None, default_batch: int
) -> tuple[int, int]:
    if checkpoint is None:
        return default_batch, 0
    batch_size = checkpoint.get("batch_size")
    reductions = checkpoint.get("oom_reductions")
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 256:
        return default_batch, 0
    if not isinstance(reductions, int) or not 0 <= reductions <= 8:
        return default_batch, 0
    return batch_size, reductions


__all__ = ("EMBEDDING_JOB_KEY", "AcceleratorOutOfMemory", "EmbeddingJobHandler")
