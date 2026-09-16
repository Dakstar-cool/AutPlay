"""Language-neutral wire and canonical-hash freeze for two-phone pairing."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts/self-device-pairing/v1"
FIXTURES = ROOT / "tests/fixtures/self-device-pairing/v1"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_schemas_examples_and_openapi_agree() -> None:
    examples = load(FIXTURES / "schema-examples.json")
    assert {p.stem.removesuffix(".schema") for p in SCHEMAS.glob("*.json")} == set(examples)
    for name, value in examples.items():
        schema = load(SCHEMAS / f"{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(value)
        assert not validator.is_valid({**value, "role": "OWNER"})
        for required in schema["required"]:
            changed = {key: item for key, item in value.items() if key != required}
            assert not validator.is_valid(changed), (name, required)
    api = load(ROOT / "contracts/openapi/v1/autplay-self-device-pairing.openapi.json")
    validate(api)
    operations = [op for path in api["paths"].values() for op in path.values()]
    assert len(operations) == 6
    for op in operations:
        name = op["operationId"].removeprefix("selfPairing").lower()
        if name in {"claim", "poll", "exchange"}:
            assert op["security"] == [{"pairingSecret": []}]
            assert op["x-autplay-proof-domain"] == f"autplay:self-device-pairing:{name}:v1\n"
        else:
            assert op["security"] == [{"bearerAuth": []}]
        if name != "status":
            assert op["x-autplay-max-request-bytes"] == 8192


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("expected_identity_epoch", 0),
        ("ceremony_id", "10000000-0000-4000-8000-00000000000A"),
        ("request_sha256", "A" * 64),
        ("device_name", "Phone\nOwner"),
        ("device_name", "x" * 121),
        ("requested_at", "2026-09-16 12:00:00Z"),
        ("requested_at", "2026-09-16T12:00:00+00:00"),
        ("platform", "WEB"),
        ("device_signature_b64url", "A" * 85 + "B"),
    ],
)
def test_claim_rejects_noncanonical_or_expanded_fields(field: str, value: Any) -> None:
    claim = copy.deepcopy(load(FIXTURES / "schema-examples.json")["claim-request"])
    claim[field] = value
    validator = Draft202012Validator(
        load(SCHEMAS / "claim-request.schema.json"), format_checker=FormatChecker()
    )
    assert not validator.is_valid(claim)


def test_shared_canonical_hashes_and_unsigned_sas_value() -> None:
    fixture = load(FIXTURES / "proof-vectors.json")
    for vector in fixture["requests"].values():
        document = vector["request"]
        unsigned = {
            key: value
            for key, value in document.items()
            if key not in {"request_sha256", "device_signature_b64url"}
        }
        canonical = rfc8785.dumps(unsigned)
        assert canonical.decode() == vector["canonical_unsigned_utf8"]
        assert hashlib.sha256(canonical).hexdigest() == document["request_sha256"]
    sas = fixture["sas"]
    hashed = hashlib.sha256(
        b"autplay:self-device-pairing:sas:v1\n"
        + hashlib.sha256(rfc8785.dumps(sas["payload"])).digest()
    ).digest()
    assert hashed.hex() == sas["digest_sha256"]
    assert f"{int.from_bytes(hashed[:8], 'big') % 10**12:012d}" == sas["comparison_code"]
