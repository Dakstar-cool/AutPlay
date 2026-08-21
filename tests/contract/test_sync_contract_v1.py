"""Device-independent validation for the P04 sync wire contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "contracts" / "events" / "v1"
OPENAPI = ROOT / "contracts" / "openapi" / "v1" / "autplay-sync.openapi.json"
FIXTURES = ROOT / "tests" / "fixtures" / "sync" / "v1"
PROTOCOL = ROOT / "docs" / "design" / "AutPlay_Sync_Protocol_v1.md"
REQUIRED_CASE_IDS = {
    "ambiguous-single-id-envelope",
    "bootstrap-cutover-pending-edits",
    "duplicate-same-payload",
    "clock-skew-metadata-only",
    "cursor-replay-after-failed-page-apply",
    "device-sequence-reuse-different-event",
    "same-id-different-payload",
    "reordered-batch",
    "sequence-gap",
    "partial-rejection",
    "offline-delete",
    "edit-vs-delete",
    "expired-cursor",
    "forged-cursor",
    "bootstrap-pending-edits",
    "unknown-enum-event",
    "oversized-payload",
    "p00-d006-adopt-local-id",
    "p00-d006-cross-owner",
    "p00-d006-lost-ack-bootstrap-reuse",
    "p00-d006-ordered-pre-ack-follow-up",
    "p00-d006-other-device-local-id",
    "p00-d006-server-authoritative-null-id",
    "p00-d006-server-bound-mutation",
    "p00-d006-invalid-redirect-cycle",
    "p00-d006-redirect-alias-and-canonical",
    "p00-d006-redirect-alias-only",
    "p00-d006-tombstone-after-binding",
    "p00-d006-tombstone-before-binding",
    "p00-d006-unavailable-id",
    "journal-epoch-mismatch-device-reset",
    "idempotency-key-reuse-different-identity",
    "lost-ack-duplicate-prefix-with-new-event",
    "unsupported-pulled-server-event",
    "unknown-payload-enum",
}
REQUIRED_INVALID_CASE_IDS = {
    "ack-error-without-request-id",
    "applied-ack-with-null-canonical-id",
    "bootstrap-aggregate-refresh-token",
    "client-event-private-url",
    "client-event-top-level-access-token",
    "conflict-ack-without-conflict",
    "conflict-snapshot-private-url",
    "duplicate-ack-without-original-outcome",
    "error-details-credential",
    "pull-event-access-token",
    "rejected-ack-without-error",
}
REQUIRED_INTERACTION_CASE_IDS = {
    "direct-dismissal-acknowledged-impression",
    "direct-selection-pre-ack-impression",
    "listening-event-with-attribution",
    "local-reranked-impression",
    "offline-pack-impression",
    "organic-listening-explicit-null-attribution",
    "recommendation-impression-model-independent",
    "same-presentation-idempotency",
}
REQUIRED_INTERACTION_INVALID_CASE_IDS = {
    "duplicate-impression-presentation",
    "event-aggregate-id-mismatch",
}
PUBLIC_SCHEMA_TO_OPENAPI = {
    "catalog-artist-payload.schema.json": "CatalogArtistPayload",
    "catalog-artist-credit-payload.schema.json": "CatalogArtistCreditPayload",
    "catalog-recording-credit-link-payload.schema.json": "CatalogRecordingCreditLinkPayload",
    "catalog-release-credit-link-payload.schema.json": "CatalogReleaseCreditLinkPayload",
    "bootstrap-request.schema.json": "BootstrapRequest",
    "bootstrap-response.schema.json": "BootstrapResponse",
    "client-event.schema.json": "ClientEvent",
    "device-binding.schema.json": "DeviceBinding",
    "error.schema.json": "Error",
    "pull-response.schema.json": "PullResponse",
    "push-request.schema.json": "PushRequest",
    "push-response.schema.json": "PushResponse",
    "sync-status.schema.json": "SyncStatus",
}
SENSITIVE_KEYS = {
    "absolute_path",
    "access_token",
    "authorization",
    "base_url",
    "credential",
    "debug_text",
    "feature_vector",
    "filesystem_path",
    "model_features",
    "password",
    "personal_debug",
    "private_url",
    "raw_model_features",
    "raw_path",
    "raw_search_query",
    "refresh_token",
    "search_query",
}


def load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def schema_registry() -> Registry:
    registry = Registry()
    for path in EVENTS.glob("*.json"):
        schema = load_json(path)
        registry = registry.with_resource(
            schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012)
        )
    return registry


def validator(schema_name: str) -> Draft202012Validator:
    schema = load_json(EVENTS / schema_name)
    return Draft202012Validator(schema, registry=schema_registry(), format_checker=FormatChecker())


def openapi_component_validator(component_name: str) -> Draft202012Validator:
    document = load_json(OPENAPI)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{component_name}",
        "components": {"schemas": document["components"]["schemas"]},
    }
    return Draft202012Validator(schema, format_checker=FormatChecker())


def canonical_event_hash(event: dict[str, Any]) -> str:
    immutable = copy.deepcopy(event)
    immutable.pop("request_hash")
    return hashlib.sha256(rfc8785.dumps(immutable)).hexdigest()


def contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in SENSITIVE_KEYS or contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)
    return False


def validation_error_tree(errors: list[Any]) -> list[Any]:
    flattened: list[Any] = []
    pending = list(errors)
    while pending:
        error = pending.pop()
        flattened.append(error)
        pending.extend(error.context)
    return flattened


def has_expected_validation_keyword(errors: list[Any], expected: str) -> bool:
    return any(
        error.validator == expected or expected in {str(part) for part in error.schema_path}
        for error in validation_error_tree(errors)
    )


def test_every_event_schema_is_draft_2020_12_and_self_validating() -> None:
    for path in EVENTS.glob("*.schema.json"):
        schema = load_json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


def test_openapi_contract_is_valid_and_declares_required_sync_operations() -> None:
    document = load_json(OPENAPI)
    validate(document)
    sync_paths = {
        "/devices/bind",
        "/sync/push",
        "/sync/pull",
        "/sync/bootstrap",
        "/sync/status",
    }
    vault_paths = {
        "/vault/uploads",
        "/vault/uploads/{upload_id}",
        "/vault/uploads/{upload_id}/complete",
        "/stream/audio-variants/{audio_variant_id}",
    }
    assert sync_paths <= set(document["paths"])
    assert vault_paths <= set(document["paths"])
    assert set(document["paths"]) == sync_paths | vault_paths
    assert document["openapi"].startswith("3.1.")


def test_openapi_operations_require_complete_binding_and_match_event_contract() -> None:
    document = load_json(OPENAPI)
    parameters = document["components"]["parameters"]
    required_query_names = {
        parameters[name]["name"]
        for name in ("ProtocolVersion", "DeviceId", "ServerProfileId", "JournalEpoch")
    }
    assert required_query_names == {
        "protocol_version",
        "device_id",
        "server_profile_id",
        "journal_epoch",
    }
    for path in ("/sync/pull", "/sync/status"):
        operation = document["paths"][path]["get"]
        references = {parameter["$ref"].rsplit("/", 1)[-1] for parameter in operation["parameters"]}
        assert {"ProtocolVersion", "DeviceId", "ServerProfileId", "JournalEpoch"} <= references

    event_schema = load_json(EVENTS / "client-event.schema.json")
    openapi_event = document["components"]["schemas"]["ClientEvent"]
    assert set(openapi_event["required"]) == set(event_schema["required"])
    assert (
        openapi_event["x-autplay-known-event-dispatch"]
        == event_schema["x-autplay-known-event-dispatch"]
    )
    assert openapi_event["additionalProperties"] is True


def test_valid_event_vectors_match_schema_and_rfc8785_compatible_hashes() -> None:
    vectors = load_json(FIXTURES / "valid-cases.json")
    cases = vectors["cases"]
    assert {case["case_id"] for case in cases} >= REQUIRED_CASE_IDS
    event_validator = validator("client-event.schema.json")
    for case in cases:
        events = [case["event"]] if "event" in case else case.get("events", [])
        for event in events:
            assert not list(event_validator.iter_errors(event)), case["case_id"]
            assert canonical_event_hash(event) == event["request_hash"], case["case_id"]


def test_unknown_additive_event_fields_are_valid_and_hash_covered() -> None:
    event = copy.deepcopy(load_json(FIXTURES / "valid-cases.json")["cases"][0]["event"])
    event["future_additive_field"] = {"future_enum": "FUTURE_VALUE"}
    event["request_hash"] = canonical_event_hash(event)
    assert not list(validator("client-event.schema.json").iter_errors(event))


def test_language_neutral_examples_validate_against_every_public_schema() -> None:
    examples = load_json(FIXTURES / "schema-examples.json")["examples"]
    covered = {example["schema"] for example in examples}
    public_schemas = {path.name for path in EVENTS.glob("*.schema.json")}
    interaction_schemas = {
        "recommendation-attribution.schema.json",
        "listening-event-recorded-payload.schema.json",
        "recommendation-impression-recorded-payload.schema.json",
        "recommendation-feedback-recorded-payload.schema.json",
        "user-interaction-event.schema.json",
        "wave-envelope.schema.json",
    }
    assert covered == public_schemas - {"client-event.schema.json", *interaction_schemas}
    for example in examples:
        errors = list(validator(example["schema"]).iter_errors(example["instance"]))
        assert not errors, (example["schema"], errors)
        component = PUBLIC_SCHEMA_TO_OPENAPI[example["schema"]]
        openapi_errors = list(
            openapi_component_validator(component).iter_errors(example["instance"])
        )
        assert not openapi_errors, (component, openapi_errors)


def test_catalog_artist_payload_dispatch_is_typed_and_member_bound_is_shared() -> None:
    artist_id = "c1111111-1111-4111-8111-111111111111"
    credit_id = "c2111111-1111-4211-8211-111111111111"
    malformed_artist = {
        "aggregate_type": "ARTIST",
        "aggregate_server_id": artist_id,
        "server_row_version": 1,
        "payload": {"name": "Missing canonical identity"},
    }
    assert list(openapi_component_validator("BootstrapAggregate").iter_errors(malformed_artist))

    member = {
        "artist_id": artist_id,
        "position": 0,
        "credited_name": "Member",
        "join_phrase": "",
        "role": "OTHER",
    }
    overflow = {
        "artist_credit_id": credit_id,
        "display_name": "Large credit",
        "names": [member] * 1001,
        "deleted_at": None,
    }
    assert list(validator("catalog-artist-credit-payload.schema.json").iter_errors(overflow))
    assert list(openapi_component_validator("CatalogArtistCreditPayload").iter_errors(overflow))

    link = copy.deepcopy(
        next(
            example["instance"]
            for example in load_json(FIXTURES / "schema-examples.json")["examples"]
            if example["schema"] == "catalog-release-credit-link-payload.schema.json"
        )
    )
    link["owner_recording_ids"] = [
        f"{value:08x}-1111-4111-8111-111111111111" for value in range(101)
    ]
    schema_errors = list(
        validator("catalog-release-credit-link-payload.schema.json").iter_errors(link)
    )
    openapi_errors = list(
        openapi_component_validator("CatalogReleaseCreditLinkPayload").iter_errors(link)
    )
    assert any(error.validator == "maxItems" for error in schema_errors)
    assert any(error.validator == "maxItems" for error in openapi_errors)


def test_known_user_interaction_events_validate_and_are_reproducibly_hashed() -> None:
    cases = load_json(FIXTURES / "user-interaction-valid-cases.json")["cases"]
    hashes = {
        case["fixture_case_id"]: case["expected_sha256"]
        for case in load_json(FIXTURES / "user-interaction-hash-vectors.json")["cases"]
    }
    event_validator = validator("user-interaction-event.schema.json")
    assert {case["case_id"] for case in cases} == set(hashes) == REQUIRED_INTERACTION_CASE_IDS
    for case in cases:
        event = case["event"]
        assert not list(event_validator.iter_errors(event)), case["case_id"]
        openapi_errors = list(openapi_component_validator("ClientEvent").iter_errors(event))
        assert not openapi_errors, case["case_id"]
        assert canonical_event_hash(event) == event["request_hash"] == hashes[case["case_id"]]
        assert event["event_id"] == event["aggregate_local_id"]


def test_user_interaction_invalid_vectors_are_rejected() -> None:
    cases = load_json(FIXTURES / "user-interaction-invalid-cases.json")["cases"]
    assert {case["case_id"] for case in cases} >= REQUIRED_INTERACTION_INVALID_CASE_IDS
    for case in cases:
        if case["validation_kind"] == "semantic":
            assert case["expected"]
            continue
        errors = list(validator(case["schema"]).iter_errors(case["instance"]))
        assert errors, case["case_id"]
        assert has_expected_validation_keyword(errors, case["expected_error"]), case["case_id"]
        if case["schema"] == "user-interaction-event.schema.json":
            openapi_errors = list(
                openapi_component_validator("ClientEvent").iter_errors(case["instance"])
            )
            assert openapi_errors, case["case_id"]


def test_openapi_rejects_impression_without_presentation_fields() -> None:
    cases = load_json(FIXTURES / "user-interaction-invalid-cases.json")["cases"]
    case = next(
        item for item in cases if item["case_id"] == "impression-requires-actual-presentation"
    )
    assert list(openapi_component_validator("ClientEvent").iter_errors(case["instance"]))


def test_recommendation_dispatch_is_additive_and_generator_independent() -> None:
    client_event = load_json(EVENTS / "client-event.schema.json")
    dispatch = client_event["x-autplay-known-event-dispatch"]
    assert set(dispatch) == {
        "LISTENING_EVENT_RECORDED",
        "RECOMMENDATION_IMPRESSION_RECORDED",
        "RECOMMENDATION_FEEDBACK_RECORDED",
    }
    case = load_json(FIXTURES / "user-interaction-valid-cases.json")["cases"][0]
    attribution = case["event"]["payload"]["recommendation"]
    assert {"recommendation_request_id", "recording_id", "source_rank", "source", "surface"} <= set(
        attribution
    )
    attribution_schema = cast(
        dict[str, Any], validator("recommendation-attribution.schema.json").schema
    )
    attribution_properties = attribution_schema["properties"]
    assert all(
        term not in attribution_properties
        for term in (
            "model_version",
            "ranker_version",
            "config_version",
            "candidate_generators",
            "candidate_sources",
        )
    )
    assert not list(validator("client-event.schema.json").iter_errors(case["event"]))


def test_semantic_limits_and_sequence_vectors_are_machine_readable() -> None:
    cases = {case["case_id"]: case for case in load_json(FIXTURES / "valid-cases.json")["cases"]}
    assert cases["oversized-payload"]["input"]["canonical_payload_bytes"] == 262145
    assert cases["oversized-payload"]["expected"]["max_canonical_payload_bytes"] == 262144
    assert cases["reordered-batch"]["input"]["device_sequences"] == [4, 3]
    assert cases["reordered-batch"]["expected"]["domain_mutation_count"] == 0
    assert cases["sequence-gap"]["expected"]["acknowledged_through_device_sequence"] == 2
    assert cases["partial-rejection"]["expected"]["acknowledged_through_device_sequence"] == 9
    duplicate = cases["duplicate-same-payload"]
    assert duplicate["input"]["received_device_sequence"] < 3
    assert duplicate["expected"]["duplicate_lookup_precedes_new_sequence_admission"]
    mixed = cases["lost-ack-duplicate-prefix-with-new-event"]
    assert mixed["input"]["device_sequences"] == [2, 3]
    assert mixed["expected"]["ack_outcomes"] == ["DUPLICATE", "APPLIED"]
    assert mixed["expected"]["acknowledged_through_device_sequence"] == 3
    idempotency_reuse = cases["idempotency-key-reuse-different-identity"]
    assert idempotency_reuse["expected"]["error_code"] == "IDEMPOTENCY_KEY_REUSE"
    assert idempotency_reuse["expected"]["stored_event_unchanged"]


def test_cursor_bootstrap_and_p00_d006_vectors_freeze_atomic_outcomes() -> None:
    cases = {case["case_id"]: case for case in load_json(FIXTURES / "valid-cases.json")["cases"]}
    assert cases["cursor-replay-after-failed-page-apply"]["expected"]["old_cursor_retained"]
    assert not cases["unsupported-pulled-server-event"]["expected"]["cursor_advance"]
    assert cases["p00-d006-lost-ack-bootstrap-reuse"]["expected"]["binding_snapshot_cursor_atomic"]
    redirect = cases["p00-d006-redirect-alias-and-canonical"]["expected"]
    assert redirect["both_local_ids_preserved"]
    assert redirect["server_id_unique_constraint_preserved"]
    assert not redirect["existing_foreign_keys_rewritten"]
    pre_ack = cases["p00-d006-ordered-pre-ack-follow-up"]
    assert [event["device_sequence"] for event in pre_ack["events"]] == [10, 11]
    assert all(event["aggregate_server_id"] is None for event in pre_ack["events"])
    assert pre_ack["expected"]["availability_collision"] is False


def test_protocol_maps_wire_and_future_persistence_seams_explicitly() -> None:
    protocol = PROTOCOL.read_text(encoding="utf-8")
    required_terms = {
        "sync.device_event_inbox",
        "sync.sync_event",
        "sync.device_sync_cursor",
        "sync.tombstone",
        "sync.idempotency_record",
        "offline_journal_event",
        "aggregate_redirect",
        "journal_epoch",
        "applied/deferred server-event",
        "bootstrap snapshot/session",
    }
    assert all(term in protocol for term in required_terms)


def test_invalid_vectors_are_rejected_by_the_named_schema() -> None:
    vectors = load_json(FIXTURES / "invalid-cases.json")
    assert {case["case_id"] for case in vectors["cases"]} >= REQUIRED_INVALID_CASE_IDS
    for case in vectors["cases"]:
        errors = list(validator(case["schema"]).iter_errors(case["instance"]))
        assert errors, case["case_id"]
        assert has_expected_validation_keyword(errors, case["expected_error"]), case["case_id"]


def test_openapi_and_json_schemas_reject_the_same_negative_instances() -> None:
    for case in load_json(FIXTURES / "invalid-cases.json")["cases"]:
        component = PUBLIC_SCHEMA_TO_OPENAPI[case["schema"]]
        errors = list(openapi_component_validator(component).iter_errors(case["instance"]))
        assert errors, (case["case_id"], component)
        assert has_expected_validation_keyword(errors, case["expected_error"]), case["case_id"]


def test_contract_and_vectors_do_not_publish_sensitive_fields() -> None:
    published_paths = [
        *EVENTS.glob("*.json"),
        OPENAPI,
        FIXTURES / "valid-cases.json",
        FIXTURES / "schema-examples.json",
        FIXTURES / "user-interaction-valid-cases.json",
        FIXTURES / "user-interaction-hash-vectors.json",
    ]
    for path in published_paths:
        assert not contains_sensitive_key(load_json(path)), path


@pytest.mark.parametrize("case_id", sorted(REQUIRED_CASE_IDS))
def test_required_case_has_machine_readable_expected_outcome(case_id: str) -> None:
    cases = load_json(FIXTURES / "valid-cases.json")["cases"]
    case = next(item for item in cases if item["case_id"] == case_id)
    assert isinstance(case["expected"], dict) and case["expected"], case_id
