"""Replay-bound Sona-Lite training-example evidence."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from autplay.application.sona_training import (
    build_sona_teacher_snapshot,
    build_sona_training_example,
    verify_sona_teacher_snapshot,
)
from autplay.domain.sona import (
    SonaAction,
    SonaCandidate,
    SonaHistoryEvent,
    SonaInferenceRequest,
    SonaOrigin,
    SonaSemanticId,
)
from autplay.domain.sona_training import (
    SONA_MAX_LABEL_DELAY_MS,
    SonaObservedOutcome,
    SonaTeacherSnapshot,
    SonaTeacherTarget,
)

OWNER = UUID("00000000-0000-7000-8000-000000000001")
OTHER = UUID("00000000-0000-7000-8000-000000000002")
TRACK_A = UUID("00000000-0000-7000-8000-000000000301")
TRACK_B = UUID("00000000-0000-7000-8000-000000000302")
CUTOFF_MS = 1_788_375_600_000


def _request() -> SonaInferenceRequest:
    return SonaInferenceRequest(
        owner_user_id=OWNER,
        temporal_snapshot_id=UUID(int=10),
        baseline_snapshot_id=UUID(int=11),
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=5,
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
                CUTOFF_MS - 1_000,
                5,
            ),
        ),
        candidates=(
            SonaCandidate(TRACK_A, SonaSemanticId(1, 2, 3)),
            SonaCandidate(TRACK_B, SonaSemanticId(4, 5, 6)),
        ),
        request_sha256="c" * 64,
    )


def _outcome() -> SonaObservedOutcome:
    return SonaObservedOutcome(
        owner_user_id=OWNER,
        source_request_sha256="c" * 64,
        recording_id=TRACK_B,
        observed_at_ms=CUTOFF_MS + 1_000,
        completion=1.0,
        like=1.0,
        skip=0.0,
    )


def _teacher(*, reversed_order: bool = False) -> SonaTeacherSnapshot:
    targets = (
        SonaTeacherTarget(TRACK_A, (0.2, 0.1, 0.8, 0.3)),
        SonaTeacherTarget(TRACK_B, (0.9, 0.7, 0.1, 0.8)),
    )
    return SonaTeacherSnapshot(
        source_request_sha256="c" * 64,
        teacher_key="p11-offline-teacher",
        teacher_version="1",
        teacher_manifest_sha256="d" * 64,
        targets=tuple(reversed(targets)) if reversed_order else targets,
        snapshot_sha256="e" * 64,
    )


def test_teacher_snapshot_builder_is_canonical_and_order_independent() -> None:
    first = build_sona_teacher_snapshot(
        source_request_sha256="c" * 64,
        teacher_key="p11-offline-teacher",
        teacher_version="1",
        teacher_manifest_sha256="d" * 64,
        targets=(
            SonaTeacherTarget(TRACK_B, (0.9, 0.7, 0.1, 0.8)),
            SonaTeacherTarget(TRACK_A, (0.2, 0.1, 0.8, 0.3)),
        ),
    )
    second = build_sona_teacher_snapshot(
        source_request_sha256="c" * 64,
        teacher_key="p11-offline-teacher",
        teacher_version="1",
        teacher_manifest_sha256="d" * 64,
        targets=tuple(reversed(first.targets)),
    )

    assert first == second
    verify_sona_teacher_snapshot(first)
    with pytest.raises(ValueError, match="not canonical"):
        verify_sona_teacher_snapshot(replace(first, snapshot_sha256="f" * 64))


def test_training_example_is_deterministic_and_aligned_to_original_candidates() -> None:
    first = build_sona_training_example(_request(), _outcome(), _teacher())
    second = build_sona_training_example(_request(), _outcome(), _teacher(reversed_order=True))

    assert first == second
    assert first.target_semantic_id.values == (4, 5, 6)
    negative, positive = first.ranking_targets
    assert negative.labels == (0.0, 0.0, 0.0, 0.0)
    assert negative.label_mask == (False, False, False, True)
    assert positive.labels == (1.0, 1.0, 0.0, 1.0)
    assert positive.label_mask == (True, True, True, True)
    assert positive.teacher_probabilities == (0.9, 0.7, 0.1, 0.8)


@pytest.mark.parametrize(
    "outcome, message",
    (
        (replace(_outcome(), owner_user_id=OTHER), "cross-owner"),
        (replace(_outcome(), source_request_sha256="f" * 64), "original request"),
        (replace(_outcome(), recording_id=UUID(int=999)), "candidate set"),
        (
            replace(_outcome(), observed_at_ms=CUTOFF_MS + SONA_MAX_LABEL_DELAY_MS + 1),
            "label window",
        ),
    ),
)
def test_training_example_rejects_current_state_substitution(
    outcome: SonaObservedOutcome, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_sona_training_example(_request(), outcome, _teacher())


def test_training_example_rejects_incomplete_or_foreign_teacher_snapshot() -> None:
    incomplete = replace(_teacher(), targets=_teacher().targets[:1])
    foreign = replace(_teacher(), source_request_sha256="f" * 64)

    with pytest.raises(ValueError, match="exactly"):
        build_sona_training_example(_request(), _outcome(), incomplete)
    with pytest.raises(ValueError, match="original request"):
        build_sona_training_example(_request(), _outcome(), foreign)
