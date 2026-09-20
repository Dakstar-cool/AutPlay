"""Shared Android/server recovery messages and the public HTTP contract."""

import hashlib
import json
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]


def test_recovery_messages_match_openapi_and_canonical_hashes() -> None:
    api = json.loads(
        (ROOT / "contracts/openapi/v1/autplay-account-recovery.openapi.json").read_text(
            encoding="utf-8"
        )
    )
    vectors = json.loads(
        (ROOT / "tests/fixtures/account-recovery/v1/proof-vectors.json").read_text(encoding="utf-8")
    )
    validate(api)
    for kind, request in vectors["requests"].items():
        schema = api["components"]["schemas"][f"{kind}-request"]
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(request)
        assert not validator.is_valid({**request, "raw_code": vectors["code"]})
        unsigned = {
            key: value
            for key, value in request.items()
            if key not in {"request_sha256", "device_signature_b64url"}
        }
        assert hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest() == request["request_sha256"]
    status = api["components"]["schemas"]["status-response"]
    assert "account_label" in status["required"]


def test_recovery_outcome_reuses_exact_signed_request_with_refresh_proof() -> None:
    api = json.loads(
        (ROOT / "contracts/openapi/v1/autplay-account-recovery.openapi.json").read_text(
            encoding="utf-8"
        )
    )
    commit = api["paths"]["/recovery/commit"]["post"]
    outcome = api["paths"]["/recovery/outcome"]["post"]
    assert outcome["security"] == [{"recoveryRefresh": []}]
    assert outcome["requestBody"] == commit["requestBody"]
    assert outcome["x-autplay-proof-domain"] == commit["x-autplay-proof-domain"]
    assert outcome["responses"]["200"]["content"] == commit["responses"]["200"]["content"]
    assert api["components"]["securitySchemes"]["recoveryRefresh"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-AutPlay-Recovery-Refresh",
        "description": (
            "Exact client-created refresh secret bound by the signed original request; "
            "used only to recover an already committed lost reply."
        ),
    }
