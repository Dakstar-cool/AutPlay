"""Framework-neutral Sona-Lite collation and reference loss contract."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
import numpy.typing as npt
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_RANKING_HEADS, SONA_SID_DEPTH
from autplay.domain.sona_training import SonaTrainingExample, SonaTrainingLossWeights

from .sona_tensors import SonaInputTensorBatch, pack_sona_requests

type Float32Array = npt.NDArray[np.float32]
type Int64Array = npt.NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class SonaTrainingTensorBatch:
    """One bounded batch with observed and offline-teacher supervision masks."""

    inputs: SonaInputTensorBatch
    target_sids: Int64Array
    ranking_labels: Float32Array
    ranking_label_mask: Float32Array
    teacher_probabilities: Float32Array
    teacher_mask: Float32Array
    example_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SonaLossBreakdown:
    ntp: float
    ranking: float
    distillation: float
    total: float


def pack_sona_training_examples(
    examples: tuple[SonaTrainingExample, ...],
) -> SonaTrainingTensorBatch:
    """Collate examples without consulting mutable catalog or profile state."""

    inputs = pack_sona_requests(tuple(value.request for value in examples))
    batch_size = len(examples)
    target_sids = np.zeros((batch_size, SONA_SID_DEPTH), dtype=np.int64)
    ranking_labels = np.zeros(
        (batch_size, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)), dtype=np.float32
    )
    ranking_label_mask = np.zeros_like(ranking_labels)
    teacher_probabilities = np.zeros_like(ranking_labels)
    teacher_mask = np.zeros_like(ranking_labels)
    for batch_index, example in enumerate(examples):
        target_sids[batch_index] = example.target_semantic_id.values
        for candidate_index, target in enumerate(example.ranking_targets):
            ranking_labels[batch_index, candidate_index] = target.labels
            ranking_label_mask[batch_index, candidate_index] = target.label_mask
            teacher_probabilities[batch_index, candidate_index] = target.teacher_probabilities
            teacher_mask[batch_index, candidate_index] = 1.0
    return SonaTrainingTensorBatch(
        inputs=inputs,
        target_sids=target_sids,
        ranking_labels=ranking_labels,
        ranking_label_mask=ranking_label_mask,
        teacher_probabilities=teacher_probabilities,
        teacher_mask=teacher_mask,
        example_sha256=tuple(value.example_sha256 for value in examples),
    )


def compute_sona_losses(
    decoder_logits: npt.NDArray[np.floating],
    ranking_logits: npt.NDArray[np.floating],
    batch: SonaTrainingTensorBatch,
    *,
    weights: SonaTrainingLossWeights | None = None,
    distillation_temperature: float = 1.0,
) -> SonaLossBreakdown:
    """Compute the reference NTP, masked ranking, and teacher-distillation objective."""

    effective_weights = weights or SonaTrainingLossWeights()
    if not isfinite(distillation_temperature) or distillation_temperature <= 0.0:
        raise ValueError("Sona distillation temperature must be finite and positive")
    decoder = np.asarray(decoder_logits, dtype=np.float64)
    ranking = np.asarray(ranking_logits, dtype=np.float64)
    batch_size = batch.target_sids.shape[0]
    if (
        decoder.ndim != 3
        or decoder.shape[:2] != (batch_size, SONA_SID_DEPTH)
        or decoder.shape[2] <= int(batch.target_sids.max(initial=0))
    ):
        raise ValueError("Sona decoder logits have an invalid shape or vocabulary")
    expected_ranking_shape = (
        batch_size,
        SONA_MAX_CANDIDATES,
        len(SONA_RANKING_HEADS),
    )
    if ranking.shape != expected_ranking_shape:
        raise ValueError("Sona ranking logits have an invalid shape")
    if not np.isfinite(decoder).all() or not np.isfinite(ranking).all():
        raise ValueError("Sona logits must be finite")

    shifted = decoder - decoder.max(axis=2, keepdims=True)
    log_probabilities = shifted - np.log(np.exp(shifted).sum(axis=2, keepdims=True))
    target_log_probabilities = np.take_along_axis(
        log_probabilities,
        batch.target_sids.astype(np.int64, copy=False)[..., np.newaxis],
        axis=2,
    ).squeeze(axis=2)
    ntp_loss = float(-target_log_probabilities.mean())

    ranking_loss = _masked_binary_cross_entropy(
        ranking,
        batch.ranking_labels.astype(np.float64, copy=False),
        batch.ranking_label_mask.astype(np.float64, copy=False),
    )
    softened_logits = ranking / distillation_temperature
    distillation_loss = _masked_binary_cross_entropy(
        softened_logits,
        batch.teacher_probabilities.astype(np.float64, copy=False),
        batch.teacher_mask.astype(np.float64, copy=False),
    ) * (distillation_temperature**2)
    total = (
        effective_weights.ntp * ntp_loss
        + effective_weights.ranking * ranking_loss
        + effective_weights.distillation * distillation_loss
    )
    return SonaLossBreakdown(ntp_loss, ranking_loss, distillation_loss, total)


def _masked_binary_cross_entropy(
    logits: npt.NDArray[np.float64],
    targets: npt.NDArray[np.float64],
    mask: npt.NDArray[np.float64],
) -> float:
    active = float(mask.sum())
    if active <= 0.0:
        return 0.0
    losses = np.maximum(logits, 0.0) - logits * targets + np.log1p(np.exp(-np.abs(logits)))
    return float((losses * mask).sum() / active)


__all__ = (
    "SonaLossBreakdown",
    "SonaTrainingTensorBatch",
    "compute_sona_losses",
    "pack_sona_training_examples",
)
