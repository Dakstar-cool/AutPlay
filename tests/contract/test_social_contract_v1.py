"""Executable S1B/S1C social contract freeze."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "social" / "v1"
FIXTURES = ROOT / "tests" / "fixtures" / "social" / "v1"
OPENAPI = ROOT / "contracts" / "openapi" / "v1" / "autplay-social.openapi.json"
EVENT = ROOT / "contracts" / "events" / "v1" / "social-envelope.schema.json"
REQUIRED_ACCEPTED_DOCS = (
    ROOT / "docs" / "design" / "AutPlay_Identity_Trust_Social_Contract_v1.md",
    ROOT / "docs" / "design" / "AutPlay_Identity_Trust_Social_Threat_Model_v1.md",
    ROOT / "docs" / "adr" / "ADR-035-s1a-web-device-admission-and-exact-key-trust.md",
    ROOT / "docs" / "adr" / "ADR-036-s1a-friends-presence-and-wave-invitations.md",
    ROOT / "docs" / "adr" / "ADR-037-s1a-android-guest-capability-and-media-boundary.md",
    ROOT / "docs" / "adr" / "ADR-038-s1b-device-admission-protocol-completion.md",
    ROOT / "docs" / "adr" / "ADR-039-s1c-social-runtime-contract-completion.md",
)

S1B_SCHEMAS = {
    "admission-created.schema.json",
    "admission-decision.schema.json",
    "admission-exchange.schema.json",
    "admission-poll.schema.json",
    "admission-recovery.schema.json",
    "admission-request.schema.json",
    "admission-status.schema.json",
    "review-binding.schema.json",
    "review-locator-resolution.schema.json",
    "trusted-key-command.schema.json",
    "trusted-reenrollment-challenge-request.schema.json",
    "trusted-reenrollment-challenge.schema.json",
    "trusted-reenrollment.schema.json",
}
S1C_SCHEMAS = {
    "contact-card.schema.json",
    "friend-room-invitation-create.schema.json",
    "friendship-command.schema.json",
    "friendship-receipt.schema.json",
    "operation-command.schema.json",
    "presence-aggregate.schema.json",
    "presence-heartbeat.schema.json",
    "presence-page.schema.json",
    "presence-settings.schema.json",
    "presence-settings-view.schema.json",
    "room-invitation-accept.schema.json",
    "room-invitation-acceptance.schema.json",
    "room-invitation.schema.json",
    "social-snapshot.schema.json",
}
REQUIRED_OPERATIONS = {
    "submitAdmissionRequest",
    "pollAdmissionRequest",
    "recoverAdmissionCreationSecrets",
    "exchangeApprovedAdmission",
    "createTrustedKeyReenrollmentChallenge",
    "exchangeTrustedKeyReenrollment",
    "resolveAdmissionReviewLocator",
    "decideAdmissionRequest",
    "commandTrustedKey",
    "getOwnContactCard",
    "getSocialSnapshot",
    "commandFriendship",
    "getPresenceSettings",
    "setPresenceSettings",
    "heartbeatPresence",
    "getFriendPresence",
    "getFriendPresencePage",
    "createRoomInvitation",
    "cancelFriendRoomInvitation",
    "acceptFriendRoomInvitation",
}
REQUIRED_SCENARIOS = {
    "admission-exact-replay",
    "admission-lost-202-secret-rotation",
    "admission-exact-duplicate-recovery-required",
    "admission-approved-exchange-exact-replay",
    "trusted-reenrollment-exact-key-only",
    "admission-changed-body-conflict",
    "admission-server-or-identity-substitution",
    "admission-cross-owner",
    "admission-concurrent-approve",
    "admission-reject-block-race",
    "admission-expiry-t-minus-1",
    "admission-expiry-at-t",
    "admission-expiry-t-plus-1",
    "admission-poll-url-history-referrer-proxy-intent-log-screenshot-export",
    "new-key-no-trust-inheritance",
    "remove-trust-does-not-revoke-session",
    "block-key-does-not-revoke-session",
    "revoke-and-remove-explicit-combined",
    "friend-reverse-request-no-autoaccept",
    "friend-block-wins-accept-race",
    "friend-cross-owner",
    "block-active-room",
    "block-after-ordinary-p13-leave",
    "friend-remove-preserves-room-membership",
    "account-disabled-social-action",
    "account-delete-invalidates-social-state",
    "presence-default-private",
    "presence-multi-device-aggregate",
    "presence-expiry-90-seconds",
    "friend-invite-duplicate-exact",
    "friend-invite-concurrent-accept",
    "friend-invite-expired-or-cancelled",
    "friend-invite-policy-capacity-race",
    "guest-single-use-concurrent-redemption",
    "guest-forbidden-account-library-vault-action",
    "guest-independent-media-only",
    "guest-secret-leakage-surfaces",
}


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_s1b_and_s1c_schema_statuses_and_examples_are_strict() -> None:
    names = {path.name for path in SCHEMAS.glob("*.schema.json")}
    assert names == S1B_SCHEMAS | S1C_SCHEMAS
    for path in SCHEMAS.glob("*.schema.json"):
        schema = load(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://autplay.local/contracts/social/v1/{path.name}"
        assert schema["x-autplay-implementation-status"] == (
            "IMPLEMENTED_S1B" if path.name in S1B_SCHEMAS else "IMPLEMENTED_S1C"
        )
        Draft202012Validator.check_schema(schema)

    examples = load(FIXTURES / "schema-examples.json")["examples"]
    assert {item["schema"] for item in examples} == S1B_SCHEMAS | S1C_SCHEMAS
    for item in examples:
        validator = Draft202012Validator(
            load(SCHEMAS / item["schema"]), format_checker=FormatChecker()
        )
        assert not list(validator.iter_errors(item["instance"])), item["schema"]


def test_invalid_public_shapes_fail() -> None:
    examples = load(FIXTURES / "schema-examples.json")["examples"]
    for case in load(FIXTURES / "invalid-cases.json")["cases"]:
        item = copy.deepcopy(examples[case["example_index"]])
        if case.get("delete"):
            del item["instance"][case["path"]]
        else:
            item["instance"][case["path"]] = case["value"]
        validator = Draft202012Validator(
            load(SCHEMAS / item["schema"]), format_checker=FormatChecker()
        )
        assert list(validator.iter_errors(item["instance"])), case["case_id"]


def test_openapi_freezes_s1c_recovery_and_authority_surface() -> None:
    api = load(OPENAPI)
    validate(api, base_uri=OPENAPI.as_uri())
    assert api["x-autplay-implementation-status"] == "IMPLEMENTED_S1C"
    assert api["security"] == [{"bearerAuth": []}]
    operations = {
        operation["operationId"]: operation
        for path in api["paths"].values()
        for operation in path.values()
    }
    assert set(operations) == REQUIRED_OPERATIONS

    created = operations["submitAdmissionRequest"]["responses"]["202"]
    assert operations["submitAdmissionRequest"]["security"] == []
    assert "AdmissionCreated" in json.dumps(created)
    assert created["headers"]["Cache-Control"]["$ref"].endswith("NoStore")
    poll = operations["pollAdmissionRequest"]
    assert poll["security"] == [{"admissionPollBearer": []}]
    assert "AdmissionPoll" in json.dumps(poll["requestBody"])
    poll_scheme = api["components"]["securitySchemes"]["admissionPollBearer"]
    assert poll_scheme["in"] == "header"
    assert poll_scheme["name"] == "X-AutPlay-Admission-Poll"
    recovery = operations["recoverAdmissionCreationSecrets"]
    assert recovery["security"] == []
    assert recovery["x-autplay-authorization"] == (
        "EXACT_PENDING_REQUEST_HASH_SERVER_BINDING_AND_DEVICE_KEY_POP"
    )
    assert "AdmissionRecovery" in json.dumps(recovery["requestBody"])
    assert recovery["responses"]["200"]["headers"]["Cache-Control"]["$ref"].endswith("NoStore")
    exchange = operations["exchangeApprovedAdmission"]
    assert exchange["security"] == [{"admissionPollBearer": []}]
    assert exchange["x-autplay-proof-domain"] == "autplay:s1b:admission-exchange:v1\\n"
    for operation_id in (
        "createTrustedKeyReenrollmentChallenge",
        "exchangeTrustedKeyReenrollment",
    ):
        assert operations[operation_id]["security"] == []
        assert operations[operation_id]["x-autplay-proof-domain"] == (
            "autplay:s1b:trusted-reenrollment:v1\\n"
        )
    for operation_id in (
        "resolveAdmissionReviewLocator",
        "decideAdmissionRequest",
        "commandTrustedKey",
    ):
        operation = operations[operation_id]
        assert operation["security"] == [{"webSessionCookie": []}]
        assert operation["x-autplay-authorization"] == (
            "M6_WEB_OWNER_OR_ADMIN_EXACT_SELF_ACCOUNT_ONLY"
        )
        assert operation["x-autplay-m6-mutation-boundary"] == (
            "EXACT_ORIGIN_CSRF_SYNCHRONIZER_AND_OPERATION_ID_REQUIRED"
        )
    web_scheme = api["components"]["securitySchemes"]["webSessionCookie"]
    assert "HttpOnly" in web_scheme["description"]
    assert web_scheme["name"] == "__Host-autplay_admin"

    assert operations["getOwnContactCard"]["x-autplay-rate-limit"] == "60/account/15m"
    assert operations["getSocialSnapshot"]["x-autplay-rate-limit"] == "60/account/15m"
    assert operations["commandFriendship"]["x-autplay-rate-limit"] == "30/account/15m; 10/pair/15m"
    assert operations["setPresenceSettings"]["x-autplay-rate-limit"] == "10/account/15m"
    assert operations["heartbeatPresence"]["x-autplay-rate-limit"] == "1/30s/device"
    assert operations["getFriendPresencePage"]["x-autplay-rate-limit"] == "120/account/15m"
    assert (
        operations["createRoomInvitation"]["x-autplay-rate-limit"] == "20/host/15m; 8/pending-room"
    )
    assert operations["createRoomInvitation"]["x-autplay-authorization"] == (
        "ACTIVE_P13_HOST_ONLY_FRIEND_POLICY"
    )
    assert operations["acceptFriendRoomInvitation"]["x-autplay-authorization"] == (
        "ACTIVE_TARGET_ACCOUNT_DEVICE_BOUND_P13_JOIN"
    )
    assert "RoomInvitationAcceptance" in json.dumps(operations["acceptFriendRoomInvitation"])
    assert "OperationCommand" in json.dumps(operations["cancelFriendRoomInvitation"])


def test_s1c_never_reactivates_guest_or_presence_tracking() -> None:
    room_invitation = load(SCHEMAS / "room-invitation.schema.json")
    assert room_invitation["properties"]["kind"] == {"const": "FRIEND"}
    contact = load(SCHEMAS / "contact-card.schema.json")
    assert contact["x-autplay-signature-domain"] == "autplay:s1c:social-contact-card:v1\\n"
    snapshot = load(SCHEMAS / "social-snapshot.schema.json")
    for field in ("friends", "incoming_requests", "outgoing_requests", "blocked"):
        assert snapshot["properties"][field]["$ref"] == "#/$defs/accounts"
    for field in ("sent_room_invitations", "received_room_invitations"):
        assert snapshot["properties"][field]["$ref"] == "#/$defs/invitations"
    assert snapshot["$defs"]["accounts"]["maxItems"] == 100
    assert snapshot["$defs"]["invitations"]["maxItems"] == 100
    assert "device_id" not in json.dumps(snapshot)
    assert load(EVENT)["x-autplay-implementation-status"] == "DRAFT_NOT_IMPLEMENTED"


def test_s1b_security_freeze_preserves_server_derived_and_exact_key_boundaries() -> None:
    status = load(SCHEMAS / "admission-status.schema.json")
    assert "review_locator" not in status["properties"]
    assert "poll_bearer" not in status["properties"]
    admission = load(SCHEMAS / "admission-request.schema.json")
    assert "expires_at" not in admission["properties"]
    assert admission["properties"]["device_key_thumbprint_sha256"]["pattern"] == "^[a-f0-9]{64}$"
    exchange = load(SCHEMAS / "admission-exchange.schema.json")
    assert {"poll_bearer_sha256", "approved_account_id", "device_public_key_jwk"} <= set(
        exchange["required"]
    )
    assert "poll_bearer" not in exchange["properties"]
    trusted = load(SCHEMAS / "trusted-key-command.schema.json")
    for consequence in (
        "TRUST_ONLY_SESSION_UNCHANGED",
        "BLOCK_FUTURE_ADMISSION_ONLY",
        "REVOKE_ACTIVE_DEVICE_SESSIONS_AND_REMOVE_TRUST",
    ):
        assert consequence in json.dumps(trusted)
    review = load(SCHEMAS / "review-binding.schema.json")
    assert review["x-autplay-server-side-review-binding"] is True
    assert "review_binding" not in load(SCHEMAS / "admission-decision.schema.json")["properties"]


def test_event_allowlist_and_accepted_documents_remain_frozen() -> None:
    schema = load(EVENT)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    valid = {
        "protocol_version": 1,
        "event_id": "99999999-9999-4999-8999-999999999999",
        "kind": "FRIENDSHIP_CHANGED",
        "occurred_at": "2026-08-25T00:00:00Z",
        "payload": {"state": "MUTUAL"},
    }
    assert not list(validator.iter_errors(valid))
    for key in (
        "guest_secret",
        "poll_bearer",
        "review_locator",
        "recording_id",
        "ip_address",
        "device_id",
        "account_id",
    ):
        item = copy.deepcopy(valid)
        payload = item["payload"]
        assert isinstance(payload, dict)
        payload[key] = "x"
        assert list(validator.iter_errors(item)), key
    for path in REQUIRED_ACCEPTED_DOCS:
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8").lower()
        assert "accepted" in text
        assert "awaiting_user_acceptance" not in text


def test_rfc8785_hash_vectors_and_full_tuple_sas_are_stable() -> None:
    examples = load(FIXTURES / "schema-examples.json")["examples"]
    for vector in load(FIXTURES / "hash-vectors.json")["cases"]:
        value: Any = examples[vector["example_index"]]["instance"]
        if "path" in vector:
            value = value[vector["path"]]
        assert hashlib.sha256(rfc8785.dumps(value)).hexdigest() == vector["expected_sha256"]

    vector = load(FIXTURES / "sas-vectors.json")["cases"][0]
    for counter in range(2**32):
        item = [
            vector["domain"],
            vector["server_instance_id"],
            vector["request_id"],
            vector["request_sha256"],
            vector["device_key_thumbprint_sha256"],
            counter,
        ]
        candidate = int.from_bytes(hashlib.sha256(rfc8785.dumps(item)).digest()[:5], "big")
        if candidate < 1_000_000_000_000:
            assert counter == vector["accepted_counter"]
            assert f"{candidate:012d}" == vector["expected_sas_decimal_12"]
            break
    else:
        raise AssertionError("no SAS candidate in uint32 counter space")


def test_deterministic_s1c_race_and_privacy_vectors() -> None:
    cases = load(FIXTURES / "scenario-vectors.json")["cases"]
    vectors = {case["case_id"]: case for case in cases}
    assert set(vectors) == REQUIRED_SCENARIOS
    required_shape = {
        "preconditions",
        "actor",
        "at",
        "operation",
        "transition",
        "expected_outcome",
    }
    assert all(required_shape <= set(vector) for vector in cases)
    assert all(isinstance(vector["preconditions"], dict) for vector in cases)
    assert all(isinstance(vector["actor"], dict) for vector in cases)
    assert all(isinstance(vector["operation"], dict) for vector in cases)
    assert all(isinstance(vector["transition"], dict) for vector in cases)
    assert (
        vectors["friend-block-wins-accept-race"]["expected_outcome"] == "BLOCK_WINS_NO_FRIENDSHIP"
    )
    assert vectors["presence-expiry-90-seconds"]["expected_outcome"] == "EXPIRES_TO_OFFLINE"
    assert vectors["friend-invite-concurrent-accept"]["durable_create_count"] == 1
    assert vectors["friend-invite-expired-or-cancelled"]["expected_outcome"] == "NO_MEMBERSHIP"
    assert vectors["admission-exact-replay"]["durable_create_count"] == 1
    assert vectors["admission-lost-202-secret-rotation"]["secret_generation_count"] == 2
    assert vectors["admission-lost-202-secret-rotation"]["prior_generation_active"] is False
    assert vectors["admission-lost-202-secret-rotation"]["new_generation_active"] is True
    assert (
        vectors["trusted-reenrollment-exact-key-only"]["expected_outcome"]
        == "NEW_KEY_REQUIRES_ADMISSION"
    )
    assert vectors["block-active-room"]["expected_outcome"] == "ACTIVE_ROOM_EXIT_REQUIRED"
    assert not any(vectors["guest-secret-leakage-surfaces"]["secret_exposure"].values())
