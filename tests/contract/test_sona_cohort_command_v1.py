"""Disabled R1C cohort command cannot bypass a generation or target binding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "contracts/ml/v1/sona-cohort-operation-command.schema.json"
)


def _validator() -> Draft202012Validator:
    schema = cast(dict[str, Any], json.loads(_SCHEMA.read_text(encoding="utf-8")))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _command(action: str) -> dict[str, object]:
    return {
        "operation_id": "10000000-0000-4000-8000-000000000001",
        "action": action,
        "expected_activation_epoch": 1,
        "expected_cohort_generation": 0,
        "expected_breaker_generation": 2 if action == "ENTER_HALF_OPEN" else None,
        "reason_code": "OPERATOR_REVIEW",
        "step_up_challenge_id": "20000000-0000-4000-8000-000000000002",
    }


def test_cohort_command_has_four_bounded_actions() -> None:
    validator = _validator()
    for action in ("ADD_RECIPIENT", "REMOVE_RECIPIENT", "KILL_COHORT", "ENTER_HALF_OPEN"):
        validator.validate(_command(action))


def test_half_open_requires_exact_breaker_generation() -> None:
    validator = _validator()
    for invalid in (None, 0, 9_007_199_254_740_992):
        command = _command("ENTER_HALF_OPEN")
        command["expected_breaker_generation"] = invalid
        with pytest.raises(ValidationError):
            validator.validate(command)
    command = _command("KILL_COHORT")
    command["expected_breaker_generation"] = 2
    with pytest.raises(ValidationError):
        validator.validate(command)


def test_body_cannot_name_target_or_skip_authority_fields() -> None:
    validator = _validator()
    command = _command("ADD_RECIPIENT")
    for invalid in (
        {**command, "owner_user_id": "30000000-0000-4000-8000-000000000003"},
        {**command, "expected_activation_epoch": 0},
        {**command, "expected_cohort_generation": 9_007_199_254_740_991},
        {key: value for key, value in command.items() if key != "step_up_challenge_id"},
        {**command, "action": "FORCE_CLOSED"},
    ):
        with pytest.raises(ValidationError):
            validator.validate(invalid)
