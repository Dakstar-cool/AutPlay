"""Deterministic P11 temperature calibration with exact dataset ancestry."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from math import exp, isfinite, log1p
from pathlib import Path
from typing import Final, cast
from uuid import UUID, uuid4

import rfc8785
from autplay.application.recommendations import baseline_pipeline_definition
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SONA_RANKING_HEADS

SONA_TEACHER_CALIBRATION_SET_KIND: Final = "SONA_P11_TEACHER_CALIBRATION_SET_V1"
SONA_TEACHER_MANIFEST_KIND: Final = "SONA_P11_TEACHER_V2"
SONA_TEACHER_MANIFEST_SCHEMA_VERSION: Final = 2
SONA_TEACHER_CALIBRATION_POLICY: Final = "P11_LOGIT_TEMPERATURE_CALIBRATION_V1"
SONA_TEACHER_CALIBRATION_FIT_ALGORITHM: Final = (
    "PER_HEAD_BCE_GRID_TEMPERATURE_MILLI_50_20000_STEP_50_V1"
)
SONA_TEACHER_INPUT_VALUE_SEMANTICS: Final = "P11_RAW_HEURISTIC_SCORE_AS_LOGIT_V1"
SONA_TEACHER_CALIBRATION_SPLIT: Final = "validation"
SONA_TEACHER_MIN_TEMPERATURE_MILLI: Final = 50
SONA_TEACHER_MAX_TEMPERATURE_MILLI: Final = 20_000
SONA_TEACHER_TEMPERATURE_STEP_MILLI: Final = 50
SONA_TEACHER_MAX_CALIBRATION_REQUESTS: Final = 4_096
SONA_TEACHER_MAX_CANDIDATES_PER_REQUEST: Final = 1_024
SONA_TEACHER_MAX_ABSOLUTE_P11_SCORE: Final = 100.0
SONA_TEACHER_CALIBRATION_MAX_BYTES: Final = 1_073_741_824

_CALIBRATION_SET_KEYS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "source_model_manifest_sha256",
        "p11_pipeline_manifest_sha256",
        "ranking_heads",
        "calibration_split",
        "input_value_semantics",
        "example_count",
        "candidate_count",
        "request_set_sha256",
        "label_set_sha256",
        "contains_raw_owner_ids",
        "examples",
    }
)

_TEACHER_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "manifest_kind",
        "teacher_key",
        "teacher_version",
        "source_model_manifest_sha256",
        "p11_pipeline_manifest_sha256",
        "ranking_heads",
        "calibration_policy",
        "calibration_fit_algorithm",
        "input_value_semantics",
        "calibration_split",
        "calibration_input_manifest_sha256",
        "calibration_request_set_sha256",
        "calibration_label_set_sha256",
        "calibration_example_count",
        "calibration_candidate_count",
        "head_temperature_milli",
        "calibration_fit_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class SonaTeacherCalibrationCandidate:
    """One raw P11 score and its masked observed labels."""

    recording_id: UUID
    raw_p11_score: float
    labels: tuple[float, float, float, float]
    label_mask: tuple[bool, bool, bool, bool]

    def __post_init__(self) -> None:
        if (
            not isfinite(self.raw_p11_score)
            or abs(self.raw_p11_score) > SONA_TEACHER_MAX_ABSOLUTE_P11_SCORE
        ):
            raise ValueError("Sona teacher calibration score is invalid")
        if len(self.labels) != len(SONA_RANKING_HEADS) or any(
            not isfinite(value) or not 0.0 <= value <= 1.0 for value in self.labels
        ):
            raise ValueError("Sona teacher calibration labels are invalid")
        if len(self.label_mask) != len(SONA_RANKING_HEADS) or any(
            not isinstance(value, bool) for value in self.label_mask
        ):
            raise ValueError("Sona teacher calibration label mask is invalid")


@dataclass(frozen=True, slots=True)
class SonaTeacherCalibrationExample:
    """One P11-to-Sona request pair used only by the validation calibration split."""

    p11_request_sha256: str
    sona_request_sha256: str
    candidates: tuple[SonaTeacherCalibrationCandidate, ...]

    def __post_init__(self) -> None:
        _validate_sha256(self.p11_request_sha256, "p11_request_sha256")
        _validate_sha256(self.sona_request_sha256, "sona_request_sha256")
        if self.p11_request_sha256 == self.sona_request_sha256:
            raise ValueError("Sona teacher calibration request identity stages overlap")
        recording_ids = tuple(value.recording_id for value in self.candidates)
        if not 1 <= len(recording_ids) <= SONA_TEACHER_MAX_CANDIDATES_PER_REQUEST or len(
            recording_ids
        ) != len(set(recording_ids)):
            raise ValueError("Sona teacher calibration candidates are not unique and bounded")


@dataclass(frozen=True, slots=True)
class SonaTeacherCalibrationSet:
    """Canonical raw-score/label artifact whose digest is teacher ancestry."""

    source_model_manifest_sha256: str
    p11_pipeline_manifest_sha256: str
    examples: tuple[SonaTeacherCalibrationExample, ...]
    request_set_sha256: str
    label_set_sha256: str
    candidate_count: int
    document: dict[str, JsonValue]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SonaTeacherCalibrationFit:
    """Exact grid-selected temperatures bound to one calibration artifact."""

    calibration_input_manifest_sha256: str
    head_temperature_milli: tuple[tuple[str, int], ...]
    document: dict[str, JsonValue]
    fit_sha256: str


def build_sona_teacher_calibration_set(
    examples: Sequence[SonaTeacherCalibrationExample],
    *,
    source_model_manifest_sha256: str,
    p11_pipeline_manifest_sha256: str,
) -> SonaTeacherCalibrationSet:
    """Canonicalize validation examples and bind every raw score and observed label."""

    _validate_sha256(source_model_manifest_sha256, "source_model_manifest_sha256")
    _validate_sha256(p11_pipeline_manifest_sha256, "p11_pipeline_manifest_sha256")
    if p11_pipeline_manifest_sha256 != baseline_pipeline_definition().manifest_sha256:
        raise ValueError("Sona teacher calibration is not bound to frozen cpu-baseline:1")
    ordered_examples = tuple(
        replace(
            example,
            candidates=tuple(sorted(example.candidates, key=lambda value: value.recording_id.hex)),
        )
        for example in sorted(examples, key=lambda value: value.sona_request_sha256)
    )
    if not 1 <= len(ordered_examples) <= SONA_TEACHER_MAX_CALIBRATION_REQUESTS:
        raise ValueError("Sona teacher calibration request count is outside the accepted bound")
    p11_hashes = tuple(value.p11_request_sha256 for value in ordered_examples)
    sona_hashes = tuple(value.sona_request_sha256 for value in ordered_examples)
    if (
        len(p11_hashes) != len(set(p11_hashes))
        or len(sona_hashes) != len(set(sona_hashes))
        or set(p11_hashes) & set(sona_hashes)
    ):
        raise ValueError("Sona teacher calibration request identities are not one-to-one")

    examples_document = cast(
        list[JsonValue],
        [
            {
                "p11_request_sha256": example.p11_request_sha256,
                "sona_request_sha256": example.sona_request_sha256,
                "candidates": [
                    {
                        "recording_id": str(candidate.recording_id),
                        "raw_p11_score": candidate.raw_p11_score,
                        "labels": list(candidate.labels),
                        "label_mask": list(candidate.label_mask),
                    }
                    for candidate in example.candidates
                ],
            }
            for example in ordered_examples
        ],
    )
    request_set_sha256 = _canonical_sha256(cast(list[JsonValue], list(sona_hashes)))
    label_set_document: list[JsonValue] = [
        {
            "sona_request_sha256": example.sona_request_sha256,
            "candidates": [
                {
                    "recording_id": str(candidate.recording_id),
                    "labels": list(candidate.labels),
                    "label_mask": list(candidate.label_mask),
                }
                for candidate in example.candidates
            ],
        }
        for example in ordered_examples
    ]
    label_set_sha256 = _canonical_sha256(label_set_document)
    candidate_count = sum(len(value.candidates) for value in ordered_examples)
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "artifact_kind": SONA_TEACHER_CALIBRATION_SET_KIND,
        "source_model_manifest_sha256": source_model_manifest_sha256,
        "p11_pipeline_manifest_sha256": p11_pipeline_manifest_sha256,
        "ranking_heads": list(SONA_RANKING_HEADS),
        "calibration_split": SONA_TEACHER_CALIBRATION_SPLIT,
        "input_value_semantics": SONA_TEACHER_INPUT_VALUE_SEMANTICS,
        "example_count": len(ordered_examples),
        "candidate_count": candidate_count,
        "request_set_sha256": request_set_sha256,
        "label_set_sha256": label_set_sha256,
        "contains_raw_owner_ids": False,
        "examples": examples_document,
    }
    manifest_sha256 = _canonical_sha256(document)
    return SonaTeacherCalibrationSet(
        source_model_manifest_sha256=source_model_manifest_sha256,
        p11_pipeline_manifest_sha256=p11_pipeline_manifest_sha256,
        examples=ordered_examples,
        request_set_sha256=request_set_sha256,
        label_set_sha256=label_set_sha256,
        candidate_count=candidate_count,
        document=document,
        manifest_sha256=manifest_sha256,
    )


def verify_sona_teacher_calibration_set(value: SonaTeacherCalibrationSet) -> None:
    """Rebuild one calibration set and require byte-semantic equality."""

    expected = build_sona_teacher_calibration_set(
        value.examples,
        source_model_manifest_sha256=value.source_model_manifest_sha256,
        p11_pipeline_manifest_sha256=value.p11_pipeline_manifest_sha256,
    )
    if expected != value:
        raise ValueError("Sona teacher calibration set integrity verification failed")


def materialize_sona_teacher_calibration_set(
    calibration: SonaTeacherCalibrationSet, path: Path
) -> str:
    """Write a canonical, content-addressed calibration envelope without overwriting."""

    verify_sona_teacher_calibration_set(calibration)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    envelope: dict[str, JsonValue] = {
        "calibration": calibration.document,
        "calibration_manifest_sha256": calibration.manifest_sha256,
    }
    try:
        with temporary.open("xb") as handle:
            handle.write(rfc8785.dumps(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return calibration.manifest_sha256


def load_sona_teacher_calibration_set(path: Path) -> SonaTeacherCalibrationSet:
    """Load a bounded envelope and rebuild its exact semantic identity."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("Sona teacher calibration path is not a regular file")
    if path.stat().st_size > SONA_TEACHER_CALIBRATION_MAX_BYTES:
        raise ValueError("Sona teacher calibration artifact exceeds the accepted bound")
    parsed = cast(JsonValue, json.loads(path.read_bytes()))
    envelope = _required_object(parsed, "calibration envelope")
    if set(envelope) != {"calibration", "calibration_manifest_sha256"}:
        raise ValueError("Sona teacher calibration envelope keys are invalid")
    document = _required_object(envelope.get("calibration"), "calibration document")
    manifest_sha256 = _required_string(envelope, "calibration_manifest_sha256")
    _validate_sha256(manifest_sha256, "calibration_manifest_sha256")
    if _canonical_sha256(cast(JsonValue, document)) != manifest_sha256:
        raise ValueError("Sona teacher calibration artifact hash mismatch")
    if set(document) != _CALIBRATION_SET_KEYS:
        raise ValueError("Sona teacher calibration document keys are invalid")

    examples = tuple(
        _parse_calibration_example(value) for value in _required_list(document, "examples")
    )
    rebuilt = build_sona_teacher_calibration_set(
        examples,
        source_model_manifest_sha256=_required_string(document, "source_model_manifest_sha256"),
        p11_pipeline_manifest_sha256=_required_string(document, "p11_pipeline_manifest_sha256"),
    )
    if rebuilt.document != document or rebuilt.manifest_sha256 != manifest_sha256:
        raise ValueError("Sona teacher calibration artifact contents are invalid")
    return rebuilt


