"""The disabled self-service Face policy command cannot choose another owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

_SCHEMA = (
    Path(__file__).resolve().parents[2] / "contracts/ml/v1/face-analysis-policy-command.schema.json"
)


def test_face_policy_command_is_self_only_and_generation_bounded() -> None:
    schema = cast(dict[str, Any], json.loads(_SCHEMA.read_text(encoding="utf-8")))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid: dict[str, object] = {
        "operation_id": "10000000-0000-4000-8000-000000000001",
        "expected_generation": 0,
        "enabled": True,
        "scope": "NEW_UPLOADS",
    }
    validator.validate(valid)
    for bad in (
        {**valid, "owner_user_id": "20000000-0000-4000-8000-000000000002"},
        {**valid, "expected_generation": -1},
        {**valid, "expected_generation": 9_007_199_254_740_991},
        {**valid, "enabled": 1},
        {**valid, "scope": "BACKFILL"},
        {key: value for key, value in valid.items() if key != "operation_id"},
    ):
        with pytest.raises(ValidationError):
            validator.validate(bad)
