"""Language-neutral structure and cross-language golden identity vectors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts/face/v1"
FIXTURES = ROOT / "tests/fixtures/face/v1"


def test_schema_examples_and_canonical_vectors() -> None:
    schemas = [json.loads(path.read_text()) for path in SCHEMAS.glob("*.schema.json")]
    assert len(schemas) == 4
    registry: Registry[Any] = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    timeline = json.loads((FIXTURES / "timeline.json").read_text())
    projection = json.loads((FIXTURES / "projection.json").read_text())
    examples = {
        "timeline": timeline,
        "timeline-identity": timeline["identity"],
        "semantic-state": timeline["track_character"],
        "projection-binding": projection,
    }
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
        name = schema["$id"].rsplit("/", 1)[1].removesuffix(".schema.json")
        Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(
            examples[name]
        )
    canonical = rfc8785.dumps(timeline)
    assert canonical == (FIXTURES / "timeline.canonical.json").read_bytes()
    assert (
        hashlib.sha256(
            b"autplay.face.semantic-key.v1\0" + rfc8785.dumps(timeline["identity"])
        ).hexdigest()
        == projection["semantic_key"]
    )
    assert (
        hashlib.sha256(b"autplay.face.timeline-result.v1\0" + canonical).hexdigest()
        == projection["result_hash"]
    )


@pytest.mark.parametrize("abstained", [False, True])
def test_missing_and_estimated_zero_are_distinct(abstained: bool) -> None:
    schema = json.loads((SCHEMAS / "semantic-state.schema.json").read_text())
    axis = {
        "value": None if abstained else 0,
        "confidence": None if abstained else 0.5,
        "abstained": abstained,
        "reason_code": "UNCALIBRATED" if abstained else None,
    }
    validator = Draft202012Validator(schema)
    validator.validate({"axes": {"future_axis": axis}})
    validator.validate({"axes": {}})
    axis["abstained"] = not abstained
    assert list(validator.iter_errors({"axes": {"future_axis": axis}}))