def fit_sona_teacher_temperatures(
    calibration: SonaTeacherCalibrationSet,
) -> SonaTeacherCalibrationFit:
    """Fit four exact grid temperatures using masked binary cross entropy."""

    verify_sona_teacher_calibration_set(calibration)
    temperatures: list[tuple[str, int]] = []
    for head_index, head_name in enumerate(SONA_RANKING_HEADS):
        observations = tuple(
            (candidate.raw_p11_score, candidate.labels[head_index])
            for example in calibration.examples
            for candidate in example.candidates
            if candidate.label_mask[head_index]
        )
        if not observations:
            raise ValueError(f"Sona teacher calibration head {head_name} has no observed labels")
        selected_milli = min(
            range(
                SONA_TEACHER_MIN_TEMPERATURE_MILLI,
                SONA_TEACHER_MAX_TEMPERATURE_MILLI + 1,
                SONA_TEACHER_TEMPERATURE_STEP_MILLI,
            ),
            key=lambda milli: (_mean_binary_cross_entropy(observations, milli), milli),
        )
        temperatures.append((head_name, selected_milli))
    temperature_document: dict[str, JsonValue] = {name: milli for name, milli in temperatures}
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "fit_algorithm": SONA_TEACHER_CALIBRATION_FIT_ALGORITHM,
        "calibration_policy": SONA_TEACHER_CALIBRATION_POLICY,
        "calibration_input_manifest_sha256": calibration.manifest_sha256,
        "head_temperature_milli": temperature_document,
    }
    fit_sha256 = _canonical_sha256(document)
    return SonaTeacherCalibrationFit(
        calibration_input_manifest_sha256=calibration.manifest_sha256,
        head_temperature_milli=tuple(temperatures),
        document=document,
        fit_sha256=fit_sha256,
    )


