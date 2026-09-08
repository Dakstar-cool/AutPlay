"""Autograd implementation of the frozen Sona-Lite training objective."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from autplay.domain.sona import SONA_SID_DEPTH
from autplay.domain.sona_training import SonaTrainingLossWeights
from torch import Tensor
from torch.nn import functional as functional


@dataclass(frozen=True, slots=True)
class TorchSonaLossBreakdown:
    ntp: Tensor
    ranking: Tensor
    distillation: Tensor
    total: Tensor


def compute_torch_sona_losses(
    decoder_logits: Tensor,
    ranking_logits: Tensor,
    target_sids: Tensor,
    ranking_labels: Tensor,
    ranking_label_mask: Tensor,
    teacher_probabilities: Tensor,
    teacher_mask: Tensor,
    *,
    weights: SonaTrainingLossWeights | None = None,
    distillation_temperature: float = 1.0,
) -> TorchSonaLossBreakdown:
    """Match the framework-neutral reference objective while retaining gradients."""

    if distillation_temperature <= 0.0:
        raise ValueError("Sona distillation temperature must be positive")
    effective_weights = weights or SonaTrainingLossWeights()
    ntp = functional.cross_entropy(
        decoder_logits.reshape(-1, decoder_logits.shape[-1]),
        target_sids.reshape(-1),
        reduction="mean",
    )
    if decoder_logits.shape[1] != SONA_SID_DEPTH:
        raise ValueError("Sona decoder logits have an invalid depth")
    ranking = _masked_binary_cross_entropy(
        ranking_logits,
        ranking_labels,
        ranking_label_mask,
    )
    distillation = _masked_binary_cross_entropy(
        ranking_logits / distillation_temperature,
        teacher_probabilities,
        teacher_mask,
    ) * (distillation_temperature**2)
    total = (
        effective_weights.ntp * ntp
        + effective_weights.ranking * ranking
        + effective_weights.distillation * distillation
    )
    return TorchSonaLossBreakdown(ntp, ranking, distillation, total)


def _masked_binary_cross_entropy(logits: Tensor, targets: Tensor, mask: Tensor) -> Tensor:
    losses = functional.binary_cross_entropy_with_logits(logits, targets, reduction="none")
    return torch.sum(losses * mask) / torch.clamp_min(torch.sum(mask), 1.0)


__all__ = ("TorchSonaLossBreakdown", "compute_torch_sona_losses")
