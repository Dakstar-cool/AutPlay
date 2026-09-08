"""Exact P11 calibration ancestry and temperature-fit tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from autplay.application.recommendations import baseline_pipeline_definition
from autplay_sona_training.teacher_calibration import (
    SONA_TEACHER_CALIBRATION_FIT_ALGORITHM,
    SONA_TEACHER_MANIFEST_KIND,
    SONA_TEACHER_MANIFEST_SCHEMA_VERSION,
    SonaTeacherCalibrationCandidate,
    SonaTeacherCalibrationExample,
    SonaTeacherCalibrationSet,
    build_sona_teacher_calibration_set,
    build_sona_teacher_manifest,
    calibrated_sona_teacher_probabilities,
    fit_sona_teacher_temperatures,
    load_sona_teacher_calibration_set,
    materialize_sona_teacher_calibration_set,
    validate_sona_teacher_manifest_shape,
    verify_sona_teacher_calibration_set,
    verify_sona_teacher_manifest,
)

SOURCE_MODEL = "a" * 64
P11_PIPELINE = baseline_pipeline_definition().manifest_sha256


def _candidate(index: int, score: float, label: float) -> SonaTeacherCalibrationCandidate:
    return SonaTeacherCalibrationCandidate(
        UUID(f"00000000-0000-7000-8000-{index:012x}"),
        score,
        (label, label, 1.0 - label, label),
        (True, True, True, True),
    )


def _example(index: int) -> SonaTeacherCalibrationExample:
    return SonaTeacherCalibrationExample(
        p11_request_sha256=f"{index:064x}",
        sona_request_sha256=f"{index + 100:064x}",
        candidates=(
            _candidate(index * 10 + 2, -1.0, 0.0),
            _candidate(index * 10 + 1, 1.0, 1.0),
        ),
    )


def _calibration() -> SonaTeacherCalibrationSet:
    return build_sona_teacher_calibration_set(
        (_example(2), _example(1)),
        source_model_manifest_sha256=SOURCE_MODEL,
        p11_pipeline_manifest_sha256=P11_PIPELINE,
    )


def test_calibration_set_and_fit_are_order_independent_and_content_addressed() -> None:
    first = _calibration()
    second = build_sona_teacher_calibration_set(
        tuple(reversed((_example(2), _example(1)))),
        source_model_manifest_sha256=SOURCE_MODEL,
        p11_pipeline_manifest_sha256=P11_PIPELINE,
    )

    assert first == second
    assert first.document["contains_raw_owner_ids"] is False
    assert first.document["calibration_split"] == "validation"
    assert first.candidate_count == 4
    verify_sona_teacher_calibration_set(first)
    assert fit_sona_teacher_temperatures(first) == fit_sona_teacher_temperatures(second)


def test_v2_teacher_manifest_binds_and_refits_parameters_and_ancestry() -> None:
    calibration = _calibration()
    manifest = build_sona_teacher_manifest(
        calibration,
        teacher_key="p11-calibrated",
        teacher_version="2",
    )

    assert manifest["schema_version"] == SONA_TEACHER_MANIFEST_SCHEMA_VERSION
    assert manifest["manifest_kind"] == SONA_TEACHER_MANIFEST_KIND
    assert manifest["calibration_fit_algorithm"] == SONA_TEACHER_CALIBRATION_FIT_ALGORITHM
    assert manifest["calibration_input_manifest_sha256"] == calibration.manifest_sha256
    assert manifest["calibration_request_set_sha256"] == calibration.request_set_sha256
    assert manifest["calibration_label_set_sha256"] == calibration.label_set_sha256
    validate_sona_teacher_manifest_shape(manifest)
    verify_sona_teacher_manifest(manifest, calibration)

    temperatures = manifest["head_temperature_milli"]
    assert isinstance(temperatures, dict)
    tampered = {**manifest, "head_temperature_milli": {**temperatures, "like": 20_000}}
    with pytest.raises(ValueError, match="ancestry or fit"):
        verify_sona_teacher_manifest(tampered, calibration)


def test_calibration_artifact_round_trips_and_rejects_overwrite(tmp_path: Path) -> None:
    calibration = _calibration()
    path = tmp_path / "teacher-calibration.json"

    assert (
        materialize_sona_teacher_calibration_set(calibration, path) == calibration.manifest_sha256
    )
    assert load_sona_teacher_calibration_set(path) == calibration
    with pytest.raises(FileExistsError):
        materialize_sona_teacher_calibration_set(calibration, path)


def test_calibration_rejects_non_frozen_p11_pipeline() -> None:
    with pytest.raises(ValueError, match="frozen cpu-baseline:1"):
        build_sona_teacher_calibration_set(
            (_example(1),),
            source_model_manifest_sha256=SOURCE_MODEL,
            p11_pipeline_manifest_sha256="b" * 64,
        )


def test_legacy_teacher_manifest_shape_is_rejected() -> None:
    legacy = {
        "schema_version": 1,
        "manifest_kind": "SONA_P11_TEACHER_V1",
        "teacher_key": "p11-calibrated",
        "teacher_version": "1",
        "source_model_manifest_sha256": SOURCE_MODEL,
        "ranking_heads": ["completion", "like", "skip", "pairwise"],
        "calibration_policy": "P11_LOGIT_TEMPERATURE_CALIBRATION_V1",
    }

    with pytest.raises(ValueError, match="keys are invalid"):
        validate_sona_teacher_manifest_shape(legacy)


def test_label_change_changes_ancestry_and_fitted_manifest() -> None:
    original = _calibration()
    changed_candidate = replace(_example(1).candidates[0], labels=(1.0, 1.0, 0.0, 1.0))
    changed_example = replace(
        _example(1),
        candidates=(changed_candidate, _example(1).candidates[1]),
    )
    changed = build_sona_teacher_calibration_set(
        (changed_example, _example(2)),
        source_model_manifest_sha256=SOURCE_MODEL,
        p11_pipeline_manifest_sha256=P11_PIPELINE,
    )

    assert changed.label_set_sha256 != original.label_set_sha256
    assert changed.manifest_sha256 != original.manifest_sha256
    with pytest.raises(ValueError, match="ancestry or fit"):
        verify_sona_teacher_manifest(
            build_sona_teacher_manifest(original, teacher_key="teacher", teacher_version="2"),
            changed,
        )


def test_every_head_requires_observed_calibration_labels() -> None:
    candidate = replace(_candidate(1, 0.2, 1.0), label_mask=(True, False, True, True))
    calibration = build_sona_teacher_calibration_set(
        (SonaTeacherCalibrationExample("1" * 64, "2" * 64, (candidate,)),),
        source_model_manifest_sha256=SOURCE_MODEL,
        p11_pipeline_manifest_sha256=P11_PIPELINE,
    )

    with pytest.raises(ValueError, match="head like has no observed labels"):
        fit_sona_teacher_temperatures(calibration)


def test_calibrated_probabilities_are_finite_bounded_and_monotonic() -> None:
    fit = fit_sona_teacher_temperatures(_calibration())
    lower = calibrated_sona_teacher_probabilities(-1.0, fit)
    middle = calibrated_sona_teacher_probabilities(0.0, fit)
    upper = calibrated_sona_teacher_probabilities(1.0, fit)

    assert middle == (0.5, 0.5, 0.5, 0.5)
    assert all(
        0.0 <= low < mid < high <= 1.0 for low, mid, high in zip(lower, middle, upper, strict=True)
    )
