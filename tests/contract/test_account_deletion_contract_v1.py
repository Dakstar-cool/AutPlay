"""Purpose-bound deletion schemas and canonical cross-client vectors."""

import hashlib
import json
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]


def test_deletion_contract_and_shared_request_hashes() -> None:
    api = json.loads(
        (ROOT / "contracts/openapi/v1/autplay-account-deletion.openapi.json").read_text()
    )
    vectors = json.loads(
        (ROOT / "tests/fixtures/account-deletion/v1/proof-vectors.json").read_text()
    )
    validate(api)
    for kind, request in vectors["requests"].items():
        schema = api["components"]["schemas"][kind + "-request"]
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(request)
        assert not validator.is_valid({**request, "raw_code": "NEVER_IN_BODY"})
        unsigned = {
            key: value
            for key, value in request.items()
            if key not in {"request_sha256", "device_signature_b64url"}
        }
        assert hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest() == request["request_sha256"]


def test_negative_resolution_has_its_own_strict_schema_and_original_proof_transport() -> None:
    api = json.loads(
        (ROOT / "contracts/openapi/v1/autplay-account-deletion.openapi.json").read_text()
    )
    resolve = api["paths"]["/deletion/request-resolve"]["post"]
    receipt = api["paths"]["/deletion/request-receipt"]["post"]
    assert resolve["security"] == receipt["security"]
    assert resolve["requestBody"] == receipt["requestBody"]
    schema = api["components"]["schemas"]["not-accepted-response"]
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    reply = {
        "contract_version": "v1",
        "schema_version": 1,
        "account_id": "10000000-0000-4000-8000-000000000003",
        "deletion_request_id": "10000000-0000-4000-8000-000000000009",
        "request_sha256": "a" * 64,
        "state": "NOT_ACCEPTED",
        "resolved_at": "2026-09-19T12:00:00Z",
        "replayed": True,
    }
    validator.validate(reply)
    for wrong in (
        {**reply, "state": "CANCELLED"},
        {**reply, "replayed": "true"},
        {**reply, "request_sha256": "unknown"},
        {**reply, "raw_code": "private"},
    ):
        assert not validator.is_valid(wrong)


def test_cancel_outcome_reuses_exact_signed_request_with_refresh_proof() -> None:
    api = json.loads(
        (ROOT / "contracts/openapi/v1/autplay-account-deletion.openapi.json").read_text()
    )
    commit = api["paths"]["/deletion/cancel/commit"]["post"]
    outcome = api["paths"]["/deletion/cancel/outcome"]["post"]
    assert outcome["security"] == [{"recoveryRefresh": []}]
    assert outcome["requestBody"] == commit["requestBody"]
    assert outcome["responses"]["200"]["content"] == commit["responses"]["200"]["content"]
    assert api["components"]["securitySchemes"]["recoveryRefresh"]["name"] == (
        "X-AutPlay-Recovery-Refresh"
    )
