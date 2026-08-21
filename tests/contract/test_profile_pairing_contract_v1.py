"""Device-independent validation for the proposed M5A profile/pairing contract."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "profile-pairing" / "v1"
OPENAPI = ROOT / "contracts" / "openapi" / "v1" / "autplay-profile-pairing.openapi.json"
FIXTURES = ROOT / "tests" / "fixtures" / "profile-pairing" / "v1"
CONTRACT = ROOT / "docs" / "design" / "AutPlay_Profile_Pairing_Security_Contract_v1.md"
ADR_029 = ROOT / "docs" / "adr" / "ADR-029-m5-server-identity-discovery-enrollment-capabilities.md"
ADR_030 = ROOT / "docs" / "adr" / "ADR-030-m5-device-session-and-local-binding-lifecycle.md"

REQUIRED_SCHEMAS = {
    "capabilities.schema.json",
    "create-invitation-request.schema.json",
    "device-list.schema.json",
    "discovery-metadata.schema.json",
    "enrollment-exchange-request.schema.json",
    "enrollment-exchange-response.schema.json",
    "enrollment-invitation.schema.json",
    "error.schema.json",
    "lifecycle-command.schema.json",
    "lifecycle-result.schema.json",
    "session-rotation-request.schema.json",
    "session-rotation-response.schema.json",
    "session-list.schema.json",
}
REQUIRED_OPERATION_IDS = {
    "cancelEnrollmentInvitation",
    "createEnrollmentInvitation",
    "exchangeEnrollmentInvitation",
    "getCapabilities",
    "getDiscoveryMetadata",
    "listDevices",
    "listSessions",
    "logoutAllSessions",
    "logoutCurrentSession",
    "revokeDevice",
    "rotateDeviceSession",
}
REQUIRED_SECURITY_CASE_IDS = {
    "valid-invitation-exchange",
    "lost-response-exact-replay",
    "expired-invitation",
    "cancelled-invitation",
    "used-invitation-different-replay",
    "wrong-server-invitation",
    "wrong-account-invitation",
    "wrong-device-invitation",
    "concurrent-double-exchange",
    "origin-substitution",
    "identity-key-change-v1-blocked",
    "certificate-renewal-same-identity",
    "superseded-generation-result",
    "capability-downgrade",
    "capability-unknown-additive-field",
    "capability-incompatible-major",
    "revoked-device-protected-operation",
    "rotated-session-protected-operation",
    "rotation-lost-response-exact-replay",
    "rotation-changed-replay",
    "exchange-replay-before-receipt-expiry",
    "exchange-replay-at-receipt-expiry",
    "rotation-replay-at-receipt-expiry",
    "exchange-replay-after-device-revoke",
    "rotation-replay-after-logout-all",
    "exchange-replay-after-account-disable",
    "secret-http-response-no-store",
    "list-devices-cross-account",
    "revoke-device-cross-account",
    "cancel-foreign-invitation",
    "logout-all-own-account",
    "duplicate-current-logout",
    "duplicate-device-revoke",
    "logout-offline",
    "revoke-timeout",
    "materialization-consent-cancel",
    "materialization-retry-new-generation",
    "materialization-binding-change",
    "redact-secret-and-private-data",
}


def load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        load_json(SCHEMAS / schema_name),
        format_checker=FormatChecker(),
    )


def error_tree(errors: list[Any]) -> list[Any]:
    flattened: list[Any] = []
    pending = list(errors)
    while pending:
        error = pending.pop()
        flattened.append(error)
        pending.extend(error.context)
    return flattened


def has_keyword(errors: list[Any], expected: str) -> bool:
    return any(error.validator == expected for error in error_tree(errors))


def set_path(instance: dict[str, Any], path: str, value: Any, *, delete: bool = False) -> None:
    parts = path.split(".")
    current: Any = instance
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    final = parts[-1]
    if delete:
        del current[final]
    elif isinstance(current, list):
        current[int(final)] = value
    else:
        current[final] = value


def example_by_schema(schema_name: str) -> dict[str, Any]:
    examples = load_json(FIXTURES / "schema-examples.json")["examples"]
    return cast(
        dict[str, Any],
        copy.deepcopy(next(item["instance"] for item in examples if item["schema"] == schema_name)),
    )


def operation_ids(document: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                result.add(operation["operationId"])
    return result


def test_every_schema_is_draft_2020_12_valid_and_marked_not_implemented() -> None:
    paths = list(SCHEMAS.glob("*.schema.json"))
    assert {path.name for path in paths} == REQUIRED_SCHEMAS
    for path in paths:
        schema = load_json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://autplay.local/contracts/profile-pairing/v1/{path.name}"
        assert schema["x-autplay-implementation-status"] == "DRAFT_NOT_IMPLEMENTED"
        Draft202012Validator.check_schema(schema)


def test_openapi_is_valid_complete_draft_and_separates_public_operations() -> None:
    document = load_json(OPENAPI)
    validate(document, base_uri=OPENAPI.as_uri())
    assert document["openapi"].startswith("3.1.")
    assert document["info"]["version"] == "1.0.0-draft"
    assert document["x-autplay-implementation-status"] == "DRAFT_NOT_IMPLEMENTED"
    assert operation_ids(document) == REQUIRED_OPERATION_IDS
    assert document["paths"]["/pairing/discovery"]["get"]["security"] == []
    assert document["paths"]["/pairing/enrollment/exchanges"]["post"]["security"] == []
    assert document["paths"]["/account/sessions/rotate"]["post"]["security"] == []
    legacy_p03_paths = {
        "/auth/refresh",
        "/auth/logout",
        "/auth/logout-all",
        "/devices/{device_id}/revoke",
    }
    assert not legacy_p03_paths & set(document["paths"])
    for path, method in (
        ("/profile/capabilities", "get"),
        ("/account/devices", "get"),
        ("/account/sessions", "get"),
    ):
        assert "security" not in document["paths"][path][method]


def test_openapi_freezes_per_operation_authorization_without_target_user_parameter() -> None:
    document = load_json(OPENAPI)
    expected = {
        "getDiscoveryMetadata": "PUBLIC_NO_AUTHORITY",
        "getCapabilities": "ACTIVE_SESSION_SELF_ACCOUNT",
        "createEnrollmentInvitation": "ACTIVE_OWNER_OR_ADMIN_SELF_ACCOUNT",
        "cancelEnrollmentInvitation": "ISSUER_OR_OWNER_SELF_ACCOUNT",
        "exchangeEnrollmentInvitation": "INVITATION_BEARER_FIRST_SUCCESS_BINDS_DEVICE_POP",
        "listDevices": "ACTIVE_SESSION_SELF_ACCOUNT",
        "listSessions": "ACTIVE_SESSION_SELF_ACCOUNT",
        "rotateDeviceSession": "MATCHING_REFRESH_AND_DEVICE_POP",
        "logoutCurrentSession": "ACTIVE_SESSION_EXACT_SELF",
        "logoutAllSessions": "ACTIVE_SESSION_SELF_ACCOUNT",
        "revokeDevice": "ACTIVE_SESSION_SELF_ACCOUNT_DEVICE",
    }
    actual: dict[str, str] = {}
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            actual[operation["operationId"]] = operation["x-autplay-authorization"]
            parameter_names = {
                parameter.get("name")
                for parameter in operation.get("parameters", [])
                if isinstance(parameter, dict)
            }
            assert "user_id" not in parameter_names
    assert actual == expected


def test_language_neutral_examples_validate_and_openapi_required_fields_match() -> None:
    examples = load_json(FIXTURES / "schema-examples.json")["examples"]
    assert {example["schema"] for example in examples} == REQUIRED_SCHEMAS
    document = load_json(OPENAPI)
    components = document["components"]["schemas"]
    for example in examples:
        errors = list(validator(example["schema"]).iter_errors(example["instance"]))
        assert not errors, (example["schema"], errors)
    component_map = {
        "capabilities.schema.json": "Capabilities",
        "create-invitation-request.schema.json": "CreateInvitationRequest",
        "device-list.schema.json": "DeviceList",
        "discovery-metadata.schema.json": "DiscoveryMetadata",
        "enrollment-exchange-request.schema.json": "EnrollmentExchangeRequest",
        "enrollment-exchange-response.schema.json": "EnrollmentExchangeResponse",
        "enrollment-invitation.schema.json": "EnrollmentInvitation",
        "error.schema.json": "ProfilePairingError",
        "lifecycle-command.schema.json": "LifecycleCommand",
        "lifecycle-result.schema.json": "LifecycleResult",
        "session-rotation-request.schema.json": "SessionRotationRequest",
        "session-rotation-response.schema.json": "SessionRotationResponse",
        "session-list.schema.json": "SessionList",
    }
    for schema_name, component_name in component_map.items():
        component = components[component_name]
        assert set(component) == {"$ref"}
        linked_schema = (OPENAPI.parent / component["$ref"]).resolve()
        assert linked_schema == (SCHEMAS / schema_name).resolve()
        assert linked_schema.is_file()


def test_secret_http_responses_require_no_store_headers() -> None:
    document = load_json(OPENAPI)
    secret_responses = (
        document["paths"]["/pairing/enrollment/invitations"]["post"]["responses"]["201"],
        document["paths"]["/pairing/enrollment/exchanges"]["post"]["responses"]["200"],
        document["paths"]["/pairing/enrollment/exchanges"]["post"]["responses"]["201"],
        document["paths"]["/account/sessions/rotate"]["post"]["responses"]["200"],
        document["components"]["responses"]["ErrorResponse"],
    )
    for response in secret_responses:
        assert response["headers"] == {
            "Cache-Control": {"$ref": "#/components/headers/CacheControlNoStore"},
            "Pragma": {"$ref": "#/components/headers/PragmaNoCache"},
        }
    assert document["components"]["headers"]["CacheControlNoStore"]["schema"]["const"] == (
        "no-store"
    )
    assert document["components"]["headers"]["PragmaNoCache"]["schema"]["const"] == "no-cache"


def test_rfc8785_sha256_vectors_are_reproducible() -> None:
    examples = load_json(FIXTURES / "schema-examples.json")["examples"]
    vectors = load_json(FIXTURES / "hash-vectors.json")["cases"]
    assert {case["case_id"] for case in vectors} == {
        "discovery-payload",
        "capabilities-payload",
        "exchange-request",
        "rotation-request",
    }
    for vector in vectors:
        instance = copy.deepcopy(examples[vector["schema_example_index"]]["instance"])
        if vector["case_id"] in {"exchange-request", "rotation-request"}:
            instance.pop("request_sha256")
            instance.pop("device_signature_b64url")
        else:
            instance = instance["payload"]
        actual = hashlib.sha256(rfc8785.dumps(instance)).hexdigest()
        assert actual == vector["expected_sha256"], vector["case_id"]
    assert examples[0]["instance"]["payload_sha256"] == vectors[0]["expected_sha256"]
    assert examples[1]["instance"]["payload_sha256"] == vectors[1]["expected_sha256"]
    assert examples[3]["instance"]["request_sha256"] == vectors[2]["expected_sha256"]
    assert examples[10]["instance"]["request_sha256"] == vectors[3]["expected_sha256"]


def test_artifact_manifest_paths_and_sha256_are_current() -> None:
    manifest = load_json(FIXTURES / "artifact-manifest.json")
    artifacts = manifest["artifacts"]
    assert manifest["algorithm"] == "SHA-256"
    assert len(artifacts) == 22
    assert len({artifact["path"] for artifact in artifacts}) == len(artifacts)
    for artifact in artifacts:
        path = ROOT / artifact["path"]
        assert path.is_file(), artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"], artifact["path"]


def test_invalid_vectors_are_rejected_by_named_schema_or_semantic_rule() -> None:
    for case in load_json(FIXTURES / "invalid-cases.json")["cases"]:
        instance = example_by_schema(case["schema"])
        mutation = case["mutation"]
        if mutation.get("value_factory") == "65_unique_operations":
            value: Any = [f"test.operation.{index}" for index in range(65)]
        elif mutation.get("value_factory") == "101_devices":
            value = [copy.deepcopy(instance["devices"][0]) for _ in range(101)]
        else:
            value = mutation.get("value")
        set_path(instance, mutation["path"], value, delete=mutation.get("delete", False))
        if case["expected_error"] == "semantic_origin_credentials_forbidden":
            assert urlsplit(instance["payload"]["api_origin"]).username is not None
            continue
        errors = list(validator(case["schema"]).iter_errors(instance))
        assert errors, case["case_id"]
        assert has_keyword(errors, case["expected_error"]), case["case_id"]


def test_security_vectors_cover_required_fail_closed_outcomes() -> None:
    cases = load_json(FIXTURES / "security-vectors.json")["cases"]
    assert {case["case_id"] for case in cases} == REQUIRED_SECURITY_CASE_IDS
    assert all(case.get("expected_outcome") for case in cases)
    by_id = {case["case_id"]: case for case in cases}
    assert by_id["lost-response-exact-replay"]["durable_create_count"] == 1
    assert by_id["concurrent-double-exchange"]["durable_create_count"] == 1
    assert by_id["rotation-lost-response-exact-replay"]["durable_create_count"] == 1
    assert by_id["wrong-device-invitation"]["input"]["invitation_state"] == "USED"
    assert by_id["exchange-replay-before-receipt-expiry"]["durable_create_count"] == 1
    assert by_id["exchange-replay-at-receipt-expiry"]["durable_create_count"] == 1
    assert by_id["rotation-replay-at-receipt-expiry"]["durable_create_count"] == 1
    for case_id in {
        "exchange-replay-after-device-revoke",
        "rotation-replay-after-logout-all",
        "exchange-replay-after-account-disable",
    }:
        assert by_id[case_id]["expected_outcome"] == "REJECTED_NO_BEARER_NO_SIDE_EFFECT"
        assert by_id[case_id]["durable_create_count"] == 1
    for case_id in {
        "materialization-consent-cancel",
        "materialization-retry-new-generation",
        "materialization-binding-change",
    }:
        assert by_id[case_id]["existing_event_rewrite_count"] == 0
    for case_id in {"logout-offline", "revoke-timeout"}:
        assert by_id[case_id]["expected_error_code"] == "remote_outcome_unknown"

    for schema_name in {
        "enrollment-exchange-response.schema.json",
        "session-rotation-response.schema.json",
    }:
        instance = example_by_schema(schema_name)
        absolute_expiry = datetime.fromisoformat(instance["refresh_absolute_expires_at"])
        receipt_expiry = datetime.fromisoformat(instance["receipt_expires_at"])
        assert absolute_expiry.tzinfo == UTC
        assert (receipt_expiry - absolute_expiry).total_seconds() == 300


def test_secret_documents_are_explicit_and_only_use_synthetic_values() -> None:
    for schema_name in {
        "enrollment-invitation.schema.json",
        "enrollment-exchange-request.schema.json",
        "enrollment-exchange-response.schema.json",
        "session-rotation-request.schema.json",
        "session-rotation-response.schema.json",
    }:
        assert load_json(SCHEMAS / schema_name)["x-autplay-sensitive"] is True
    examples_text = (FIXTURES / "schema-examples.json").read_text(encoding="utf-8")
    assert "http://" not in examples_text
    for forbidden in ("Bearer ", "-----BEGIN PRIVATE KEY-----", "C:\\\\Users\\"):
        assert forbidden not in examples_text
    for key in ("invitation_secret", "current_refresh_token", "access_token"):
        value = next(
            example["instance"][key]
            for example in load_json(FIXTURES / "schema-examples.json")["examples"]
            if key in example["instance"]
        )
        assert value.startswith("TEST_ONLY_")


def test_contract_and_adrs_freeze_required_boundaries_and_deferrals() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in (CONTRACT, ADR_029, ADR_030))
    for required in {
        "DRAFT_NOT_IMPLEMENTED",
        "server_profile_id",
        "RFC 8785",
        "P-256",
        "remote_outcome_unknown",
        "Review and connect this library to <account>",
        "Production domain",
        "M5B",
    }:
        assert required in text
    assert "no m5a room migration" in text.lower()