def build_sona_teacher_manifest(
    calibration: SonaTeacherCalibrationSet,
    *,
    teacher_key: str,
    teacher_version: str,
) -> dict[str, JsonValue]:
    """Build the only quality teacher manifest accepted after the V2 boundary change."""

    _validate_label(teacher_key, "teacher_key")
    _validate_label(teacher_version, "teacher_version")
    fit = fit_sona_teacher_temperatures(calibration)
    return {
        "schema_version": SONA_TEACHER_MANIFEST_SCHEMA_VERSION,
        "manifest_kind": SONA_TEACHER_MANIFEST_KIND,
        "teacher_key": teacher_key,
        "teacher_version": teacher_version,
        "source_model_manifest_sha256": calibration.source_model_manifest_sha256,
        "p11_pipeline_manifest_sha256": calibration.p11_pipeline_manifest_sha256,
        "ranking_heads": list(SONA_RANKING_HEADS),
        "calibration_policy": SONA_TEACHER_CALIBRATION_POLICY,
        "calibration_fit_algorithm": SONA_TEACHER_CALIBRATION_FIT_ALGORITHM,
        "input_value_semantics": SONA_TEACHER_INPUT_VALUE_SEMANTICS,
        "calibration_split": SONA_TEACHER_CALIBRATION_SPLIT,
        "calibration_input_manifest_sha256": calibration.manifest_sha256,
        "calibration_request_set_sha256": calibration.request_set_sha256,
        "calibration_label_set_sha256": calibration.label_set_sha256,
        "calibration_example_count": len(calibration.examples),
        "calibration_candidate_count": calibration.candidate_count,
        "head_temperature_milli": {name: value for name, value in fit.head_temperature_milli},
        "calibration_fit_sha256": fit.fit_sha256,
    }


