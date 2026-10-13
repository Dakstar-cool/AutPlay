"""Run the same language-neutral record scenarios through the actual server validator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from autplay.application.portable_account_data import validate_portable_record

ROOT = Path(__file__).resolve().parents[2]


def test_server_validator_agrees_with_shared_portable_record_contract_scenarios() -> None:
    cases: dict[str, Any] = json.loads(
        (ROOT / "tests/fixtures/portable-account/v1/record-contract-cases.json").read_text(
            encoding="utf-8"
        )
    )
    for record in cases["valid"]:
        identifier, kind, document, digest = validate_portable_record(record)
        assert str(identifier) == record["record_id"] and kind == record["kind"]
        assert document == record["document"] and len(digest) == 32
    for case in cases["invalid"]:
        with pytest.raises(ValueError):
            validate_portable_record(case["record"])
