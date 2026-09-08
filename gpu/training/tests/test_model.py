"""Shared-encoder Sona-Lite model and autograd objective evidence."""

from __future__ import annotations

from math import log
from typing import cast

import numpy as np
import pytest
import torch
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_MAX_HISTORY_EVENTS, SONA_RANKING_HEADS
from autplay_sona_training.model import SonaLiteConfig, SonaLiteModel
from autplay_sona_training.objective import compute_torch_sona_losses


def _inputs() -> tuple[torch.Tensor, ...]:
    history_sids = torch.zeros((1, SONA_MAX_HISTORY_EVENTS, 3), dtype=torch.int64)
    history_actions = torch.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64)
    history_origins = torch.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64)
    history_ages = torch.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64)
    history_mask = torch.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64)
    history_sids[0, -1] = torch.tensor((1, 2, 3))
    history_actions[0, -1] = 1
    history_origins[0, -1] = 2
    history_ages[0, -1] = 4
    history_mask[0, -1] = 1
    candidate_sids = torch.zeros((1, SONA_MAX_CANDIDATES, 3), dtype=torch.int64)
    candidate_mask = torch.zeros((1, SONA_MAX_CANDIDATES), dtype=torch.int64)
    candidate_sids[0, 0] = torch.tensor((1, 2, 3))
    candidate_sids[0, 1] = torch.tensor((4, 5, 6))
    candidate_mask[0, :2] = 1
    seed = torch.tensor((19,), dtype=torch.int64)
    return (
        history_sids,
        history_actions,
        history_origins,
        history_ages,
        history_mask,
        candidate_sids,
        candidate_mask,
        seed,
    )


def test_generation_and_ranking_backpropagate_through_one_shared_encoder() -> None:
    torch.manual_seed(7)
    model = SonaLiteModel(SonaLiteConfig(codebook_size=16, model_dimensions=16, encoder_layers=1))
    target_sids = torch.tensor(((4, 5, 6),), dtype=torch.int64)
    inputs = _inputs()
    decoder_logits, ranking_logits = model.training_forward(
        inputs[0],
        inputs[1],
        inputs[2],
        inputs[3],
        inputs[4],
        inputs[5],
        inputs[6],
        inputs[7],
        target_sids,
    )
    labels = torch.zeros_like(ranking_logits)
    labels[0, 1] = torch.tensor((1.0, 1.0, 0.0, 1.0))
    label_mask = torch.zeros_like(ranking_logits)
    label_mask[0, 0, 3] = 1.0
    label_mask[0, 1] = 1.0
    teacher = torch.full_like(ranking_logits, 0.5)
    teacher_mask = torch.zeros_like(ranking_logits)
    teacher_mask[0, :2] = 1.0

    losses = compute_torch_sona_losses(
        decoder_logits,
        ranking_logits,
        target_sids,
        labels,
        label_mask,
        teacher,
        teacher_mask,
    )
    losses.total.backward()  # type: ignore[no-untyped-call]

    encoder_input_weights = cast(torch.nn.Parameter, model.encoder.weight_ih_l0)
    gradient = encoder_input_weights.grad
    assert gradient is not None
    assert torch.count_nonzero(gradient).item() > 0
    generated, log_probability, heads, scores = model(*_inputs())
    assert generated.shape == (1, 1, 3)
    assert torch.all(generated > 0).item()
    assert log_probability.shape == (1, 1)
    assert heads.shape == (1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS))
    assert scores[0, 2].item() == -10_000.0


def test_torch_objective_matches_framework_neutral_reference() -> None:
    vocabulary_size = 8
    decoder = np.zeros((1, 3, vocabulary_size), dtype=np.float32)
    ranking = np.zeros((1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)), dtype=np.float32)
    target = np.asarray(((1, 2, 3),), dtype=np.int64)
    labels = np.zeros_like(ranking)
    label_mask = np.zeros_like(ranking)
    label_mask[0, 0] = 1.0
    teacher = np.full_like(ranking, 0.5)
    teacher_mask = np.zeros_like(ranking)
    teacher_mask[0, :2] = 1.0
    actual = compute_torch_sona_losses(
        torch.from_numpy(decoder),
        torch.from_numpy(ranking),
        torch.from_numpy(target),
        torch.from_numpy(labels),
        torch.from_numpy(label_mask),
        torch.from_numpy(teacher),
        torch.from_numpy(teacher_mask),
    )

    assert actual.ntp.item() == pytest.approx(log(vocabulary_size))
    assert actual.ranking.item() == pytest.approx(log(2.0))
    assert actual.distillation.item() == pytest.approx(log(2.0))
    assert actual.total.item() == pytest.approx(log(vocabulary_size) + 1.5 * log(2.0))