def verify_sona_teacher_manifest(
    manifest: Mapping[str, object],
    calibration: SonaTeacherCalibrationSet,
) -> None:
    """Re-fit from the bound labels and raw scores, then require the exact V2 manifest."""

    validate_sona_teacher_manifest_shape(manifest)
    teacher_key = _required_string(manifest, "teacher_key")
    teacher_version = _required_string(manifest, "teacher_version")
    expected = build_sona_teacher_manifest(
        calibration,
        teacher_key=teacher_key,
        teacher_version=teacher_version,
    )
    if dict(manifest) != expected:
        raise ValueError("Sona teacher manifest calibration ancestry or fit is invalid")


def validate_sona_teacher_manifest_shape(manifest: Mapping[str, object]) -> None:
    """Reject legacy or incomplete teacher evidence before it reaches a quality bundle."""

    if set(manifest) != _TEACHER_MANIFEST_KEYS:
        raise ValueError("Sona teacher manifest keys are invalid")
    if (
        _required_int(manifest, "schema_version") != SONA_TEACHER_MANIFEST_SCHEMA_VERSION
        or _required_string(manifest, "manifest_kind") != SONA_TEACHER_MANIFEST_KIND
        or _required_string(manifest, "calibration_policy") != SONA_TEACHER_CALIBRATION_POLICY
        or _required_string(manifest, "calibration_fit_algorithm")
        != SONA_TEACHER_CALIBRATION_FIT_ALGORITHM
        or _required_string(manifest, "input_value_semantics") != SONA_TEACHER_INPUT_VALUE_SEMANTICS
        or _required_string(manifest, "calibration_split") != SONA_TEACHER_CALIBRATION_SPLIT
    ):
        raise ValueError("Sona teacher manifest contract is invalid")
    _validate_label(_required_string(manifest, "teacher_key"), "teacher_key")
    _validate_label(_required_string(manifest, "teacher_version"), "teacher_version")
    for field in (
        "source_model_manifest_sha256",
        "p11_pipeline_manifest_sha256",
        "calibration_input_manifest_sha256",
        "calibration_request_set_sha256",
        "calibration_label_set_sha256",
        "calibration_fit_sha256",
    ):
        _validate_sha256(_required_string(manifest, field), field)
    if (
        _required_string(manifest, "p11_pipeline_manifest_sha256")
        != baseline_pipeline_definition().manifest_sha256
    ):
        raise ValueError("Sona teacher manifest is not bound to frozen cpu-baseline:1")
    if _required_string_list(manifest, "ranking_heads") != list(SONA_RANKING_HEADS):
        raise ValueError("Sona teacher manifest ranking heads are invalid")
    example_count = _required_int(manifest, "calibration_example_count")
    candidate_count = _required_int(manifest, "calibration_candidate_count")
    if not 1 <= example_count <= SONA_TEACHER_MAX_CALIBRATION_REQUESTS or not (
        example_count <= candidate_count <= example_count * SONA_TEACHER_MAX_CANDIDATES_PER_REQUEST
    ):
        raise ValueError("Sona teacher manifest calibration counts are invalid")
    temperatures = _required_object(manifest.get("head_temperature_milli"), "temperatures")
    if set(temperatures) != set(SONA_RANKING_HEADS):
        raise ValueError("Sona teacher manifest temperature heads are invalid")
    for head in SONA_RANKING_HEADS:
        temperature = _required_int(temperatures, head)
        if not (
            SONA_TEACHER_MIN_TEMPERATURE_MILLI <= temperature <= SONA_TEACHER_MAX_TEMPERATURE_MILLI
            and temperature % SONA_TEACHER_TEMPERATURE_STEP_MILLI == 0
        ):
            raise ValueError("Sona teacher manifest temperature is invalid")


