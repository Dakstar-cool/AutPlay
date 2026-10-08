"""Shared portability scenarios pin the secret-free record schema and runtime contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]


def test_portable_record_schema_accepts_all_kinds_and_rejects_authority_and_overflow() -> None:
    schema: dict[str, Any] = json.loads(
        (ROOT / "contracts/portable-account/v1/account-record.schema.json").read_text(
            encoding="utf-8"
        )
    )
    cases: dict[str, Any] = json.loads(
        (ROOT / "tests/fixtures/portable-account/v1/record-contract-cases.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert {record["kind"] for record in cases["valid"]} == set(
        schema["properties"]["kind"]["enum"]
    )
    for record in cases["valid"]:
        validator.validate(record)
    for case in cases["invalid"]:
        assert not validator.is_valid(case["record"]), case["case"]
