"""The checked-in signed vector must remain valid under the disabled wire schema."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_DIR = _ROOT / "contracts/face/v2"


def _schema(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8")))


def _envelope_validator() -> Draft202012Validator:
    identity = _schema("timeline-identity.schema.json")
    registry = Registry().with_resource(identity["$id"], Resource.from_contents(identity))
    return Draft202012Validator(_schema("projection-envelope.schema.json"), registry=registry)


def _signed_envelope() -> dict[str, Any]:
    fixture = json.loads(
        (_ROOT / "tests/fixtures/face/v2-projection-lease.json").read_text(encoding="utf-8")
    )
    return cast(dict[str, Any], json.loads(fixture["envelope_json"]))


def test_signed_projection_vector_satisfies_wire_schema() -> None:
    _envelope_validator().validate(_signed_envelope())


def test_projection_schema_rejects_missing_role_and_unbounded_generation() -> None:
    validator = _envelope_validator()
    for edit in ("missing_role", "unbounded_generation", "unexpected_field"):
        envelope = deepcopy(_signed_envelope())
        if edit == "missing_role":
            del envelope["required_role_cardinality"]["INTERPRETER_EXPORT"]
        elif edit == "unbounded_generation":
            envelope["activation_epoch"] = 9_007_199_254_740_992
        else:
            envelope["raw_embedding"] = "not-on-wire"
        with pytest.raises(ValidationError):
            validator.validate(envelope)


def test_projection_tombstone_has_no_timeline_payload() -> None:
    validator = Draft202012Validator(_schema("projection-tombstone.schema.json"))
    document = {
        "schema_version": 2,
        "projection_id": "80000000-0000-4000-8000-000000000008",
        "server_profile_id": "50000000-0000-4000-8000-000000000005",
        "user_id": "60000000-0000-4000-8000-000000000006",
        "superseding_generation": 6,
        "reason_code": "SOURCE_REVOKED",
        "tombstoned_at_ms": 1_700_000_001_000,
    }
    validator.validate(document)
    with pytest.raises(ValidationError):
        validator.validate(
            {**document, "timeline_identity": _signed_envelope()["timeline_identity"]}
        )