def calibrated_sona_teacher_probabilities(
    raw_p11_score: float,
    fit: SonaTeacherCalibrationFit,
) -> tuple[float, float, float, float]:
    """Convert one bounded P11 score into four temperature-scaled probabilities."""

    if not isfinite(raw_p11_score) or abs(raw_p11_score) > SONA_TEACHER_MAX_ABSOLUTE_P11_SCORE:
        raise ValueError("Sona teacher score is invalid")
    temperatures = dict(fit.head_temperature_milli)
    if tuple(temperatures) != SONA_RANKING_HEADS:
        raise ValueError("Sona teacher fit head order is invalid")
    return cast(
        tuple[float, float, float, float],
        tuple(
            _sigmoid(raw_p11_score * 1_000.0 / temperatures[head]) for head in SONA_RANKING_HEADS
        ),
    )


def _mean_binary_cross_entropy(
    observations: Sequence[tuple[float, float]], temperature_milli: int
) -> float:
    temperature = temperature_milli / 1_000.0
    total = 0.0
    for raw_score, label in observations:
        logit = raw_score / temperature
        total += max(logit, 0.0) - label * logit + log1p(exp(-abs(logit)))
    return total / len(observations)


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        inverse = exp(-value)
        return 1.0 / (1.0 + inverse)
    direct = exp(value)
    return direct / (1.0 + direct)


def _canonical_sha256(value: JsonValue) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Sona teacher {field} is not a lowercase SHA-256 digest")


