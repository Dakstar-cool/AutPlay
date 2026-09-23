"""The disabled review wire schema preserves the operation-bound review shape."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "contracts/ml/v1/artifact-license-review-command.schema.json"
)


def _validator() -> Draft202012Validator:
    schema = cast(dict[str, Any], json.loads(_SCHEMA.read_text(encoding="utf-8")))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _command() -> dict[str, object]:
    return {
        "operation_id": "10000000-0000-4000-8000-000000000001",
        "expected_generation": 2,
        "artifact_sha256": "a" * 64,
        "state": "APPROVED",
        "license_identifier": "SPDX:CC-BY-4.0",
        "license_text_sha256": "b" * 64,
        "use_restrictions": {"commercial": True},
        "redistribution_decision": "PERMITTED",
        "modification_decision": "PERMITTED",
        "attribution_payload": {"notice": "fixture"},
        "review_reference": "operator-reviewed-fixture",
        "reason_code": "LICENSE_REVIEWED",
        "max_offline_revocation_lag_ms": 86_400_000,
        "derived_output_disposition": "RETAIN_NON_DISTRIBUTABLE",
    }


def test_approved_review_and_zero_lag_denial_are_valid() -> None:
    validator = _validator()
    validator.validate(_command())
    denied = _command()
    denied["state"] = "DENIED"
    denied["max_offline_revocation_lag_ms"] = 0
    validator.validate(denied)


def test_review_schema_rejects_unreviewed_or_invalid_generation_and_lag() -> None:
    validator = _validator()
    for changes in (
        {"state": "LEGACY_UNREVIEWED"},
        {"expected_generation": 9_007_199_254_740_991},
        {"max_offline_revocation_lag_ms": 0},
        {"artifact_sha256": "A" * 64},
        {"reason_code": "lowercase"},
    ):
        with pytest.raises(ValidationError):
            validator.validate({**_command(), **changes})


def test_review_schema_does_not_accept_actor_or_step_up_claims_from_body() -> None:
    validator = _validator()
    for field in ("reviewer_user_id", "reviewed_at", "step_up_challenge_id", "actor_role"):
        with pytest.raises(ValidationError):
            validator.validate({**_command(), field: "forged"})
