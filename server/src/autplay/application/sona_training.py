"""Deterministic construction of replay-bound Sona-Lite training examples."""

from __future__ import annotations

from hashlib import sha256

import rfc8785

from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SONA_RANKING_HEADS, SonaInferenceRequest
from autplay.domain.sona_training import (
    SONA_TRAINING_SCHEMA_VERSION,
    SonaObservedOutcome,
    SonaRankingTarget,
    SonaTeacherSnapshot,
    SonaTeacherTarget,
    SonaTrainingExample,
)

SONA_TEACHER_SNAPSHOT_KIND = "SONA_P11_TEACHER_SNAPSHOT_V1"


def build_sona_teacher_snapshot(
    *,
    source_request_sha256: str,
    teacher_key: str,
    teacher_version: str,
    teacher_manifest_sha256: str,
    targets: tuple[SonaTeacherTarget, ...],
) -> SonaTeacherSnapshot:
    """Build one canonical, candidate-complete offline teacher snapshot."""

    ordered_targets = tuple(sorted(targets, key=lambda value: value.recording_id.hex))
    document = _sona_teacher_snapshot_document(
        source_request_sha256=source_request_sha256,
        teacher_key=teacher_key,
        teacher_version=teacher_version,
        teacher_manifest_sha256=teacher_manifest_sha256,
        targets=ordered_targets,
    )
    return SonaTeacherSnapshot(
        source_request_sha256=source_request_sha256,
        teacher_key=teacher_key,
        teacher_version=teacher_version,
        teacher_manifest_sha256=teacher_manifest_sha256,
        targets=ordered_targets,
        snapshot_sha256=sha256(rfc8785.dumps(document)).hexdigest(),
    )


def verify_sona_teacher_snapshot(snapshot: SonaTeacherSnapshot) -> None:
    """Fail closed unless a teacher snapshot matches its canonical identity."""

    expected = build_sona_teacher_snapshot(
        source_request_sha256=snapshot.source_request_sha256,
        teacher_key=snapshot.teacher_key,
        teacher_version=snapshot.teacher_version,
        teacher_manifest_sha256=snapshot.teacher_manifest_sha256,
        targets=snapshot.targets,
    )
    if expected != snapshot:
        raise ValueError("Sona teacher snapshot hash is not canonical")


def _sona_teacher_snapshot_document(
    *,
    source_request_sha256: str,
    teacher_key: str,
    teacher_version: str,
    teacher_manifest_sha256: str,
    targets: tuple[SonaTeacherTarget, ...],
) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "snapshot_kind": SONA_TEACHER_SNAPSHOT_KIND,
        "source_request_sha256": source_request_sha256,
        "teacher_key": teacher_key,
        "teacher_version": teacher_version,
        "teacher_manifest_sha256": teacher_manifest_sha256,
        "ranking_heads": list(SONA_RANKING_HEADS),
        "targets": [
            {
                "recording_id": str(value.recording_id),
                "probabilities": list(value.probabilities),
            }
            for value in targets
        ],
    }


def build_sona_training_example(
    request: SonaInferenceRequest,
    outcome: SonaObservedOutcome,
    teacher: SonaTeacherSnapshot,
) -> SonaTrainingExample:
    """Bind observed and teacher labels to the exact historical request candidate set."""

    if outcome.owner_user_id != request.owner_user_id:
        raise ValueError("cross-owner Sona training outcome rejected")
    if outcome.source_request_sha256 != request.request_sha256:
        raise ValueError("Sona outcome is not bound to the original request")
    if teacher.source_request_sha256 != request.request_sha256:
        raise ValueError("Sona teacher snapshot is not bound to the original request")
    candidate_by_recording = {value.recording_id: value for value in request.candidates}
    candidate = candidate_by_recording.get(outcome.recording_id)
    if candidate is None:
        raise ValueError("Sona observed target is absent from the original candidate set")
    teacher_by_recording = {value.recording_id: value for value in teacher.targets}
    if set(teacher_by_recording) != set(candidate_by_recording):
        raise ValueError("Sona teacher snapshot must cover exactly the original candidate set")

    positive_labels = (
        0.0 if outcome.completion is None else outcome.completion,
        0.0 if outcome.like is None else outcome.like,
        0.0 if outcome.skip is None else outcome.skip,
        1.0,
    )
    positive_mask = (
        outcome.completion is not None,
        outcome.like is not None,
        outcome.skip is not None,
        True,
    )
    ranking_targets = tuple(
        SonaRankingTarget(
            recording_id=value.recording_id,
            labels=(
                positive_labels
                if value.recording_id == outcome.recording_id
                else (0.0, 0.0, 0.0, 0.0)
            ),
            label_mask=(
                positive_mask
                if value.recording_id == outcome.recording_id
                else (False, False, False, True)
            ),
            teacher_probabilities=teacher_by_recording[value.recording_id].probabilities,
        )
        for value in request.candidates
    )
    document: dict[str, JsonValue] = {
        "schema_version": SONA_TRAINING_SCHEMA_VERSION,
        "source_request_sha256": request.request_sha256,
        "owner_user_id": str(request.owner_user_id),
        "temporal_snapshot_id": str(request.temporal_snapshot_id),
        "baseline_snapshot_id": str(request.baseline_snapshot_id),
        "cutoff_at_ms": request.cutoff_at_ms,
        "interaction_watermark": request.interaction_watermark,
        "tokenizer_sha256": request.tokenizer_sha256,
        "observed_at_ms": outcome.observed_at_ms,
        "outcome_source": (
            None
            if outcome.outcome_source_event_id is None
            else {
                "event_id": str(outcome.outcome_source_event_id),
                "request_sha256": outcome.outcome_source_request_sha256,
            }
        ),
        "target_recording_id": str(outcome.recording_id),
        "target_semantic_id": list(candidate.semantic_id.values),
        "teacher": {
            "key": teacher.teacher_key,
            "version": teacher.teacher_version,
            "manifest_sha256": teacher.teacher_manifest_sha256,
            "snapshot_sha256": teacher.snapshot_sha256,
        },
        "ranking_targets": [
            {
                "recording_id": str(value.recording_id),
                "head_names": list(SONA_RANKING_HEADS),
                "labels": list(value.labels),
                "label_mask": list(value.label_mask),
                "teacher_probabilities": list(value.teacher_probabilities),
            }
            for value in ranking_targets
        ],
    }
    example_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    return SonaTrainingExample(
        request=request,
        observed_at_ms=outcome.observed_at_ms,
        target_recording_id=outcome.recording_id,
        target_semantic_id=candidate.semantic_id,
        teacher_key=teacher.teacher_key,
        teacher_version=teacher.teacher_version,
        teacher_manifest_sha256=teacher.teacher_manifest_sha256,
        teacher_snapshot_sha256=teacher.snapshot_sha256,
        ranking_targets=ranking_targets,
        example_sha256=example_sha256,
        outcome_source_event_id=outcome.outcome_source_event_id,
        outcome_source_request_sha256=outcome.outcome_source_request_sha256,
    )


__all__ = (
    "SONA_TEACHER_SNAPSHOT_KIND",
    "build_sona_teacher_snapshot",
    "build_sona_training_example",
    "verify_sona_teacher_snapshot",
)