def _validate_label(value: str, field: str) -> None:
    if not 1 <= len(value) <= 200 or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise ValueError(f"Sona teacher {field} is invalid")


def _required_string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise ValueError(f"Sona teacher {field} is invalid")
    return candidate


def _required_int(value: Mapping[str, object], field: str) -> int:
    candidate = value.get(field)
    if not isinstance(candidate, int) or isinstance(candidate, bool):
        raise ValueError(f"Sona teacher {field} is invalid")
    return candidate


def _required_string_list(value: Mapping[str, object], field: str) -> list[str]:
    candidate = value.get(field)
    if not isinstance(candidate, list) or any(not isinstance(item, str) for item in candidate):
        raise ValueError(f"Sona teacher {field} is invalid")
    return cast(list[str], candidate)


def _required_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"Sona teacher {field} is invalid")
    return cast(dict[str, object], value)


def _required_list(value: Mapping[str, object], field: str) -> list[object]:
    candidate = value.get(field)
    if not isinstance(candidate, list):
        raise ValueError(f"Sona teacher {field} is invalid")
    return cast(list[object], candidate)


def _parse_calibration_example(value: object) -> SonaTeacherCalibrationExample:
    document = _required_object(value, "calibration example")
    if set(document) != {"p11_request_sha256", "sona_request_sha256", "candidates"}:
        raise ValueError("Sona teacher calibration example keys are invalid")
    return SonaTeacherCalibrationExample(
        p11_request_sha256=_required_string(document, "p11_request_sha256"),
        sona_request_sha256=_required_string(document, "sona_request_sha256"),
        candidates=tuple(
            _parse_calibration_candidate(candidate)
            for candidate in _required_list(document, "candidates")
        ),
    )


def _parse_calibration_candidate(value: object) -> SonaTeacherCalibrationCandidate:
    document = _required_object(value, "calibration candidate")
    if set(document) != {"recording_id", "raw_p11_score", "labels", "label_mask"}:
        raise ValueError("Sona teacher calibration candidate keys are invalid")
    raw_score = document.get("raw_p11_score")
    if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
        raise ValueError("Sona teacher calibration raw_p11_score is invalid")
    labels = _required_list(document, "labels")
    label_mask = _required_list(document, "label_mask")
    numeric_labels: list[float] = []
    for item in labels:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise ValueError("Sona teacher calibration labels are invalid")
        numeric_labels.append(float(item))
    if any(not isinstance(item, bool) for item in label_mask):
        raise ValueError("Sona teacher calibration label_mask is invalid")
    try:
        recording_id = UUID(_required_string(document, "recording_id"))
    except ValueError as error:
        raise ValueError("Sona teacher calibration recording_id is invalid") from error
    return SonaTeacherCalibrationCandidate(
        recording_id=recording_id,
        raw_p11_score=float(raw_score),
        labels=cast(tuple[float, float, float, float], tuple(numeric_labels)),
        label_mask=cast(tuple[bool, bool, bool, bool], tuple(label_mask)),
    )


__all__ = (
    "SONA_TEACHER_CALIBRATION_FIT_ALGORITHM",
    "SONA_TEACHER_CALIBRATION_MAX_BYTES",
    "SONA_TEACHER_CALIBRATION_POLICY",
    "SONA_TEACHER_CALIBRATION_SET_KIND",
    "SONA_TEACHER_CALIBRATION_SPLIT",
    "SONA_TEACHER_INPUT_VALUE_SEMANTICS",
    "SONA_TEACHER_MANIFEST_KIND",
    "SONA_TEACHER_MANIFEST_SCHEMA_VERSION",
    "SonaTeacherCalibrationCandidate",
    "SonaTeacherCalibrationExample",
    "SonaTeacherCalibrationFit",
    "SonaTeacherCalibrationSet",
    "build_sona_teacher_calibration_set",
    "build_sona_teacher_manifest",
    "calibrated_sona_teacher_probabilities",
    "fit_sona_teacher_temperatures",
    "load_sona_teacher_calibration_set",
    "materialize_sona_teacher_calibration_set",
    "validate_sona_teacher_manifest_shape",
    "verify_sona_teacher_calibration_set",
    "verify_sona_teacher_manifest",
)
