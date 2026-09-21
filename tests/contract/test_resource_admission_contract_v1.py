"""Language-neutral admission documents and public device-only API shape."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts/resource-admission/v1"
EXAMPLES = ROOT / "tests/fixtures/resource-admission/v1/schema-examples.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        _load(SCHEMAS / f"{name}.schema.json"), format_checker=FormatChecker()
    )


def test_admission_schemas_require_exact_documents_and_openapi_matches() -> None:
    examples = _load(EXAMPLES)
    assert {path.stem.removesuffix(".schema") for path in SCHEMAS.glob("*.json")} == set(examples)
    api = _load(ROOT / "contracts/openapi/v1/autplay-resource-admission.openapi.json")
    validate(api)
    for name, document in examples.items():
        schema = _load(SCHEMAS / f"{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        validator = _validator(name)
        validator.validate(document)
        assert not validator.is_valid({**document, "authority_kind": "SERVER_ACQUISITION"})
        for required in schema["required"]:
            assert not validator.is_valid({k: v for k, v in document.items() if k != required})
        assert api["components"]["schemas"][name] == {
            key: value for key, value in schema.items() if key not in {"$id", "$schema"}
        }
    operations = [op for path in api["paths"].values() for op in path.values()]
    assert len(operations) == 5
    for operation in operations:
        assert operation["security"] == [{"bearerAuth": []}]
        if operation["operationId"] != "resourcePoll":
            assert operation["x-autplay-max-request-bytes"] == 4096
        assert set(operation["responses"]) == {"200", "400", "401", "403", "409", "429", "503"}


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("acquire-playback-request", "schema_version", True),
        ("acquire-playback-request", "resource_id", "10000000-0000-4000-8000-00000000000A"),
        ("acquire-playback-request", "kind", "TRANSFER"),
        ("acquire-transfer-request", "resource_type", "INTERNET_ACQUISITION"),
        ("acquire-transfer-request", "target_id", None),
        ("renew-request", "generation", "1"),
        ("renew-request", "generation", 0),
        ("renew-request", "generation", 2**53),
        ("cancel-waiting-request", "reason", "COMPLETE"),
        ("cancel-waiting-request", "operation_sha256", "A" * 64),
        ("attachments-request", "expected_attachment_revision", -1),
        ("status-response", "lease_until", "2026-09-16T12:00:30+00:00"),
    ],
)
def test_admission_schemas_reject_ambiguous_or_expanded_values(
    name: str, field: str, value: Any
) -> None:
    document = copy.deepcopy(_load(EXAMPLES)[name])
    document[field] = value
    assert not _validator(name).is_valid(document)


def test_waiting_status_cannot_publish_an_active_fence_or_deadline() -> None:
    document = copy.deepcopy(_load(EXAMPLES)["status-response"])
    document.update(
        state="WAITING",
        activation_id=None,
        generation=None,
        claim_until=None,
        lease_until=None,
        waiting_reason="SERVER_CAPACITY",
        retry_after_seconds=5,
    )
    validator = _validator("status-response")
    validator.validate(document)
    for field, value in (
        ("activation_id", "10000000-0000-4000-8000-000000000003"),
        ("generation", 1),
        ("lease_until", "2026-09-16T12:00:30Z"),
    ):
        assert not validator.is_valid({**document, field: value})
