"""Executable freeze for the PA1 invite-only account contract."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "public-access" / "v1"
FIXTURES = ROOT / "tests" / "fixtures" / "public-access" / "v1"
OPENAPI = ROOT / "contracts" / "openapi" / "v1" / "autplay-public-access.openapi.json"
CONTRACT = ROOT / "docs" / "design" / "AutPlay_Public_Invite_Only_Account_Contract_v1.md"
THREAT_MODEL = ROOT / "docs" / "design" / "AutPlay_Public_Invite_Only_Account_Threat_Model_v1.md"
ADR = ROOT / "docs" / "adr" / "ADR-045-pa1-invite-only-account-provisioning.md"
PROMPT = (
    ROOT / "docs" / "build-pack" / "prompts" / "PUBLIC_ACCESS_PA1_INVITE_ONLY_ACCOUNT_CONTRACT.md"
)

REQUIRED_SCHEMAS = {
    "account-invitation-create.schema.json",
    "account-invitation-document.schema.json",
    "account-invitation-page.schema.json",
    "account-invitation-view.schema.json",
    "account-lifecycle-command.schema.json",
    "account-lifecycle-result.schema.json",
    "account-registration-request.schema.json",
    "account-registration-response.schema.json",
    "error.schema.json",
    "invited-account-page.schema.json",
}
REQUIRED_OPERATIONS = {
    "createAccountInvitation",
    "listAccountInvitations",
    "cancelAccountInvitation",
    "redeemAccountInvitation",
    "listInvitedAccounts",
    "disableInvitedAccount",
}
REQUIRED_SCENARIOS = {
    "registration-first-success",
    "registration-exact-replay",
    "registration-changed-replay",
    "registration-regenerated-signature-exact-replay",
    "registration-authenticated-caller-rejected",
    "registration-concurrent-double-use",
    "registration-expired",
    "registration-cancelled",
    "registration-consumed-different-key",
    "registration-wrong-server",
    "registration-wrong-origin",
    "registration-stolen-bearer-first-use",
    "registration-role-escalation",
    "invitation-non-owner-issuance",
    "invitation-changed-create-operation-conflict",
    "invitation-changed-cancel-operation-conflict",
    "registration-account-cap-race",
    "registration-invitation-rate-limit",
    "registration-source-rate-limit",
    "registration-server-rate-limit",
    "invited-account-disable-race",
    "account-changed-disable-operation-conflict",
    "existing-account-enrollment-separation",
    "registration-secret-leakage-surfaces",
    "wan-rollout-prerequisite-failure",
}


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def schema_registry() -> Registry[Any]:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        schema = load(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def examples() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], load(FIXTURES / "schema-examples.json")["examples"])


def test_pa2_schema_set_and_examples_are_strict_implemented_contracts() -> None:
    assert {path.name for path in SCHEMAS.glob("*.schema.json")} == REQUIRED_SCHEMAS
    registry = schema_registry()
    for path in SCHEMAS.glob("*.schema.json"):
        schema = load(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://autplay.local/contracts/public-access/v1/{path.name}"
        assert schema["x-autplay-implementation-status"] == "IMPLEMENTED_PA2"
        Draft202012Validator.check_schema(schema)

    assert {item["schema"] for item in examples()} == REQUIRED_SCHEMAS
    for item in examples():
        validator = Draft202012Validator(
            load(SCHEMAS / item["schema"]),
            registry=registry,
            format_checker=FormatChecker(),
        )
        assert not list(validator.iter_errors(item["instance"])), item["schema"]


def test_invalid_shapes_reject_role_origin_secret_and_projection_expansion() -> None:
    registry = schema_registry()
    source_examples = examples()
    for case in load(FIXTURES / "invalid-cases.json")["cases"]:
        item = copy.deepcopy(source_examples[case["example_index"]])
        target = item["instance"]
        if "nested_item" in case:
            target = target["items"][case["nested_item"]]
        if case.get("delete"):
            del target[case["path"]]
        else:
            target[case["path"]] = case["value"]
        validator = Draft202012Validator(
            load(SCHEMAS / item["schema"]),
            registry=registry,
            format_checker=FormatChecker(),
        )
        assert list(validator.iter_errors(item["instance"])), case["case_id"]


def test_openapi_freezes_owner_only_and_body_only_public_surface() -> None:
    api = load(OPENAPI)
    validate(api, base_uri=OPENAPI.as_uri())
    assert api["x-autplay-implementation-status"] == "IMPLEMENTED_PA2"
    operations = {
        operation["operationId"]: operation
        for path in api["paths"].values()
        for operation in path.values()
    }
    assert set(operations) == REQUIRED_OPERATIONS
    for operation_id in {
        "createAccountInvitation",
        "cancelAccountInvitation",
        "disableInvitedAccount",
    }:
        operation = operations[operation_id]
        assert operation.get("security", api["security"]) == [{"bearerAuth": []}]
        assert "OWNER" in operation["x-autplay-authorization"]
        assert operation["x-autplay-idempotency"] == (
            "ACTOR_OPERATION_ACTION_TARGET_SERVER_RFC8785_SHA256_EXACT_OR_OPERATION_CONFLICT"
        )
        assert "409" in operation["responses"]

    for operation_id in {"listAccountInvitations", "listInvitedAccounts"}:
        operation = operations[operation_id]
        assert operation.get("security", api["security"]) == [{"bearerAuth": []}]
        assert "OWNER" in operation["x-autplay-authorization"]

    redeem = operations["redeemAccountInvitation"]
    assert redeem["security"] == []
    assert redeem["x-autplay-authenticated-caller"] == (
        "REJECT_VALID_AUTHORIZATION_OR_RECOGNIZED_AUTPLAY_SESSION"
    )
    assert redeem["x-autplay-proof-domain"] == "AutPlay account registration v1\n"
    assert redeem["x-autplay-secret-boundary"] == "BODY_ONLY_NO_URL_NO_COOKIE_NO_LOG"
    assert redeem["x-autplay-rate-limit"] == (
        "5/invitation/15m; 10/source-token/15m; 30/server/15m"
    )
    assert "AccountRegistrationRequest" in json.dumps(redeem["requestBody"])
    assert "AccountRegistrationResponse" in json.dumps(redeem["responses"])
    assert all("/register" not in path and "/login" not in path for path in api["paths"])


def test_role_and_projection_can_never_grant_cross_account_data_access() -> None:
    create = load(SCHEMAS / "account-invitation-create.schema.json")
    request = load(SCHEMAS / "account-registration-request.schema.json")
    response = load(SCHEMAS / "account-registration-response.schema.json")
    account_page = load(SCHEMAS / "invited-account-page.schema.json")
    assert "role" not in create["properties"]
    assert "role" not in request["properties"] and "account_role" not in request["properties"]
    assert response["properties"]["account_role"] == {"const": "USER"}
    assert account_page["x-autplay-projection"] == ("PROVISIONING_METADATA_ONLY_NO_ACCOUNT_DATA")
    forbidden = {"device_id", "session_id", "vault_object_id", "recording_id", "track_id"}
    account_text = json.dumps(account_page)
    assert not any(field in account_text for field in forbidden)


def test_registration_hash_vector_and_receipt_window_are_deterministic() -> None:
    vector = load(FIXTURES / "hash-vectors.json")["cases"][0]
    request = copy.deepcopy(examples()[vector["schema_example_index"]]["instance"])
    for field in vector["removed_fields"]:
        del request[field]
    digest = hashlib.sha256(rfc8785.dumps(request)).hexdigest()
    assert digest == vector["expected_request_sha256"]

    response = examples()[5]["instance"]
    absolute_expiry = datetime.fromisoformat(response["refresh_absolute_expires_at"])
    receipt_expiry = datetime.fromisoformat(response["receipt_expires_at"])
    assert absolute_expiry.tzinfo == UTC
    assert (receipt_expiry - absolute_expiry).total_seconds() == 300


def test_security_scenarios_cover_replay_abuse_isolation_and_rollout_stop() -> None:
    cases = load(FIXTURES / "scenario-vectors.json")["cases"]
    by_id = {case["case_id"]: case for case in cases}
    assert set(by_id) == REQUIRED_SCENARIOS
    required_shape = {
        "preconditions",
        "actor",
        "at",
        "operation",
        "transition",
        "expected_outcome",
    }
    assert all(required_shape <= set(case) for case in cases)
    assert by_id["registration-concurrent-double-use"]["durable_account_count"] == 1
    assert by_id["registration-account-cap-race"]["transition"]["active_accounts"] == 20
    assert by_id["registration-role-escalation"]["expected_outcome"] == "SCHEMA_REJECTED"
    assert by_id["invitation-non-owner-issuance"]["expected_outcome"] == "UNAUTHORIZED"
    assert by_id["registration-authenticated-caller-rejected"]["transition"] == {
        "account": None,
        "invitation": "ACTIVE",
    }
    replay = by_id["registration-regenerated-signature-exact-replay"]
    assert (
        replay["operation"]["fresh_valid_signature"] != replay["preconditions"]["prior_signature"]
    )
    for case_id in {
        "invitation-changed-create-operation-conflict",
        "invitation-changed-cancel-operation-conflict",
        "account-changed-disable-operation-conflict",
    }:
        assert by_id[case_id]["expected_outcome"] == "OPERATION_CONFLICT"
    assert by_id["wan-rollout-prerequisite-failure"]["transition"]["wan_enabled"] is False
    assert not any(by_id["registration-secret-leakage-surfaces"]["secret_exposure"].values())


def test_secret_fixtures_are_synthetic_https_only_and_never_urls() -> None:
    fixture_text = (FIXTURES / "schema-examples.json").read_text(encoding="utf-8")
    assert "http://" not in fixture_text
    assert "Bearer " not in fixture_text
    assert "?invitation" not in fixture_text and "#invitation" not in fixture_text
    document = examples()[1]["instance"]
    request = examples()[4]["instance"]
    for secret in (document["invitation_secret"], request["invitation_secret"]):
        assert secret.startswith("TEST_ONLY_")
        assert len(secret) == 43
    assert load(SCHEMAS / "account-invitation-document.schema.json")["x-autplay-sensitive"] is True
    assert load(SCHEMAS / "account-registration-request.schema.json")["x-autplay-sensitive"] is True


def test_contract_documents_are_accepted_but_runtime_remains_separately_gated() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in (CONTRACT, THREAT_MODEL, ADR, PROMPT)
    )
    for required in {
        "ACCEPTED",
        "DRAFT_NOT_IMPLEMENTED",
        "20 active accounts",
        "AutPlay account registration v1",
        "Admin Web",
        "WAN Wave",
        "PA2",
    }:
        assert required in combined
    assert "Status: Accepted by the user on 2026-09-01" in ADR.read_text(encoding="utf-8")


def test_artifact_manifest_matches_exact_contract_bytes() -> None:
    manifest = load(FIXTURES / "artifact-manifest.json")
    assert manifest["contract"] == "public-access-v1"
    assert manifest["implementation_status"] == "IMPLEMENTED_PA2"
    paths = [item["path"] for item in manifest["artifacts"]]
    assert paths == sorted(paths)
    for item in manifest["artifacts"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
