"""Strict current-account consent documents and private HTTP authority."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "11111111-1111-4111-8111-111111111111"


def test_consent_documents_reject_ambiguous_authority_and_policy() -> None:
    api = json.loads(
        (ROOT / "contracts/openapi/v1/autplay-training-consent.openapi.json").read_text()
    )
    validate(api)
    schemas = api["components"]["schemas"]
    command = {
        "operation_id": ACCOUNT,
        "account_id": ACCOUNT,
        "expected_revision": 0,
        "decision": "GRANTED",
        "policy_version": 1,
    }
    check = Draft202012Validator(schemas["decision-command"], format_checker=FormatChecker())
    check.validate(command)
    for change in (
        {"expected_revision": True},
        {"expected_revision": -1},
        {"expected_revision": 9007199254740991},
        {"decision": "UNKNOWN"},
        {"policy_version": 2},
        {"device_id": ACCOUNT},
        {"account_id": "not-a-uuid"},
    ):
        assert not check.is_valid({**command, **change})
    policy = {
        "schema_version": 1,
        "account_id": ACCOUNT,
        "decision": "UNKNOWN",
        "revision": 0,
        "policy_version": 1,
        "changed_at": None,
    }
    check = Draft202012Validator(schemas["policy"], format_checker=FormatChecker())
    check.validate(policy)
    assert not check.is_valid({**policy, "decision": "GRANTED"})
    assert not check.is_valid({**policy, "revision": 1})
    current = {
        **policy,
        "decision": "WITHDRAWN",
        "revision": 2,
        "changed_at": "2026-09-18T12:00:00Z",
    }
    check.validate(current)
    assert not check.is_valid({**current, "decision": "GRANTED", "revision": 9007199254740991})
    check.validate({**current, "revision": 9007199254740991})
    result = {
        **current,
        "operation_id": ACCOUNT,
        "applied_decision": "GRANTED",
        "applied_revision": 1,
    }
    Draft202012Validator(schemas["decision-response"], format_checker=FormatChecker()).validate(
        result
    )
    for operation in api["paths"]["/privacy/shared-training"].values():
        assert operation["security"] == [{"bearerAuth": []}]
        assert "parameters" not in operation
        for response in operation["responses"].values():
            assert response["headers"]["Cache-Control"]["schema"]["pattern"] == "no-store"
            assert response["headers"]["Vary"]["schema"]["const"] == "Authorization"
