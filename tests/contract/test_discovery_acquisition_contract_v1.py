"""Executable Post-MVP A1A discovery/acquisition contract evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "discovery" / "v1"
FIXTURES = ROOT / "tests" / "fixtures" / "discovery" / "v1"
POLICY = SCHEMAS / "contract-policy.json"
CONTRACT = ROOT / "docs" / "design" / "AutPlay_Discovery_Acquisition_Contract_v1.md"
ADR = ROOT / "docs" / "adr" / "ADR-033-post-mvp-a1a-discovery-acquisition-boundary.md"
PROMPT = ROOT / "docs" / "build-pack" / "prompts" / "POST_MVP_A1A_DISCOVERY_ACQUISITION_CONTRACT.md"

REQUIRED_SCHEMAS = {
    "adapter-manifest.schema.json",
    "artist-policy.schema.json",
    "discovery-candidate.schema.json",
    "discovery-command.schema.json",
    "discovery-query.schema.json",
    "discovery-release.schema.json",
    "discovery-result.schema.json",
    "discovery-run.schema.json",
    "error.schema.json",
}
REQUIRED_SCENARIOS = {
    "metadata-only-visible-not-selectable",
    "playable-capability-without-authorization",
    "playable-capability-with-current-authorization",
    "list-foreign-owner",
    "filter-foreign-owner",
    "run-foreign-artist",
    "select-foreign-candidate",
    "retry-foreign-run",
    "policy-foreign-artist",
    "materialize-foreign-owner",
    "provider-page-exact-replay",
    "web-operation-exact-replay",
    "web-operation-divergent-replay",
    "concurrent-manual-selection",
    "uncertain-identity-never-auto-merges",
    "exact-byte-reuse-needs-owner-authorization",
    "authorization-revoked-before-provider-io",
    "authorization-revoked-before-vault-commit",
    "policy-revoked-before-materialization",
    "stale-worker-after-lease-loss",
    "vault-published-database-transaction-lost",
    "ready-requires-complete-boundary",
    "analysis-terminal-failure-keeps-ready",
    "discovery-creates-no-impression",
    "acquisition-creates-no-impression",
    "ready-track-not-broadcast-globally",
    "disabled-adapter-keeps-core-available",
    "a1b-rejects-scheduled-discovery",
    "a1b-rejects-auto-import",
    "discovered-selectable-missing-common-guard",
    "identity-review-selectable-needs-new-evidence",
    "selected-selectable-needs-owner-reconsideration",
    "unavailable-selectable-needs-new-authorization",
    "ignored-selectable-needs-owner-reconsideration",
    "already-present-selectable-needs-membership-removal",
    "selectable-selected-needs-explicit-owner-action",
    "ready-missing-each-predicate-fails",
    "artist-removed-after-discovery-blocks-reconsideration-and-materialization",
}

EXPECTED_TRANSITIONS = {
    "discovery_run": {
        ("QUEUED", "RUNNING"),
        ("QUEUED", "CANCELLED"),
        ("RUNNING", "COMPLETED"),
        ("RUNNING", "PARTIAL"),
        ("RUNNING", "RETRY_WAIT"),
        ("RUNNING", "FAILED_TERMINAL"),
        ("RUNNING", "CANCELLED"),
        ("RETRY_WAIT", "QUEUED"),
        ("RETRY_WAIT", "CANCELLED"),
    },
    "candidate_disposition": {
        ("DISCOVERED", "ALREADY_IN_LIBRARY"),
        ("DISCOVERED", "IDENTITY_REVIEW_REQUIRED"),
        ("DISCOVERED", "SELECTABLE"),
        ("DISCOVERED", "UNAVAILABLE"),
        ("IDENTITY_REVIEW_REQUIRED", "SELECTABLE"),
        ("IDENTITY_REVIEW_REQUIRED", "ALREADY_IN_LIBRARY"),
        ("IDENTITY_REVIEW_REQUIRED", "UNAVAILABLE"),
        ("IDENTITY_REVIEW_REQUIRED", "IGNORED"),
        ("SELECTABLE", "SELECTED"),
        ("SELECTABLE", "ALREADY_IN_LIBRARY"),
        ("SELECTABLE", "UNAVAILABLE"),
        ("SELECTABLE", "IGNORED"),
        ("SELECTED", "SELECTABLE"),
        ("SELECTED", "ALREADY_IN_LIBRARY"),
        ("SELECTED", "UNAVAILABLE"),
        ("SELECTED", "IGNORED"),
        ("UNAVAILABLE", "SELECTABLE"),
        ("UNAVAILABLE", "ALREADY_IN_LIBRARY"),
        ("UNAVAILABLE", "IDENTITY_REVIEW_REQUIRED"),
        ("UNAVAILABLE", "IGNORED"),
        ("IGNORED", "SELECTABLE"),
        ("IGNORED", "IDENTITY_REVIEW_REQUIRED"),
        ("IGNORED", "UNAVAILABLE"),
        ("ALREADY_IN_LIBRARY", "SELECTABLE"),
    },
    "acquisition": {
        ("QUEUED", "ACQUIRING"),
        ("QUEUED", "CANCELLED"),
        ("QUEUED", "FAILED_TERMINAL"),
        ("ACQUIRING", "INGESTING"),
        ("ACQUIRING", "RETRY_WAIT"),
        ("ACQUIRING", "FAILED_TERMINAL"),
        ("ACQUIRING", "CANCELLED"),
        ("INGESTING", "MATERIALIZING"),
        ("INGESTING", "RETRY_WAIT"),
        ("INGESTING", "FAILED_TERMINAL"),
        ("MATERIALIZING", "READY"),
        ("MATERIALIZING", "RETRY_WAIT"),
        ("MATERIALIZING", "FAILED_TERMINAL"),
        ("RETRY_WAIT", "QUEUED"),
        ("RETRY_WAIT", "CANCELLED"),
    },
    "analysis": {
        ("QUEUED", "RUNNING"),
        ("RUNNING", "COMPLETE"),
        ("RUNNING", "PARTIAL"),
        ("RUNNING", "FAILED_RETRYABLE"),
        ("RUNNING", "FAILED_TERMINAL"),
        ("FAILED_RETRYABLE", "QUEUED"),
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def schema_validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        load_json(SCHEMAS / name),
        format_checker=FormatChecker(),
    )


def example(index: int) -> dict[str, Any]:
    value = load_json(FIXTURES / "schema-examples.json")["examples"][index]
    return cast(dict[str, Any], copy.deepcopy(value["instance"]))


def set_path(instance: dict[str, Any], path: str, value: Any, *, delete: bool = False) -> None:
    parts = path.split(".")
    current: Any = instance
    for part in parts[:-1]:
        current = current[part]
    if delete:
        del current[parts[-1]]
    else:
        current[parts[-1]] = value


def error_keywords(errors: list[Any]) -> set[str]:
    result: set[str] = set()
    pending = list(errors)
    while pending:
        error = pending.pop()
        result.add(error.validator)
        pending.extend(error.context)
    return result


def scenarios() -> dict[str, dict[str, Any]]:
    cases = load_json(FIXTURES / "scenario-vectors.json")["cases"]
    return {case["case_id"]: case for case in cases}


def test_contract_is_accepted_but_runtime_is_explicitly_not_implemented() -> None:
    policy = load_json(POLICY)
    assert policy["status"] == "ACCEPTED_CONTRACT_RUNTIME_NOT_IMPLEMENTED"
    assert "ACCEPTED CONTRACT; RUNTIME NOT IMPLEMENTED" in CONTRACT.read_text(encoding="utf-8")
    assert "Status: Accepted" in ADR.read_text(encoding="utf-8")
    assert "Implementation effect | Contract artifacts only" in PROMPT.read_text(encoding="utf-8")


def test_all_a1a_json_schemas_are_valid_versioned_and_runtime_inactive() -> None:
    paths = [SCHEMAS / name for name in REQUIRED_SCHEMAS]
    assert all(path.is_file() for path in paths)
    for path in paths:
        schema = load_json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://autplay.local/contracts/discovery/v1/{path.name}"
        assert schema["x-autplay-implementation-status"] == (
            "CONTRACT_ACCEPTED_RUNTIME_NOT_IMPLEMENTED"
        )
        Draft202012Validator.check_schema(schema)


def test_language_neutral_examples_validate() -> None:
    values = load_json(FIXTURES / "schema-examples.json")["examples"]
    assert {value["schema"] for value in values} == REQUIRED_SCHEMAS
    for value in values:
        errors = list(schema_validator(value["schema"]).iter_errors(value["instance"]))
        assert not errors, (value["schema"], errors)


def test_invalid_vectors_fail_by_the_named_schema_rule() -> None:
    for case in load_json(FIXTURES / "invalid-cases.json")["cases"]:
        if "replacement" in case:
            instance = copy.deepcopy(case["replacement"])
        else:
            instance = example(case["base_example_index"])
        mutation = case.get("mutation")
        if mutation is not None:
            set_path(
                instance,
                mutation["path"],
                mutation.get("value"),
                delete=mutation.get("delete", False),
            )
        errors = list(schema_validator(case["schema"]).iter_errors(instance))
        assert errors, case["case_id"]
        assert case["expected_error"] in error_keywords(errors), case["case_id"]


def test_owner_is_actor_derived_and_never_accepted_as_input() -> None:
    policy = load_json(POLICY)
    assert policy["owner_source"] == "AUTHENTICATED_WEB_ACTOR_USER_ID"
    assert policy["owner_input_allowed"] is False
    assert policy["artist_policy_key"] == ["OWNER_USER_ID", "CANONICAL_ARTIST_ID"]
    for schema_name in ("discovery-query.schema.json", "discovery-command.schema.json"):
        properties = load_json(SCHEMAS / schema_name)["properties"]
        assert "owner_user_id" not in properties
        assert "user_id" not in properties

    by_id = scenarios()
    for case_id in {
        "list-foreign-owner",
        "filter-foreign-owner",
        "run-foreign-artist",
        "select-foreign-candidate",
        "retry-foreign-run",
        "policy-foreign-artist",
        "materialize-foreign-owner",
    }:
        case = by_id[case_id]
        assert case["expected_error_code"] == "discovery_target_not_found"
        assert case.get("durable_write_count", 0) == 0
        assert case.get("cross_owner_read_count", 0) == 0
        assert case.get("cross_owner_library_write_count", 0) == 0


def test_state_machines_are_closed_and_terminal_states_have_no_outgoing_edge() -> None:
    machines = load_json(POLICY)["state_machines"]
    assert set(machines) == set(EXPECTED_TRANSITIONS)
    for name, machine in machines.items():
        transitions = {tuple(edge) for edge in machine["transitions"]}
        assert transitions == EXPECTED_TRANSITIONS[name]
        assert len(transitions) == len(machine["transitions"])
        states = {machine["initial"], *machine["terminal"]}
        states.update(source for source, _ in transitions)
        states.update(target for _, target in transitions)
        assert machine["initial"] in states
        for terminal in machine["terminal"]:
            assert not any(source == terminal for source, _ in transitions)

    assert ("QUEUED", "READY") not in {
        tuple(edge) for edge in machines["acquisition"]["transitions"]
    }
    assert ("DISCOVERED", "SELECTED") not in {
        tuple(edge) for edge in machines["candidate_disposition"]["transitions"]
    }
    assert load_json(POLICY)["duplicate_evidence"]["candidate_state"] is False


def test_every_unlisted_transition_is_forbidden_for_all_four_machines() -> None:
    machines = load_json(POLICY)["state_machines"]
    for name, machine in machines.items():
        allowed = {tuple(edge) for edge in machine["transitions"]}
        states = {machine["initial"], *machine["terminal"]}
        states.update(value for edge in EXPECTED_TRANSITIONS[name] for value in edge)
        forbidden = {
            (source, target)
            for source in states
            for target in states
            if source != target and (source, target) not in EXPECTED_TRANSITIONS[name]
        }
        assert allowed.isdisjoint(forbidden)
        assert allowed | forbidden == {
            (source, target) for source in states for target in states if source != target
        }


def test_every_selectable_selected_and_ready_edge_has_exact_guards_and_vectors() -> None:
    policy = load_json(POLICY)
    guarded = policy["guarded_transitions"]
    guarded_candidate_edges = {
        (item["from"], item["to"]): set(item["requires"])
        for item in guarded["candidate_disposition"]
    }
    expected_candidate_edges = {
        edge
        for edge in EXPECTED_TRANSITIONS["candidate_disposition"]
        if edge[1] in {"SELECTABLE", "SELECTED"}
    }
    assert set(guarded_candidate_edges) == expected_candidate_edges
    common = {
        "OWNER_MATCH",
        "CANONICAL_ARTIST_ELIGIBLE",
        "IDENTITY_RESOLVED",
        "AUTHORIZED_PLAYABLE",
        "POLICY_ACTIVE",
    }
    for edge, guards in guarded_candidate_edges.items():
        assert common <= guards, edge
    assert (
        "NEW_DURABLE_IDENTITY_EVIDENCE"
        in guarded_candidate_edges[("IDENTITY_REVIEW_REQUIRED", "SELECTABLE")]
    )
    assert (
        "NEW_DURABLE_SOURCE_AUTHORIZATION_OR_EVIDENCE"
        in guarded_candidate_edges[("UNAVAILABLE", "SELECTABLE")]
    )
    assert (
        "OWNER_LIBRARY_MEMBERSHIP_REMOVED"
        in guarded_candidate_edges[("ALREADY_IN_LIBRARY", "SELECTABLE")]
    )
    assert "EXPLICIT_OWNER_ACTION" in guarded_candidate_edges[("SELECTABLE", "SELECTED")]

    ready = guarded["acquisition"]
    assert len(ready) == 1
    assert (ready[0]["from"], ready[0]["to"]) == ("MATERIALIZING", "READY")
    assert set(policy["ready_requires"]) < set(ready[0]["requires"])
    assert "CANONICAL_ARTIST_ELIGIBLE" in ready[0]["requires"]

    by_id = scenarios()
    guard_cases = {case_id for case_id, case in by_id.items() if case["area"] == "TRANSITION_GUARD"}
    assert guard_cases == {
        "discovered-selectable-missing-common-guard",
        "identity-review-selectable-needs-new-evidence",
        "selected-selectable-needs-owner-reconsideration",
        "unavailable-selectable-needs-new-authorization",
        "ignored-selectable-needs-owner-reconsideration",
        "already-present-selectable-needs-membership-removal",
        "selectable-selected-needs-explicit-owner-action",
        "ready-missing-each-predicate-fails",
        "artist-removed-after-discovery-blocks-reconsideration-and-materialization",
    }
    assert set(by_id["ready-missing-each-predicate-fails"]["missing_predicates_tested"]) == set(
        policy["ready_requires"]
    )
    assert by_id["ready-missing-each-predicate-fails"]["each_expected_ready"] is False
    removed = by_id["artist-removed-after-discovery-blocks-reconsideration-and-materialization"]
    assert removed["expected_error_code"] == "discovery_target_not_found"
    assert removed["durable_acquisition_count"] == 0
    assert removed["owner_materialization_count"] == 0


def test_discovery_capability_never_implies_playable_authority() -> None:
    policy = load_json(POLICY)
    assert policy["capabilities"] == {
        "metadata": "RELEASE_DISCOVERY",
        "playable_bytes": "PLAYABLE_ACQUISITION",
        "technical_capability_is_authorization": False,
        "authorization_owner_scoped": True,
        "authorization_recheck_points": [
            "BEFORE_ENQUEUE",
            "BEFORE_PROVIDER_IO",
            "BEFORE_VAULT_COMMIT",
            "BEFORE_OWNER_MATERIALIZATION",
        ],
    }
    by_id = scenarios()
    assert by_id["metadata-only-visible-not-selectable"]["expected_error_code"] == (
        "no_authorized_playable_source"
    )
    assert by_id["playable-capability-without-authorization"]["expected_error_code"] == (
        "source_authorization_unavailable"
    )
    assert (
        by_id["playable-capability-with-current-authorization"]["expected_disposition"]
        == "SELECTABLE"
    )


def test_replay_identity_and_concurrent_selection_converge() -> None:
    by_id = scenarios()
    assert by_id["provider-page-exact-replay"]["candidate_create_count"] == 1
    assert by_id["web-operation-exact-replay"]["durable_write_count"] == 1
    assert by_id["web-operation-exact-replay"]["expected_replayed"] is True
    assert by_id["web-operation-divergent-replay"]["expected_error_code"] == ("operation_conflict")
    concurrent = by_id["concurrent-manual-selection"]
    assert concurrent["active_acquisition_operation_count"] == 1
    assert concurrent["owner_materialization_count"] == 1


def test_provider_identity_hashing_and_web_projection_are_fail_closed() -> None:
    policy = load_json(POLICY)
    assert policy["provider_identity"] == {
        "durable_key": "PROVIDER_ID",
        "provider_key_role": "PRESENTATION_ALIAS_ONLY",
        "provider_key_rename_changes_identity": False,
        "authorization_and_uniqueness_use_provider_id": True,
    }
    assert policy["candidate_uniqueness"][1] == "PROVIDER_ID"
    assert "SERVER_COMPUTED_CANONICAL_REQUEST_SHA256" in policy["web_idempotency"]
    request_hash = policy["canonical_request_hash"]
    assert request_hash["algorithm"] == "RFC8785_SHA256"
    assert request_hash["computed_by"] == "APPLICATION_AFTER_SCHEMA_VALIDATION"
    assert request_hash["client_supplied_digest_allowed"] is False
    assert request_hash["reject_unknown_or_wrong_action_fields_before_hashing"] is True
    assert (
        "request_sha256" not in load_json(SCHEMAS / "discovery-command.schema.json")["properties"]
    )

    candidate_properties = load_json(SCHEMAS / "discovery-candidate.schema.json")["properties"]
    assert "extensions" not in candidate_properties
    assert set(policy["web_display_metadata"]["allowed_fields"]) == {
        "DISPLAY_TITLE",
        "DISPLAY_ARTIST_CREDIT",
        "DISPLAY_RELEASE_TITLE",
        "RELEASE_DATE",
    }
    assert policy["web_display_metadata"]["escaped_text_only"] is True
    assert policy["web_display_metadata"]["trusted_html_allowed"] is False
    assert policy["web_display_metadata"]["routine_log_audit_export_allowed"] is False
    assert policy["derived_projection_invariants"] == {
        "release_aggregate_status_is_source_of_truth": False,
        "release_track_counts_each_lte_total": True,
        "run_counts_are_bounded_projection_only": True,
        "policy_runtime_automation_active_is_source_of_truth": False,
    }

    release = example(6)
    counts = release["track_counts"]
    assert all(value <= counts["total"] for key, value in counts.items() if key != "total")


def test_identity_revocation_crash_and_readiness_vectors_fail_closed() -> None:
    by_id = scenarios()
    uncertain = by_id["uncertain-identity-never-auto-merges"]
    assert uncertain["expected_disposition"] == "IDENTITY_REVIEW_REQUIRED"
    assert uncertain["recording_merge_count"] == 0
    assert by_id["exact-byte-reuse-needs-owner-authorization"]["owner_materialization_count"] == 0

    for case_id in {
        "authorization-revoked-before-provider-io",
        "authorization-revoked-before-vault-commit",
        "policy-revoked-before-materialization",
        "stale-worker-after-lease-loss",
    }:
        case = by_id[case_id]
        assert case.get("vault_commit_count", 0) == 0
        assert case.get("owner_materialization_count", 0) == 0

    lost = by_id["vault-published-database-transaction-lost"]
    assert lost["reported_ready"] is False
    assert lost["expected_acquisition_state"] == "RETRY_WAIT"

    policy = load_json(POLICY)
    assert len(policy["ready_requires"]) == 10
    assert policy["job_completed_implies_ready"] is False
    assert policy["analysis_failure_rolls_back_ready"] is False
    assert by_id["analysis-terminal-failure-keeps-ready"]["playable"] is True


def test_discovery_and_acquisition_create_no_global_exposure() -> None:
    by_id = scenarios()
    assert by_id["discovery-creates-no-impression"]["impression_create_count"] == 0
    assert by_id["acquisition-creates-no-impression"]["impression_create_count"] == 0
    ready = by_id["ready-track-not-broadcast-globally"]
    assert ready["global_feed_insert_count"] == 0
    assert ready["cross_owner_library_write_count"] == 0
    assert ready["candidate_path"] == "EXISTING_OWNER_AUTHORIZED_RECOMMENDATION_PIPELINE"
    assert load_json(POLICY)["exposure"]["ready_candidate_uses_existing_mandatory_filters"]


def test_a1b_is_manual_only_and_adapter_failure_preserves_core_availability() -> None:
    policy = load_json(POLICY)
    assert policy["runtime_activation"]["A1B"] == ["MANUAL_ONLY", "REVIEW_REQUIRED"]
    by_id = scenarios()
    for case_id in {"a1b-rejects-scheduled-discovery", "a1b-rejects-auto-import"}:
        assert by_id[case_id]["expected_error_code"] == "automation_not_active"
        assert by_id[case_id]["durable_write_count"] == 0
    degraded = by_id["disabled-adapter-keeps-core-available"]
    assert degraded["cpu_api_available"]
    assert degraded["cpu_worker_core_available"]
    assert degraded["android_local_playback_available"]


def test_scenario_matrix_is_complete_and_redacted() -> None:
    by_id = scenarios()
    assert set(by_id) == REQUIRED_SCENARIOS
    fixture_text = "\n".join(
        (FIXTURES / name).read_text(encoding="utf-8")
        for name in ("schema-examples.json", "scenario-vectors.json", "hash-vectors.json")
    ).lower()
    for forbidden in (
        "bearer ",
        "-----begin private key-----",
        "c:\\\\users\\\\",
        'access_token"',
        'refresh_token"',
    ):
        assert forbidden not in fixture_text


def test_rfc8785_hash_vectors_are_deterministic() -> None:
    vectors = load_json(FIXTURES / "hash-vectors.json")
    assert vectors["algorithm"] == "RFC8785_SHA256"
    assert {case["case_id"] for case in vectors["cases"]} == {
        "start-discovery-command",
        "provider-page-identity",
        "select-candidate-a",
        "select-candidate-b",
    }
    actual_by_id: dict[str, str] = {}
    for case in vectors["cases"]:
        actual = hashlib.sha256(rfc8785.dumps(case["input"])).hexdigest()
        assert actual == case["expected_sha256"], case["case_id"]
        actual_by_id[case["case_id"]] = actual
    assert actual_by_id["select-candidate-a"] != actual_by_id["select-candidate-b"]


def test_contract_records_exact_a1b_blocker_and_no_provider_choice() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in (CONTRACT, ADR, PROMPT))
    for required in {
        "Exact A1B prerequisite",
        "RELEASE_DISCOVERY",
        "PLAYABLE_ACQUISITION",
        "no provider",
        "A1C",
        "F-016",
        "ADR-019",
    }:
        assert required.lower() in text.lower()
    for provider in {"spotify", "yandex music", "musicbrainz", "discogs"}:
        assert provider not in text.lower()
