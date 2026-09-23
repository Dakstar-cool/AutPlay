"""Frozen Face artifact-set and license-policy contract vectors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autplay.application.face_artifact_policy import (
    ArtifactPolicyEntry,
    FaceArtifactPolicyError,
    RequiredArtifact,
    decode_policy_list,
    decode_required_set,
    freeze_face_artifact_policy,
    policy_list,
    required_artifact_set,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/face/artifact-policy-v1"
ROLES = (
    "ENCODER_WEIGHTS",
    "INTERPRETER_EXPORT",
    "CALIBRATION",
    "PREPROCESSING_EXECUTABLE",
    "DECODER_PROBE",
    "TIMELINE_CODEC",
)


def _cardinalities(**overrides: int) -> dict[str, int]:
    values = dict.fromkeys(ROLES, 0)
    values["ENCODER_WEIGHTS"] = 1
    values["INTERPRETER_EXPORT"] = 1
    values.update(overrides)
    return values


def _entry(role: str, marker: str) -> RequiredArtifact:
    return RequiredArtifact(role, bytes.fromhex(marker * 64))


def _policy(
    entry: RequiredArtifact,
    *,
    lag: int = 604_800_000,
    disposition: str = "RETAIN_NON_DISTRIBUTABLE",
    state: str = "APPROVED",
) -> ArtifactPolicyEntry:
    return ArtifactPolicyEntry(
        role=entry.role,
        artifact_sha256=entry.artifact_sha256,
        decision_sequence=1,
        decision_generation=1,
        max_offline_revocation_lag_ms=lag,
        disposition=disposition,
        state=state,
    )


@pytest.mark.parametrize("name", ("empty", "minimal", "multi-role", "same-hash-two-role"))
def test_frozen_golden_bytes_and_digest(name: str) -> None:
    vector = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    required = tuple(
        RequiredArtifact(item["role"], bytes.fromhex(item["artifact_sha256"]))
        for item in vector["required_entries"]
    )
    decisions = tuple(
        ArtifactPolicyEntry(
            role=item["role"],
            artifact_sha256=bytes.fromhex(item["artifact_sha256"]),
            decision_sequence=item["decision_sequence"],
            decision_generation=item["decision_generation"],
            max_offline_revocation_lag_ms=item["max_offline_revocation_lag_ms"],
            disposition=item["disposition"],
            state="APPROVED",
        )
        for item in vector["policy_entries"]
    )
    required_document = required_artifact_set(required)
    policy_document = policy_list(decisions)
    assert required_document.canonical_bytes.decode() == vector["required_canonical"]
    assert required_document.sha256.hex() == vector["required_sha256"]
    assert policy_document.canonical_bytes.decode() == vector["policy_canonical"]
    assert policy_document.sha256.hex() == vector["policy_sha256"]
    assert decode_required_set(required_document.canonical_bytes) == required_document
    assert decode_policy_list(policy_document.canonical_bytes) == policy_document


def test_complete_set_derives_minimum_lease_and_strictest_disposition() -> None:
    encoder = _entry("ENCODER_WEIGHTS", "a")
    interpreter = _entry("INTERPRETER_EXPORT", "b")
    calibration = _entry("CALIBRATION", "c")
    frozen = freeze_face_artifact_policy(
        (calibration, interpreter, encoder),
        (
            _policy(interpreter, lag=800_000_000),
            _policy(calibration, lag=172_800_000, disposition="DELETE_AFTER_LEASE"),
            _policy(encoder, lag=604_800_000),
        ),
        cardinalities=_cardinalities(CALIBRATION=1),
    )
    assert frozen.offline_lease_ms == 172_800_000
    assert frozen.derived_output_disposition == "DELETE_AFTER_LEASE"
    assert [entry.role for entry in frozen.required_entries] == [
        "CALIBRATION",
        "ENCODER_WEIGHTS",
        "INTERPRETER_EXPORT",
    ]


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("missing_interpreter", "ml.face.artifact_cardinality"),
        ("unlisted_policy", "ml.face.artifact_policy_mismatch"),
        ("zero_lag", "ml.face.offline_lease_unavailable"),
        ("revoked", "ml.face.artifact_not_approved"),
        ("duplicate", "ml.face.artifact_duplicate"),
    ),
)
def test_activation_fails_closed_for_incomplete_or_noncurrent_artifacts(
    mutation: str, code: str
) -> None:
    encoder = _entry("ENCODER_WEIGHTS", "a")
    interpreter = _entry("INTERPRETER_EXPORT", "b")
    required: tuple[RequiredArtifact, ...] = (encoder, interpreter)
    policies: tuple[ArtifactPolicyEntry, ...] = (_policy(encoder), _policy(interpreter))
    if mutation == "missing_interpreter":
        required = (encoder,)
        policies = (_policy(encoder),)
    elif mutation == "unlisted_policy":
        policies = (_policy(encoder), _policy(_entry("CALIBRATION", "c")))
    elif mutation == "zero_lag":
        policies = (_policy(encoder), _policy(interpreter, lag=0))
    elif mutation == "revoked":
        policies = (_policy(encoder), _policy(interpreter, state="REVOKED"))
    elif mutation == "duplicate":
        required = (encoder, encoder, interpreter)
    with pytest.raises(FaceArtifactPolicyError) as error:
        freeze_face_artifact_policy(required, policies, cardinalities=_cardinalities())
    assert error.value.code == code


@pytest.mark.parametrize(
    "payload",
    (
        b'{"v":1,"v":1,"entries":[]}',
        b'{"v":1,"entries":[{"role":"ENCODER_WEIGHTS","artifact_sha256":"' + b"A" * 64 + b'"}]}',
        b'{"v":true,"entries":[]}',
        b'{"v":1.0,"entries":[]}',
        b'{"v":1,"entries":[]}',
        b'{"v":1,"entries":[],"extra":0}',
    ),
)
def test_required_set_parser_rejects_noncanonical_or_ambiguous_bytes(payload: bytes) -> None:
    with pytest.raises(FaceArtifactPolicyError):
        decode_required_set(payload)


def test_same_hash_may_fill_two_explicit_roles_but_not_duplicate_one_role() -> None:
    encoder = _entry("ENCODER_WEIGHTS", "a")
    interpreter = RequiredArtifact("INTERPRETER_EXPORT", encoder.artifact_sha256)
    frozen = freeze_face_artifact_policy(
        (encoder, interpreter),
        (_policy(encoder), _policy(interpreter)),
        cardinalities=_cardinalities(),
    )
    assert len(frozen.required_entries) == 2
    with pytest.raises(FaceArtifactPolicyError, match=r"ml\.face\.artifact_duplicate"):
        required_artifact_set((encoder, encoder))
