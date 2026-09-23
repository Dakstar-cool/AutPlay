"""Operation-bound review request bytes are unambiguous before WebAuthn issuance."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from autplay.application.artifact_license_review_command import (
    ARTIFACT_REVIEW_COMMAND_DOMAIN,
    MAX_ARTIFACT_REVIEW_COMMAND_BYTES,
    ArtifactReviewCommandError,
    parse_artifact_review_command,
)
from autplay.domain.artifact_license_review import LicenseDecisionState

_ARTIFACT = "a" * 64


def _body() -> dict[str, object]:
    return {
        "operation_id": "10000000-0000-4000-8000-000000000001",
        "expected_generation": 2,
        "artifact_sha256": _ARTIFACT,
        "state": "APPROVED",
        "license_identifier": "SPDX:CC-BY-4.0",
        "license_text_sha256": "b" * 64,
        "use_restrictions": {"commercial": True, "named": ["offline", "face"]},
        "redistribution_decision": "PERMITTED",
        "modification_decision": "PERMITTED",
        "attribution_payload": {"notice": "fixture"},
        "review_reference": "operator-reviewed-fixture",
        "reason_code": "LICENSE_REVIEWED",
        "max_offline_revocation_lag_ms": 86_400_000,
        "derived_output_disposition": "RETAIN_NON_DISTRIBUTABLE",
    }


def _parse(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def test_review_hash_binds_action_path_and_complete_command() -> None:
    body = _body()
    first = parse_artifact_review_command(_parse(body), path_artifact_sha256=_ARTIFACT)
    reordered = dict(reversed(list(body.items())))
    second = parse_artifact_review_command(_parse(reordered), path_artifact_sha256=_ARTIFACT)
    assert first.request_sha256 == second.request_sha256
    assert first.request_sha256.hex() == (
        "f58b0a34ded7f371ea948a83f972090df24fbb243564f4cba4b6897d02552550"
    )
    assert first.review.state is LicenseDecisionState.APPROVED
    assert first.expected_generation == 2
    assert ARTIFACT_REVIEW_COMMAND_DOMAIN.endswith(b"\0")
    changed = deepcopy(body)
    changed["reason_code"] = "CORRECTED_NOTICE"
    assert (
        parse_artifact_review_command(
            _parse(changed), path_artifact_sha256=_ARTIFACT
        ).request_sha256
        != first.request_sha256
    )


def test_review_rejects_duplicate_nested_key_and_path_substitution() -> None:
    raw = _parse(_body()).replace(b'"commercial":true', b'"commercial":true,"commercial":false')
    with pytest.raises(ArtifactReviewCommandError):
        parse_artifact_review_command(raw, path_artifact_sha256=_ARTIFACT)
    with pytest.raises(ArtifactReviewCommandError):
        parse_artifact_review_command(_parse(_body()), path_artifact_sha256="c" * 64)


def test_review_rejects_malformed_generation_and_policy() -> None:
    for field, value in (
        ("expected_generation", True),
        ("expected_generation", 9_007_199_254_740_991),
        ("max_offline_revocation_lag_ms", 0),
        ("artifact_sha256", "A" * 64),
        ("state", "LEGACY_UNREVIEWED"),
        ("reason_code", "lowercase"),
    ):
        body = _body()
        body[field] = value
        with pytest.raises(ArtifactReviewCommandError):
            parse_artifact_review_command(_parse(body), path_artifact_sha256=_ARTIFACT)
    denied = _body()
    denied["state"] = "DENIED"
    denied["max_offline_revocation_lag_ms"] = 0
    assert (
        parse_artifact_review_command(_parse(denied), path_artifact_sha256=_ARTIFACT).review.state
        is LicenseDecisionState.DENIED
    )


def test_review_rejects_resource_and_nonfinite_values() -> None:
    body = _body()
    body["use_restrictions"] = {"nested": [[[[[[[[[[[[[1]]]]]]]]]]]]]}
    with pytest.raises(ArtifactReviewCommandError):
        parse_artifact_review_command(_parse(body), path_artifact_sha256=_ARTIFACT)
    body = _body()
    body["attribution_payload"] = {"notice": "x" * 8192}
    with pytest.raises(ArtifactReviewCommandError):
        parse_artifact_review_command(_parse(body), path_artifact_sha256=_ARTIFACT)
    body = _body()
    body["use_restrictions"] = {"fraction": float("nan")}
    with pytest.raises(ArtifactReviewCommandError):
        parse_artifact_review_command(_parse(body), path_artifact_sha256=_ARTIFACT)
    with pytest.raises(ArtifactReviewCommandError):
        parse_artifact_review_command(
            b" " * (MAX_ARTIFACT_REVIEW_COMMAND_BYTES + 1), path_artifact_sha256=_ARTIFACT
        )
