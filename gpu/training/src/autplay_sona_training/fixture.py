"""Explicit synthetic artifacts for non-quality Sona-Lite hardware smoke tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import numpy as np
import rfc8785
from autplay.application.sona import sona_inference_request_document
from autplay.application.sona_training import build_sona_training_example
from autplay.domain.sona import (
    SonaAction,
    SonaCandidate,
    SonaHistoryEvent,
    SonaInferenceRequest,
    SonaOrigin,
    SonaSemanticId,
)
from autplay.domain.sona_training import (
    SonaObservedOutcome,
    SonaTeacherSnapshot,
    SonaTeacherTarget,
    SonaTrainingExample,
)

from .dataset import materialize_sona_dataset
from .tokenizer import (
    compute_source_embeddings_sha256,
    fit_residual_tokenizer,
    materialize_sona_tokenizer,
)


@dataclass(frozen=True, slots=True)
class SonaFixtureBundle:
    tokenizer_fit_manifest_sha256: str
    tokenizer_artifact_manifest_sha256: str
    dataset_manifest_sha256: str
    example_count: int


def materialize_synthetic_fixture_bundle(
    output_directory: Path,
    *,
    recording_count: int = 16,
) -> SonaFixtureBundle:
    """Create deterministic owner-safe fixtures that are never quality eligible."""

    if not 1 <= recording_count <= 16:
        raise ValueError("Sona synthetic recording count is outside the accepted bound")
    if output_directory.exists():
        raise FileExistsError(f"Sona fixture output already exists: {output_directory}")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent)
    )
    try:
        recording_ids = tuple(UUID(int=10_000 + index) for index in range(recording_count))
        embeddings = _synthetic_embeddings(len(recording_ids))
        source_embeddings_sha256 = compute_source_embeddings_sha256(recording_ids, embeddings)
        tokenizer = fit_residual_tokenizer(
            recording_ids,
            embeddings,
            source_embeddings_sha256=source_embeddings_sha256,
            codebook_size=32,
            iterations=10,
            seed=17,
        )
        tokenizer_artifact_manifest_sha256 = materialize_sona_tokenizer(
            tokenizer, temporary / "tokenizer"
        )
        examples = _synthetic_training_examples(tokenizer.mapping(), tokenizer.manifest_sha256)
        dataset_manifest_sha256 = materialize_sona_dataset(
            examples,
            temporary / "dataset",
            split="fixture",
            data_classification="SYNTHETIC_FIXTURE",
            quality_eligible=False,
            owner_lineage_hmac_key=sha256(b"autplay-sona-synthetic-owner-lineage-v1").digest(),
            tokenizer_active_codes_per_level=tokenizer.centroids.shape[1],
        )
        os.replace(temporary, output_directory)
        return SonaFixtureBundle(
            tokenizer_fit_manifest_sha256=tokenizer.manifest_sha256,
            tokenizer_artifact_manifest_sha256=tokenizer_artifact_manifest_sha256,
            dataset_manifest_sha256=dataset_manifest_sha256,
            example_count=len(examples),
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _synthetic_embeddings(count: int) -> np.ndarray:
    rows = []
    for index in range(count):
        angle = (index + 1) * 0.37
        rows.append(
            tuple(
                np.float32(np.sin(angle * (dimension + 1)) + np.cos(angle / (dimension + 1)))
                for dimension in range(8)
            )
        )
    return np.asarray(rows, dtype=np.float32)


def _synthetic_training_examples(
    mapping: dict[UUID, SonaSemanticId], tokenizer_sha256: str
) -> tuple[SonaTrainingExample, ...]:
    recording_ids = tuple(sorted(mapping, key=lambda value: value.hex))
    candidates = tuple(
        SonaCandidate(recording_id, mapping[recording_id]) for recording_id in recording_ids
    )
    cutoff_base = 1_788_400_000_000
    source_model_manifest_sha256 = _digest("synthetic-source-model-v1")
    examples: list[SonaTrainingExample] = []
    for example_index in range(8):
        cutoff_at_ms = cutoff_base + example_index * 60_000
        history: list[SonaHistoryEvent] = []
        for event_index in range(8):
            recording_id = recording_ids[(example_index + event_index) % len(recording_ids)]
            history.append(
                SonaHistoryEvent(
                    evidence_id=UUID(int=100_000 + example_index * 100 + event_index),
                    recording_id=recording_id,
                    semantic_id=mapping[recording_id],
                    action=(
                        SonaAction.COMPLETION if event_index % 3 else SonaAction.ORGANIC_LISTEN
                    ),
                    origin=SonaOrigin.ORGANIC,
                    age_bucket=event_index + 1,
                    effective_at_ms=cutoff_at_ms - (8 - event_index) * 1_000,
                    server_sequence=event_index + 1,
                )
            )
        request_sha256 = sha256(
            rfc8785.dumps(
                sona_inference_request_document(
                    owner_user_id=UUID(int=1),
                    temporal_snapshot_id=UUID(int=200_000 + example_index),
                    baseline_snapshot_id=UUID(int=300_000 + example_index),
                    cutoff_at_ms=cutoff_at_ms,
                    interaction_watermark=len(history),
                    tokenizer_sha256=tokenizer_sha256,
                    model_manifest_sha256=source_model_manifest_sha256,
                    seed=example_index + 1,
                    history=tuple(history),
                    candidates=candidates,
                )
            )
        ).hexdigest()
        request = SonaInferenceRequest(
            owner_user_id=UUID(int=1),
            temporal_snapshot_id=UUID(int=200_000 + example_index),
            baseline_snapshot_id=UUID(int=300_000 + example_index),
            cutoff_at_ms=cutoff_at_ms,
            interaction_watermark=len(history),
            tokenizer_sha256=tokenizer_sha256,
            model_manifest_sha256=source_model_manifest_sha256,
            seed=example_index + 1,
            history=tuple(history),
            candidates=candidates,
            request_sha256=request_sha256,
        )
        target_index = (example_index * 3 + 5) % len(recording_ids)
        target_recording_id = recording_ids[target_index]
        outcome = SonaObservedOutcome(
            owner_user_id=request.owner_user_id,
            source_request_sha256=request_sha256,
            recording_id=target_recording_id,
            observed_at_ms=cutoff_at_ms + 1_000,
            completion=1.0 if example_index % 2 == 0 else 0.25,
            like=1.0 if example_index % 3 == 0 else None,
            skip=0.0 if example_index % 2 == 0 else 1.0,
        )
        teacher_targets = tuple(
            SonaTeacherTarget(
                recording_id,
                (
                    0.85 if index == target_index else 0.15,
                    0.70 if index == target_index else 0.10,
                    0.10 if index == target_index else 0.65,
                    0.90 if index == target_index else 0.05,
                ),
            )
            for index, recording_id in enumerate(recording_ids)
        )
        teacher = SonaTeacherSnapshot(
            source_request_sha256=request_sha256,
            teacher_key="synthetic-p11-teacher",
            teacher_version="1",
            teacher_manifest_sha256=_digest("synthetic-teacher-manifest-v1"),
            targets=teacher_targets,
            snapshot_sha256=_digest(f"synthetic-teacher-snapshot-{example_index}"),
        )
        examples.append(build_sona_training_example(request, outcome, teacher))
    return tuple(examples)


def _digest(value: str) -> str:
    return sha256(value.encode("ascii")).hexdigest()


__all__ = ("SonaFixtureBundle", "materialize_synthetic_fixture_bundle")
