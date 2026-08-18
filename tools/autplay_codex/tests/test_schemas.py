from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError
from jsonschema.validators import validator_for


def _load_schema(name: str) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[3]
    document = json.loads((repository_root / "schemas" / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.mark.parametrize(
    ("schema_name", "valid_instance"),
    [
        (
            "autplay-codex-task-result.schema.json",
            {"status": "done", "summary": "complete", "checks": [], "risks": []},
        ),
        (
            "autplay-codex-review-result.schema.json",
            {"summary": "clean", "findings": []},
        ),
    ],
)
def test_checked_in_schema_is_valid_and_accepts_contract_example(
    schema_name: str, valid_instance: dict[str, Any]
) -> None:
    schema = _load_schema(schema_name)
    validator_type = validator_for(schema)
    validator_type.check_schema(schema)

    validator_type(schema).validate(valid_instance)


def test_task_schema_rejects_unknown_result_fields() -> None:
    schema = _load_schema("autplay-codex-task-result.schema.json")
    validator = validator_for(schema)(schema)

    with pytest.raises(ValidationError):
        validator.validate(
            {
                "status": "done",
                "summary": "complete",
                "checks": [],
                "risks": [],
                "unbounded_output": "forbidden",
            }
        )
