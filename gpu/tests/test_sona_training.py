"""Sona-Lite tensor collation and reference loss evidence."""

from __future__ import annotations

from math import log
from uuid import UUID

import numpy as np
import pytest
from autplay.application.sona_training import build_sona_training_example
from autplay.domain.sona import (
    SONA_MAX_CANDIDATES,
    SONA_RANKING_HEADS,
    SONA_SID_DEPTH,
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
from autplay_gpu.sona_training import compute_sona_losses, pack_sona_training_examples

OWNER = UUID("00000000-0000-7000-8000-000000000001")
TRACK_A = UUID("00000000-0000-7000-8000-000000000301")
TRACK_B = UUID("00000000-0000-7000-8000-000000000302")


def _example() -> SonaTrainingExample:
    request = SonaInferenceRequest(
        owner_user_id=OWNER,
        temporal_snapshot_id=UUID(int=10),
        baseline_snapshot_id=UUID(int=11),
        cutoff_at_ms=1_000,
        interaction_watermark=2,
        tokenizer_sha256="a" * 64,
        model_manifest_sha256="b" * 64,
        seed=19,
        history=(
            SonaHistoryEvent(
                UUID(int=1),
                TRACK_A,
                SonaSemanticId(1, 2, 3),
                SonaAction.ORGANIC_LISTEN,
                SonaOrigin.ORGANIC,
                4,
                900,
                1,
            ),
        ),
        candidates=(
            SonaCandidate(TRACK_A, SonaSemanticId(1, 2, 3)),
            SonaCandidate(TRACK_B, SonaSemanticId(4, 5, 6)),
        ),
        request_sha256="c" * 64,
    )
    outcome = SonaObservedOutcome(
        owner_user_id=OWNER,
        source_request_sha256=request.request_sha256,
        recording_id=TRACK_B,
        observed_at_ms=1_100,
        completion=1.0,
        skip=0.0,
    )
    teacher = SonaTeacherSnapshot(
        source_request_sha256=request.request_sha256,
        teacher_key="p11-offline-teacher",
        teacher_version="1",
        teacher_manifest_sha256="d" * 64,
        targets=(
            SonaTeacherTarget(TRACK_A, (0.2, 0.1, 0.8, 0.3)),
            SonaTeacherTarget(TRACK_B, (0.9, 0.7, 0.1, 0.8)),
        ),
        snapshot_sha256="e" * 64,
    )
    return build_sona_training_example(request, outcome, teacher)


def test_training_batch_reuses_frozen_request_tensors_and_masks_padding() -> None:
    example = _example()
    batch = pack_sona_training_examples((example,))

    assert batch.target_sids.tolist() == [[4, 5, 6]]
    assert batch.inputs.history_mask[0, -1] == 1
    assert batch.inputs.history_sids[0, -1].tolist() == [1, 2, 3]
    assert batch.inputs.candidate_mask[0, :3].tolist() == [1, 1, 0]
    assert batch.ranking_label_mask[0, 0].tolist() == [0.0, 0.0, 0.0, 1.0]
    assert batch.ranking_label_mask[0, 1].tolist() == [1.0, 0.0, 1.0, 1.0]
    assert batch.teacher_mask[0, :3, 0].tolist() == [1.0, 1.0, 0.0]
    assert batch.example_sha256 == (example.example_sha256,)


def test_reference_loss_combines_ntp_observed_ranking_and_teacher_distillation() -> None:
    batch = pack_sona_training_examples((_example(),))
    vocabulary_size = 8
    decoder_logits = np.zeros((1, SONA_SID_DEPTH, vocabulary_size), dtype=np.float32)
    ranking_logits = np.zeros((1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)), dtype=np.float32)
    ranking_logits[:, 2:] = 1_000.0

    losses = compute_sona_losses(decoder_logits, ranking_logits, batch)

    assert losses.ntp == pytest.approx(log(vocabulary_size))
    assert losses.ranking == pytest.approx(log(2.0))
    assert losses.distillation == pytest.approx(log(2.0))
    assert losses.total == pytest.approx(log(vocabulary_size) + 1.5 * log(2.0))


def test_training_batch_and_loss_reject_unbounded_inputs() -> None:
    with pytest.raises(ValueError, match="empty"):
        pack_sona_training_examples(())
    batch = pack_sona_training_examples((_example(),))
    with pytest.raises(ValueError, match="decoder"):
        compute_sona_losses(
            np.zeros((1, SONA_SID_DEPTH, 4), dtype=np.float32),
            np.zeros((1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)), dtype=np.float32),
            batch,
        )
