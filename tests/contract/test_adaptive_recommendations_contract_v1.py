"""Executable freeze for the Post-MVP R1A adaptive recommendation contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "recommendations" / "v1"
FIXTURES = ROOT / "tests" / "fixtures" / "recommendations" / "v1"
POLICY = SCHEMAS / "contract-policy.json"
CONTRACT = ROOT / "docs" / "design" / "AutPlay_Adaptive_Recommendation_Contract_v1.md"
ADR = ROOT / "docs" / "adr" / "ADR-048-r1a-adaptive-recommendation-profile.md"
HANDOFF = (
    ROOT / "docs" / "implementation" / "HANDOFF_POST_MVP_R1A_ADAPTIVE_RECOMMENDATIONS_CONTRACT.md"
)

REQUIRED_SCHEMAS = {
    "adaptive-profile.schema.json",
    "contract-policy.schema.json",
    "feature-policy.schema.json",
    "offline-temporal-delta.schema.json",
    "owner-export-manifest.schema.json",
    "owner-export.schema.json",
    "temporal-evidence.schema.json",
    "temporal-snapshot.schema.json",
}
EXAMPLE_SCHEMAS = REQUIRED_SCHEMAS - {"contract-policy.schema.json"}
REQUIRED_SCENARIOS = {
    "bounded-owner-export",
    "cold-start-high-bounded-plasticity",
    "contradictory-evidence-lowers-confidence-first",
    "delayed-sync-does-not-rewrite-snapshot",
    "device-unbinding-purges-local-context-only",
    "explicit-dislike-remains-mandatory-filter",
    "fading-interest-negative-momentum",
    "fatigue-recovers-with-expiry-and-completion",
    "future-clock-skew-clamps-and-reduces-confidence",
    "maturity-growth-reduces-single-event-influence",
    "overlapping-horizons-conserve-event-mass",
    "partial-active-horizons-renormalize-mass",
    "past-clock-skew-disables-recent-only",
    "privacy-delete-dead-device-does-not-claim-wipe",
    "privacy-delete-offline-device-awaits-receipt-or-local-erasure",
    "privacy-deletion-cascades-and-denies-replay",
    "privacy-restore-reapplies-active-deletion-tombstone",
    "recommendation-causal-chain-conserves-intent-mass",
    "recommendation-causal-chain-normalizes-over-budget-mass",
    "recommendation-feedback-never-becomes-organic",
    "retention-expiry-blocks-algorithmic-replay",
    "rising-interest-positive-momentum",
    "short-listens-raise-temporary-fatigue",
    "truncated-export-remains-open",
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
    value = load(FIXTURES / "schema-examples.json")["examples"]
    return cast(list[dict[str, Any]], value)


def scenarios() -> dict[str, dict[str, Any]]:
    values = load(FIXTURES / "scenario-vectors.json")["cases"]
    return {case["case_id"]: case for case in values}


def set_path(instance: dict[str, Any], path: str, value: Any, *, delete: bool) -> None:
    current: Any = instance
    parts = path.split(".")
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    final = parts[-1]
    if delete:
        if isinstance(current, list):
            del current[int(final)]
        else:
            del current[final]
    elif isinstance(current, list):
        current[int(final)] = value
    else:
        current[final] = value


def error_keywords(errors: list[Any]) -> set[str]:
    keywords: set[str] = set()
    pending = list(errors)
    while pending:
        error = pending.pop()
        keywords.add(error.validator)
        pending.extend(error.context)
    return keywords


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def maturity_score(policy: dict[str, Any], values: dict[str, Any]) -> float:
    maturity = policy["maturity"]
    assert maturity["formula"] == "WEIGHTED_SIX_TERM_SATURATION_V1"
    assert maturity["recency_formula"] == "POW_HALF_AGE_OVER_HALF_LIFE_V1"
    recency = 0.5 ** (values["recency_age_ms"] / maturity["recency_half_life_ms"])
    if "recency" in values:
        assert values["recency"] == pytest.approx(recency)
    components = {
        "EFFECTIVE_SIGNAL_MASS": min(
            1.0, values["effective_signal_mass"] / maturity["effective_mass_saturation"]
        ),
        "TRACK_AND_ARTIST_COVERAGE": (
            min(1.0, values["track_coverage"] / maturity["track_coverage_saturation"])
            + min(1.0, values["artist_coverage"] / maturity["artist_coverage_saturation"])
        )
        / 2,
        "OBSERVATION_SPAN": min(
            1.0,
            values["observation_span_ms"] / maturity["observation_span_saturation_ms"],
        ),
        "RECENCY": recency,
        "CONSISTENCY": values["consistency"],
        "ORGANIC_SHARE": values["organic_share"],
    }
    return float(
        sum(maturity["term_weights"][term] * component for term, component in components.items())
    )


def policy_semantic_error_codes(policy: dict[str, Any]) -> set[str]:
    errors: set[str] = set()
    if any(weight <= 0 for weight in policy["active_view_weights"].values()):
        errors.add("ACTIVE_VIEW_WEIGHT_ZERO")
    if sum(policy["active_view_weights"].values()) != pytest.approx(1.0):
        errors.add("ACTIVE_VIEW_WEIGHT_SUM_INVALID")
    if sum(policy["maturity"]["term_weights"].values()) != pytest.approx(1.0):
        errors.add("MATURITY_WEIGHT_SUM_INVALID")
    if policy["skip_completion_threshold"] >= policy["completion_threshold"]:
        errors.add("OUTCOME_THRESHOLD_OVERLAP")
    if policy["maturity"]["minimum_plasticity"] > policy["maturity"]["maximum_plasticity"]:
        errors.add("PLASTICITY_RANGE_INVALID")
    positive_signals = {
        "EXPLICIT_LIKE",
        "FINALIZED_ORGANIC_LISTEN",
        "FINALIZED_RECOMMENDATION_LISTEN",
        "RECOMMENDATION_SELECTED",
        "FINALIZED_COMPLETION",
    }
    negative_signals = {"RECOMMENDATION_DISMISSED", "FINALIZED_SHORT_LISTEN_SKIP"}
    if any(policy["signal_weights"][signal] <= 0 for signal in positive_signals) or any(
        policy["signal_weights"][signal] >= 0 for signal in negative_signals
    ):
        errors.add("SIGNAL_SIGN_INVALID")
    if policy["origin_weights"]["RECOMMENDATION"] >= min(
        policy["origin_weights"]["ORGANIC"], policy["origin_weights"]["SOURCE_QUEUE"]
    ):
        errors.add("ORIGIN_WEIGHT_ORDER_INVALID")
    caps = policy["adjustment_caps"]
    if caps["total_absolute"] > sum(
        value for key, value in caps.items() if key != "total_absolute"
    ):
        errors.add("ADJUSTMENT_CAP_INVALID")
    return errors


def semantic_error_codes(
    schema_name: str,
    instance: dict[str, Any],
    *,
    observed_parent_pack_sha256: str | None = None,
    evaluation_at_ms: int | None = None,
) -> set[str]:
    errors: set[str] = set()
    if schema_name == "temporal-snapshot.schema.json":
        profile = instance["adaptive_profile"]
        if profile["owner_user_id"] != instance["owner_user_id"]:
            errors.add("OWNER_SCOPE_MISMATCH")
        if any(
            profile[key] != instance[key]
            for key in ("cutoff_at_ms", "interaction_watermark", "feature_policy")
        ):
            errors.add("SNAPSHOT_PROFILE_MISMATCH")
        if any(
            not isinstance(event["server_sequence"], int)
            or event["server_sequence"] > instance["interaction_watermark"]
            or event["time_classification"] == "LOCAL_UNSYNCED_TIME"
            for event in instance["source_evidence"]
        ):
            errors.add("SNAPSHOT_SEQUENCE_INVALID")
        if any(
            event["owner_user_id"] != instance["owner_user_id"]
            for event in instance["source_evidence"]
        ):
            errors.add("OWNER_SCOPE_MISMATCH")
        evidence_ids = [
            (event["owner_user_id"], event["evidence_id"]) for event in instance["source_evidence"]
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            errors.add("DUPLICATE_EVIDENCE_ID")
        source_hashes: dict[tuple[str, str], set[str]] = {}
        derivations: list[tuple[str, str, str, str]] = []
        for event in instance["source_evidence"]:
            source_key = (event["owner_user_id"], event["source_event_id"])
            source_hashes.setdefault(source_key, set()).add(event["source_request_sha256"])
            derivations.append((*source_key, event["signal_key"], event["derivation_key"]))
        if any(len(hashes) != 1 for hashes in source_hashes.values()):
            errors.add("SOURCE_EVENT_HASH_CONFLICT")
        if len(derivations) != len(set(derivations)):
            errors.add("DUPLICATE_DERIVATION")
        if len(rfc8785.dumps(instance)) > load(POLICY)["bounds"]["maximum_snapshot_bytes"]:
            errors.add("SNAPSHOT_TOO_LARGE")
    elif schema_name == "owner-export.schema.json":
        if any(
            profile["owner_user_id"] != instance["owner_user_id"]
            for profile in instance["profiles"]
        ):
            errors.add("OWNER_SCOPE_MISMATCH")
        if instance["page"]["page_index"] > instance["page"]["page_count"]:
            errors.add("EXPORT_PAGE_INVALID")
        if (
            instance["page"]["complete"] is True
            and instance["page"]["page_index"] != instance["page"]["page_count"]
        ):
            errors.add("EXPORT_PAGE_INVALID")
        item_count = (
            len(instance["profiles"])
            + len(instance["snapshot_lineage"])
            + sum(len(item["bounded_event_lineage"]) for item in instance["snapshot_lineage"])
        )
        if instance["page"]["item_count"] != item_count:
            errors.add("EXPORT_ITEM_COUNT_MISMATCH")
        if len(rfc8785.dumps(instance)) > load(POLICY)["bounds"]["maximum_export_page_bytes"]:
            errors.add("EXPORT_PAGE_TOO_LARGE")
    elif schema_name == "offline-temporal-delta.schema.json":
        if any(event["device_id"] != instance["device_id"] for event in instance["local_events"]):
            errors.add("DEVICE_BINDING_MISMATCH")
        if any(
            event["owner_user_id"] != instance["owner_user_id"]
            or event["server_profile_id"] != instance["server_profile_id"]
            for event in instance["local_events"]
        ):
            errors.add("PROFILE_BINDING_MISMATCH")
        if any(
            event["server_sequence"] is not None
            or event["time_classification"] != "LOCAL_UNSYNCED_TIME"
            for event in instance["local_events"]
        ):
            errors.add("LOCAL_EVENT_STATE_INVALID")
        if (
            not (
                instance["created_at_ms"]
                < instance["expires_at_ms"]
                <= instance["parent_expires_at_ms"]
            )
            or instance["expires_at_ms"] - instance["created_at_ms"] > 604800000
        ):
            errors.add("DELTA_EXPIRY_INVALID")
        parent_pairs = {
            (item["recording_id"], item["source_rank"]) for item in instance["parent_items"]
        }
        adjustment_pairs = [
            (item["recording_id"], item["source_rank"]) for item in instance["adjustments"]
        ]
        if len(adjustment_pairs) != len(set(adjustment_pairs)):
            errors.add("DUPLICATE_ADJUSTMENT")
        for field in ("display_position", "impression_event_id", "impression_key_sha256"):
            values = [item[field] for item in instance["adjustments"]]
            if len(values) != len(set(values)):
                errors.add("DUPLICATE_PRESENTATION_IDENTITY")
        if any(pair not in parent_pairs for pair in adjustment_pairs):
            errors.add("PARENT_ITEM_MISMATCH")
        if (
            observed_parent_pack_sha256 is not None
            and observed_parent_pack_sha256 != instance["parent_pack_sha256"]
        ):
            errors.add("PARENT_PACK_HASH_MISMATCH")
        if evaluation_at_ms is not None:
            if evaluation_at_ms >= instance["expires_at_ms"]:
                errors.add("DELTA_EXPIRED")
            if evaluation_at_ms >= instance["parent_expires_at_ms"]:
                errors.add("PARENT_PACK_EXPIRED")
    return errors


def export_bundle_error_codes(
    manifest: dict[str, Any], pages: list[dict[str, Any]], *, observed_page_bytes: int | None = None
) -> set[str]:
    errors: set[str] = set()
    indexes = [page["page"]["page_index"] for page in pages]
    expected = list(range(1, manifest["page_count"] + 1))
    if indexes != expected:
        errors.add("EXPORT_PAGE_SEQUENCE_INVALID")
    if len(indexes) != len(set(indexes)):
        errors.add("EXPORT_PAGE_DUPLICATE")
    if any(
        page["page"]["export_id"] != manifest["export_id"]
        or page["owner_user_id"] != manifest["owner_user_id"]
        or page["page"]["page_count"] != manifest["page_count"]
        for page in pages
    ):
        errors.add("EXPORT_MANIFEST_SCOPE_MISMATCH")
    observed_hashes = [
        canonical_sha256({key: value for key, value in page.items() if key != "export_sha256"})
        for page in pages
    ]
    declared_hashes = [page["export_sha256"] for page in pages]
    if observed_hashes != declared_hashes or observed_hashes != manifest["ordered_page_sha256"]:
        errors.add("EXPORT_PAGE_HASH_MISMATCH")
    manifest_without_self_hash = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    if canonical_sha256(manifest_without_self_hash) != manifest["manifest_sha256"]:
        errors.add("EXPORT_MANIFEST_HASH_MISMATCH")
    page_byte_limit = load(POLICY)["bounds"]["maximum_export_page_bytes"]
    if observed_page_bytes is not None and observed_page_bytes > page_byte_limit:
        errors.add("EXPORT_PAGE_TOO_LARGE")
    if (
        not pages
        or pages[-1]["page"]["complete"] is not True
        or any(page["page"]["complete"] is not False for page in pages[:-1])
    ):
        errors.add("EXPORT_NOT_COMPLETE")
    return errors


def snapshot_with_exact_canonical_size(target_bytes: int) -> dict[str, Any]:
    snapshot = cast(dict[str, Any], copy.deepcopy(examples()[3]["instance"]))
    template = snapshot["source_evidence"][0]
    events: list[dict[str, Any]] = []
    for index in range(1, 6001):
        event = copy.deepcopy(template)
        event["evidence_id"] = f"00000000-0000-7000-8000-{10000000 + index:012x}"
        event["source_event_id"] = f"00000000-0000-7000-8000-{index:012x}"
        event["device_sequence"] = index
        event["server_sequence"] = index
        event["dimensions"][1]["key"] = "x"
        events.append(event)

    low = 0
    high = len(events)
    while low < high:
        middle = (low + high + 1) // 2
        snapshot["source_evidence"] = events[:middle]
        if len(rfc8785.dumps(snapshot)) <= target_bytes:
            low = middle
        else:
            high = middle - 1
    snapshot["source_evidence"] = events[:low]
    remaining = target_bytes - len(rfc8785.dumps(snapshot))
    for event in reversed(snapshot["source_evidence"]):
        if remaining == 0:
            break
        increment = min(remaining, 199)
        event["dimensions"][1]["key"] += "x" * increment
        remaining -= increment
    assert remaining == 0
    assert len(rfc8785.dumps(snapshot)) == target_bytes
    return snapshot


def test_r1a_is_accepted_contract_only_and_runtime_unimplemented() -> None:
    policy = load(POLICY)
    assert policy["status"] == "ACCEPTED_RUNTIME_NOT_IMPLEMENTED"
    assert policy["implementation_effect"] == "NONE"
    assert policy["serving_pipeline_unchanged"] is True
    assert policy["public_recommendation_dto_unchanged"] is True
    assert policy["cpu_baseline_required"] is True
    assert policy["gpu_required"] is False

    assert "ACCEPTED; RUNTIME NOT IMPLEMENTED" in CONTRACT.read_text(encoding="utf-8")
    assert "Status: Accepted by the user on 2026-09-03" in ADR.read_text(encoding="utf-8")
    assert "`PASS`" in HANDOFF.read_text(encoding="utf-8")


def test_schema_set_is_strict_versioned_and_runtime_inactive() -> None:
    assert {path.name for path in SCHEMAS.glob("*.schema.json")} == REQUIRED_SCHEMAS
    for path in SCHEMAS.glob("*.schema.json"):
        schema = load(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == (f"https://autplay.local/contracts/recommendations/v1/{path.name}")
        assert schema["x-autplay-implementation-status"] == "ACCEPTED_RUNTIME_NOT_IMPLEMENTED"
        assert schema["additionalProperties"] is False
        Draft202012Validator.check_schema(schema)


def test_language_neutral_examples_validate() -> None:
    registry = schema_registry()
    assert {item["schema"] for item in examples()} == EXAMPLE_SCHEMAS
    for item in examples():
        validator = Draft202012Validator(
            load(SCHEMAS / item["schema"]),
            registry=registry,
            format_checker=FormatChecker(),
        )
        assert not list(validator.iter_errors(item["instance"])), item["schema"]

    policy_validator = Draft202012Validator(load(SCHEMAS / "contract-policy.schema.json"))
    assert not list(policy_validator.iter_errors(load(POLICY)))

    for case in load(FIXTURES / "contract-policy-invalid-cases.json")["cases"]:
        invalid_policy = copy.deepcopy(load(POLICY))
        mutation = case["mutation"]
        set_path(invalid_policy, mutation["path"], mutation["value"], delete=False)
        errors = list(policy_validator.iter_errors(invalid_policy))
        assert errors, case["case_id"]
        assert case["expected_error"] in error_keywords(errors), case["case_id"]


def test_invalid_vectors_fail_by_the_named_rule() -> None:
    registry = schema_registry()
    source_examples = examples()
    for case in load(FIXTURES / "invalid-cases.json")["cases"]:
        instance = copy.deepcopy(source_examples[case["base_example_index"]]["instance"])
        mutation = case["mutation"]
        set_path(
            instance,
            mutation["path"],
            mutation.get("value"),
            delete=mutation.get("delete", False),
        )
        validator = Draft202012Validator(
            load(SCHEMAS / case["schema"]),
            registry=registry,
            format_checker=FormatChecker(),
        )
        errors = list(validator.iter_errors(instance))
        assert errors, case["case_id"]
        assert case["expected_error"] in error_keywords(errors), case["case_id"]


def test_cross_document_owner_binding_expiry_and_parent_invariants_fail_closed() -> None:
    source_examples = examples()
    assert not semantic_error_codes("temporal-snapshot.schema.json", source_examples[3]["instance"])
    assert not semantic_error_codes(
        "offline-temporal-delta.schema.json", source_examples[4]["instance"]
    )
    assert not semantic_error_codes("owner-export.schema.json", source_examples[5]["instance"])
    valid_local_delta = copy.deepcopy(source_examples[4]["instance"])
    local_event = copy.deepcopy(source_examples[1]["instance"])
    local_event["server_sequence"] = None
    local_event["time_classification"] = "LOCAL_UNSYNCED_TIME"
    valid_local_delta["local_events"].append(local_event)
    assert not semantic_error_codes("offline-temporal-delta.schema.json", valid_local_delta)
    validator = Draft202012Validator(
        load(SCHEMAS / "offline-temporal-delta.schema.json"),
        registry=schema_registry(),
        format_checker=FormatChecker(),
    )
    assert not list(validator.iter_errors(valid_local_delta))

    for case in load(FIXTURES / "semantic-invalid-cases.json")["cases"]:
        instance = copy.deepcopy(source_examples[case["base_example_index"]]["instance"])
        if "fixture_profile_example_index" in case:
            instance["profiles"].append(
                copy.deepcopy(source_examples[case["fixture_profile_example_index"]]["instance"])
            )
        if "fixture_event_example_index" in case:
            instance["local_events"].append(
                copy.deepcopy(source_examples[case["fixture_event_example_index"]]["instance"])
            )
        if case.get("duplicate_adjustment"):
            instance["adjustments"].append(copy.deepcopy(instance["adjustments"][0]))
        if case.get("duplicate_source_evidence"):
            instance["source_evidence"].append(copy.deepcopy(instance["source_evidence"][0]))
        if case.get("conflicting_source_evidence"):
            conflict = copy.deepcopy(instance["source_evidence"][0])
            conflict["evidence_id"] = "00000000-0000-7000-8000-000000000199"
            conflict["signal_key"] = "FINALIZED_ORGANIC_LISTEN"
            conflict["derivation_key"] = "BASE_LISTEN_V1"
            conflict["source_request_sha256"] = "a" * 64
            instance["source_evidence"].append(conflict)
        if "mutation" in case:
            mutation = case["mutation"]
            set_path(instance, mutation["path"], mutation["value"], delete=False)
        if "also_mutate" in case:
            mutation = case["also_mutate"]
            set_path(instance, mutation["path"], mutation["value"], delete=False)
        errors = semantic_error_codes(
            case["schema"],
            instance,
            observed_parent_pack_sha256=case.get("observed_parent_pack_sha256"),
            evaluation_at_ms=case.get("evaluation_at_ms"),
        )
        assert case["expected_error_code"] in errors, case["case_id"]


def test_hash_vectors_freeze_rfc8785_bytes_and_sha256() -> None:
    documents = {
        "schema-examples.json": load(FIXTURES / "schema-examples.json"),
        "contract-policy.json": load(POLICY),
        "impression-key-vectors.json": load(FIXTURES / "impression-key-vectors.json"),
    }

    def resolve(source: str) -> Any:
        filename, pointer = source.split("#", 1)
        current: Any = documents[filename]
        if pointer.strip("/"):
            for part in pointer.strip("/").split("/"):
                current = current[int(part)] if isinstance(current, list) else current[part]
        return current

    self_hash_fields = {
        "feature-policy-canonical-hash": "content_sha256",
        "normalized-evidence-canonical-hash": "normalized_evidence_sha256",
        "adaptive-profile-canonical-hash": "profile_sha256",
        "temporal-snapshot-canonical-hash": "snapshot_sha256",
        "offline-delta-canonical-hash": "delta_sha256",
        "owner-export-canonical-hash": "export_sha256",
        "owner-export-manifest-canonical-hash": "manifest_sha256",
    }
    for vector in load(FIXTURES / "hash-vectors.json")["vectors"]:
        source_value = resolve(vector["source"])
        value = copy.deepcopy(source_value)
        for field in vector["exclude_fields"]:
            del value[field]
        canonical = rfc8785.dumps(value)
        assert len(canonical) == vector["canonical_utf8_length"], vector["case_id"]
        assert hashlib.sha256(canonical).hexdigest() == vector["sha256"]
        if vector["case_id"] in self_hash_fields:
            assert source_value[self_hash_fields[vector["case_id"]]] == vector["sha256"]

    snapshot = examples()[3]["instance"]
    delta = examples()[4]["instance"]
    by_id = {
        vector["case_id"]: vector["sha256"]
        for vector in load(FIXTURES / "hash-vectors.json")["vectors"]
    }
    assert snapshot["source_evidence_sha256"] == by_id["source-evidence-list-canonical-hash"]
    assert snapshot["derived_features_sha256"] == by_id["derived-features-canonical-hash"]
    assert snapshot["event_time_policy_sha256"] == by_id["event-time-policy-canonical-hash"]
    assert delta["parent_items_sha256"] == by_id["parent-items-canonical-hash"]
    assert (
        delta["adjustments"][0]["impression_key_sha256"] == by_id["impression-key-canonical-hash"]
    )
    for evidence in snapshot["source_evidence"]:
        evidence_without_self_hash = {
            key: value for key, value in evidence.items() if key != "normalized_evidence_sha256"
        }
        assert evidence["normalized_evidence_sha256"] == canonical_sha256(
            evidence_without_self_hash
        )
    nested_profile = snapshot["adaptive_profile"]
    profile_without_self_hash = {
        key: value for key, value in nested_profile.items() if key != "profile_sha256"
    }
    assert nested_profile["profile_sha256"] == canonical_sha256(profile_without_self_hash)


def test_policy_weights_caps_and_plasticity_are_bounded() -> None:
    candidate = examples()[0]["instance"]
    assert not policy_semantic_error_codes(candidate)
    assert sum(candidate["active_view_weights"].values()) == pytest.approx(1.0)
    assert sum(candidate["maturity"]["term_weights"].values()) == pytest.approx(1.0)
    assert (
        candidate["maturity"]["minimum_plasticity"] <= candidate["maturity"]["maximum_plasticity"]
    )
    assert candidate["signal_weights"]["EXPLICIT_LIKE"] > 0
    assert candidate["signal_weights"]["FINALIZED_SHORT_LISTEN_SKIP"] < 0
    assert candidate["origin_weights"]["RECOMMENDATION"] < candidate["origin_weights"]["ORGANIC"]
    assert (
        candidate["origin_weights"]["RECOMMENDATION"] < candidate["origin_weights"]["SOURCE_QUEUE"]
    )
    caps = candidate["adjustment_caps"]
    assert all(0 <= value <= 1 for key, value in caps.items() if key != "total_absolute")
    assert caps["total_absolute"] <= sum(
        value for key, value in caps.items() if key != "total_absolute"
    )

    for case in load(FIXTURES / "policy-semantic-invalid-cases.json")["cases"]:
        invalid = copy.deepcopy(candidate)
        set_path(invalid, case["mutation"]["path"], case["mutation"]["value"], delete=False)
        assert case["expected_error_code"] in policy_semantic_error_codes(invalid), case["case_id"]


def test_every_signal_is_captured_or_explicitly_deferred() -> None:
    signals = load(POLICY)["signals"]
    assert len({signal["signal_key"] for signal in signals}) == len(signals)
    for signal in signals:
        status = signal["capture_status"]
        assert status.startswith("CAPTURED") or status == "DEFERRED_EXPLICIT_CAPTURE_REQUIRED"
        if status == "DEFERRED_EXPLICIT_CAPTURE_REQUIRED":
            assert signal["source_event_types"] == []
            assert signal["recent_eligible"] is False
            assert signal["durable_effect"] == "NONE"

    by_key = {signal["signal_key"]: signal for signal in signals}
    assert by_key["VOLUNTARY_REPEAT"]["capture_status"] == ("DEFERRED_EXPLICIT_CAPTURE_REQUIRED")
    assert by_key["VOLUNTARY_SURFACE_SELECTION"]["capture_status"] == (
        "DEFERRED_EXPLICIT_CAPTURE_REQUIRED"
    )
    assert by_key["EXPLICIT_LIKE"]["source_event_types"] == ["USER_TRACK_PREFERENCE_SET"]
    assert by_key["EXPLICIT_DISLIKE"]["source_event_types"] == ["USER_TRACK_PREFERENCE_SET"]
    assert by_key["EXPLICIT_DISLIKE"]["durable_effect"] == "MANDATORY_FILTER"
    assert by_key["EXCLUDE_FROM_TASTE"]["recent_eligible"] is False


def test_horizon_fusion_conserves_each_event_mass() -> None:
    selected = scenarios()
    for case_id in {
        "overlapping-horizons-conserve-event-mass",
        "partial-active-horizons-renormalize-mass",
    }:
        case = selected[case_id]
        raw = case["input"]["active_view_weights"]
        active = case["input"]["active_views"]
        denominator = sum(raw[horizon] for horizon in active)
        normalized = {horizon: raw[horizon] / denominator for horizon in active}
        expected = case["expected"]["normalized_coefficients"]
        assert normalized == pytest.approx(expected)
        assert sum(normalized.values()) == pytest.approx(1.0)
        fused_mass = case["input"]["event_base_mass"] * sum(normalized.values())
        assert fused_mass == pytest.approx(case["expected"]["total_fused_event_mass"])


def test_causal_chain_fusion_conserves_intent_mass() -> None:
    selected = scenarios()
    for case_id in {
        "recommendation-causal-chain-conserves-intent-mass",
        "recommendation-causal-chain-normalizes-over-budget-mass",
    }:
        case = selected[case_id]
        weights = case["input"]["signed_active_signal_weights"]
        denominator = max(1.0, sum(abs(value) for value in weights.values()))
        normalized = {key: value / denominator for key, value in weights.items()}
        assert normalized == pytest.approx(case["expected"]["normalized_signed_weights"])
        assert sum(abs(value) for value in normalized.values()) <= 1.0
        assert case["expected"]["independent_intention_count"] == 1
        assert case["expected"]["completion_and_skip_both_active"] is False

    snapshot = copy.deepcopy(examples()[3]["instance"])
    outcome = snapshot["source_evidence"][0]
    base_listen = copy.deepcopy(outcome)
    base_listen["evidence_id"] = "00000000-0000-7000-8000-000000000103"
    base_listen["signal_key"] = "FINALIZED_ORGANIC_LISTEN"
    base_listen["derivation_key"] = "BASE_LISTEN_V1"
    base_listen["signed_strength"] = 0.4
    snapshot["source_evidence"].append(base_listen)
    assert not semantic_error_codes("temporal-snapshot.schema.json", snapshot)


def test_evidence_identity_rejects_duplicate_and_source_hash_conflict() -> None:
    snapshot = copy.deepcopy(examples()[3]["instance"])
    duplicate = copy.deepcopy(snapshot["source_evidence"][0])
    snapshot["source_evidence"].append(duplicate)
    assert "DUPLICATE_EVIDENCE_ID" in semantic_error_codes(
        "temporal-snapshot.schema.json", snapshot
    )

    duplicate["evidence_id"] = "00000000-0000-7000-8000-000000000199"
    duplicate["source_request_sha256"] = "a" * 64
    assert "SOURCE_EVENT_HASH_CONFLICT" in semantic_error_codes(
        "temporal-snapshot.schema.json", snapshot
    )


def test_maturity_controls_bounded_monotonic_plasticity_not_account_age() -> None:
    selected = scenarios()
    cold = selected["cold-start-high-bounded-plasticity"]
    values = cold["input"]
    candidate = examples()[0]["instance"]
    calculated_maturity = maturity_score(candidate, values)
    calculated = candidate["maturity"]["maximum_plasticity"] - calculated_maturity * (
        candidate["maturity"]["maximum_plasticity"] - candidate["maturity"]["minimum_plasticity"]
    )
    assert calculated_maturity == pytest.approx(cold["expected"]["maturity"])
    assert calculated == pytest.approx(cold["expected"]["plasticity_multiplier"])
    assert (
        candidate["maturity"]["minimum_plasticity"]
        <= calculated
        <= candidate["maturity"]["maximum_plasticity"]
    )
    assert cold["expected"]["registration_age_used"] is False

    profile = examples()[2]["instance"]
    assert maturity_score(candidate, profile["maturity"]) == pytest.approx(
        profile["maturity"]["score"]
    )

    growth = selected["maturity-growth-reduces-single-event-influence"]
    values = growth["input"]
    span = values["maximum_plasticity"] - values["minimum_plasticity"]
    before = values["maximum_plasticity"] - values["before_maturity"] * span
    after = values["maximum_plasticity"] - values["after_maturity"] * span
    assert before == pytest.approx(growth["expected"]["before_plasticity"])
    assert after == pytest.approx(growth["expected"]["after_plasticity"])
    assert after < before
    assert growth["expected"]["recent_context_still_enabled"] is True


def test_outcome_classifier_thresholds_are_total_and_non_overlapping() -> None:
    policy = examples()[0]["instance"]
    assert policy["algorithm_parameters"]["outcome_classifier"] == (
        "EXCLUSION_THEN_SKIP_ELSE_COMPLETION_ELSE_NEUTRAL_V1"
    )
    assert policy["algorithm_parameters"]["outcome_null_completion_ratio_rule"] == (
        "RATIO_PREDICATES_FALSE_PLAYED_MS_SKIP_STILL_APPLIES_V1"
    )
    assert policy["skip_completion_threshold"] < policy["completion_threshold"]
    for case in load(FIXTURES / "outcome-boundaries.json")["cases"]:
        if case["excluded_from_taste"]:
            outcome = "EXCLUDED"
        elif (
            case["completion_ratio"] is not None
            and case["completion_ratio"] <= policy["skip_completion_threshold"]
        ) or case["played_ms"] < policy["skip_played_ms_threshold"]:
            outcome = "FINALIZED_SHORT_LISTEN_SKIP"
        elif (
            case["completion_ratio"] is not None
            and case["completion_ratio"] >= policy["completion_threshold"]
        ):
            outcome = "FINALIZED_COMPLETION"
        else:
            outcome = "NEUTRAL"
        assert outcome == case["expected_outcome"], case["case_id"]


def test_direction_confidence_fatigue_and_filter_scenarios_are_distinct() -> None:
    selected = scenarios()
    assert set(selected) == REQUIRED_SCENARIOS

    rising = selected["rising-interest-positive-momentum"]
    fading = selected["fading-interest-negative-momentum"]
    assert (
        rising["input"]["horizon_scores_oldest_to_newest"][-1]
        > rising["input"]["horizon_scores_oldest_to_newest"][0]
    )
    assert rising["expected"]["momentum_sign"] == "POSITIVE"
    rising_momentum = (
        rising["input"]["horizon_scores_oldest_to_newest"][-1]
        - rising["input"]["horizon_scores_oldest_to_newest"][0]
    )
    assert rising_momentum == pytest.approx(rising["expected"]["momentum_value"])
    assert (
        fading["input"]["horizon_scores_oldest_to_newest"][-1]
        < fading["input"]["horizon_scores_oldest_to_newest"][0]
    )
    assert fading["expected"]["momentum_sign"] == "NEGATIVE"
    fading_momentum = (
        fading["input"]["horizon_scores_oldest_to_newest"][-1]
        - fading["input"]["horizon_scores_oldest_to_newest"][0]
    )
    assert fading_momentum == pytest.approx(fading["expected"]["momentum_value"])

    fatigue = selected["short-listens-raise-temporary-fatigue"]["expected"]
    contradiction = selected["contradictory-evidence-lowers-confidence-first"]["expected"]
    mandatory_filter = selected["explicit-dislike-remains-mandatory-filter"]["expected"]
    assert fatigue["long_term_score_erased"] is False
    assert fatigue["mandatory_filter_created"] is False
    assert contradiction["confidence_relation"] == "DECREASES"
    assert contradiction["score_forced_to_extreme"] is False
    assert mandatory_filter["candidate_eligible"] is False
    assert mandatory_filter["score_composition_evaluated"] is False

    parameters = examples()[0]["instance"]["algorithm_parameters"]["fatigue"]
    skip_case = selected["short-listens-raise-temporary-fatigue"]
    skip_input = skip_case["input"]
    skip_value = min(
        1.0,
        max(
            0.0,
            skip_input["prior_fatigue"]
            * parameters["daily_decay_factor"] ** skip_input["elapsed_days"]
            + skip_input["finalized_skip_count"] * parameters["skip_gain"]
            - skip_input["completion_mass"] * parameters["completion_recovery"],
        ),
    )
    assert skip_value == pytest.approx(skip_case["expected"]["fatigue_value"])

    recovery_case = selected["fatigue-recovers-with-expiry-and-completion"]
    recovery_input = recovery_case["input"]
    recovery_value = min(
        1.0,
        max(
            0.0,
            recovery_input["prior_fatigue"]
            * parameters["daily_decay_factor"] ** recovery_input["elapsed_days"]
            + recovery_input["skip_mass"] * parameters["skip_gain"]
            - recovery_input["recent_completed_listens"] * parameters["completion_recovery"],
        ),
    )
    assert recovery_value == pytest.approx(recovery_case["expected"]["fatigue_value"])

    contradiction_case = selected["contradictory-evidence-lowers-confidence-first"]
    contradiction_input = contradiction_case["input"]
    contradiction_ratio = (
        2
        * min(contradiction_input["positive_mass"], contradiction_input["negative_mass"])
        / (contradiction_input["positive_mass"] + contradiction_input["negative_mass"])
    )
    confidence = contradiction_input["previous_confidence"] * (1 - contradiction_ratio)
    assert contradiction_ratio == pytest.approx(
        contradiction_case["expected"]["contradiction_ratio"]
    )
    assert confidence == pytest.approx(contradiction_case["expected"]["confidence_value"])


def test_event_time_replay_and_retention_never_substitute_current_state() -> None:
    policy = load(POLICY)
    assert policy["event_time"]["client_occurred_at_controls_inclusion_or_watermark"] is False
    assert policy["event_time"]["accepted_event_effective_time_orders_feature_projection"] is True
    selected = scenarios()
    future = selected["future-clock-skew-clamps-and-reduces-confidence"]
    assert future["expected"]["effective_at_ms"] == future["input"]["received_at_ms"]
    assert future["expected"]["time_classification"] == "FUTURE_SKEW_CLAMPED"

    past = selected["past-clock-skew-disables-recent-only"]["expected"]
    assert past["recent_eligible"] is False
    assert past["durable_evidence_may_remain"] is True

    delayed = selected["delayed-sync-does-not-rewrite-snapshot"]["expected"]
    assert delayed["existing_snapshot_changes"] is False
    assert delayed["earlier_exact_replay_changes"] is False

    expired = selected["retention-expiry-blocks-algorithmic-replay"]["expected"]
    assert expired["algorithmic_replay"] == policy["replay"]["missing_temporal_input_error"]
    assert expired["current_state_substitution"] is False
    assert policy["replay"]["current_state_substitution_allowed"] is False

    boundaries = load(FIXTURES / "event-time-boundaries.json")
    received = boundaries["received_at_ms"]
    future_tolerance = boundaries["future_clock_tolerance_ms"]
    backfill = boundaries["maximum_recent_backfill_ms"]
    for case in boundaries["cases"]:
        occurred = case["occurred_at_ms"]
        if occurred is None:
            effective = received
            classification = "RECEIPT_TIME_ONLY"
            recent_eligible = True
        elif occurred - received > future_tolerance:
            effective = received
            classification = "FUTURE_SKEW_CLAMPED"
            recent_eligible = True
        elif received - occurred > backfill:
            effective = occurred
            classification = "PAST_SKEW_RECENT_DISABLED"
            recent_eligible = False
        elif occurred < received:
            effective = occurred
            classification = "DELAYED_WITHIN_POLICY"
            recent_eligible = True
        else:
            effective = occurred
            classification = "TRUSTED_EVENT_TIME"
            recent_eligible = True
        assert effective == case["expected_effective_at_ms"], case["case_id"]
        assert classification == case["expected_classification"], case["case_id"]
        assert recent_eligible is case["expected_recent_eligible"], case["case_id"]

    for case in load(FIXTURES / "snapshot-size-boundaries.json")["cases"]:
        within_limit = case["canonical_utf8_length"] <= policy["bounds"]["maximum_snapshot_bytes"]
        assert within_limit is case["expected_accepted"], case["case_id"]

    byte_limit = policy["bounds"]["maximum_snapshot_bytes"]
    over_limit_snapshot = snapshot_with_exact_canonical_size(byte_limit + 1)
    at_limit_snapshot = copy.deepcopy(over_limit_snapshot)
    for evidence in reversed(at_limit_snapshot["source_evidence"]):
        token = evidence["dimensions"][1]
        if len(token["key"]) > 1:
            token["key"] = token["key"][:-1]
            break
    assert len(rfc8785.dumps(at_limit_snapshot)) == byte_limit
    assert "SNAPSHOT_TOO_LARGE" not in semantic_error_codes(
        "temporal-snapshot.schema.json", at_limit_snapshot
    )
    assert "SNAPSHOT_TOO_LARGE" in semantic_error_codes(
        "temporal-snapshot.schema.json", over_limit_snapshot
    )


def test_privacy_export_delete_and_unbinding_cover_every_artifact_class() -> None:
    privacy = load(POLICY)["privacy"]
    deletion = privacy["owner_delete"]
    for artifact in {
        "raw_interactions",
        "normalized_temporal_events",
        "adaptive_profiles",
        "temporal_snapshots",
        "p11_input_snapshots",
        "request_items_and_replay_traces",
        "server_offline_packs",
    }:
        assert "PURGE" in deletion[artifact]
    assert deletion["partial_delete_allowed"] is False
    assert deletion["offline_or_dead_device_remote_wipe_guaranteed"] is False
    assert deletion["server_completion_claims_device_purge"] is False
    assert deletion["retention_trigger_override"].startswith("RESTRICTED_SECURITY_DEFINER")
    assert deletion["server_transaction_order"] == [
        "R1_AND_P11_OFFLINE_PACKS",
        "LIBRARY_INTERACTION_LISTENING_AND_PREFERENCE_ROWS",
        "R1_AND_P11_RECOMMENDATION_ITEMS_AND_TRACES",
        "R1_AND_P11_RECOMMENDATION_REQUESTS",
        "R1_TEMPORAL_SNAPSHOTS",
        "P11_INPUT_SNAPSHOTS",
        "R1_ADAPTIVE_PROFILES",
        "R1_NORMALIZED_TEMPORAL_EVENTS",
        "SYNC_TOMBSTONE_ROWS",
        "SYNC_EVENT_AND_DEVICE_INBOX_ROWS",
    ]
    assert deletion["post_delete_exact_replay"] == "RECOMMENDATION_NOT_FOUND"
    assert deletion["post_delete_algorithmic_replay"] == "RECOMMENDATION_NOT_FOUND"

    unbinding = privacy["device_unbinding"]
    assert unbinding["derived_recommendation_pack_delta_and_memory"] == "IMMEDIATE_PURGE"
    assert unbinding["credentials_and_active_binding"] == "IMMEDIATE_PURGE"
    assert unbinding["library_outbox_media_and_profile_data"].startswith("RETAIN_SEALED")
    assert unbinding["retained_prior_profile_access"] == "INACCESSIBLE_TO_OTHER_BINDINGS"
    assert unbinding["remote_owner_state"] == "UNCHANGED_NOT_CLAIMED_DELETED"
    assert unbinding["later_profile_inheritance"] is False

    export = privacy["export"]
    assert "LONG_TERM_AND_RECENT_SCORE_CONFIDENCE" in export["includes"]
    assert "MATURITY_COMPONENTS" in export["includes"]
    assert {"SECRETS", "RAW_PATHS", "OTHER_OWNER_DATA"} <= set(export["excludes"])
    assert export["incomplete_request_status"] == "OPEN"

    selected = scenarios()
    for case_id in {
        "privacy-delete-dead-device-does-not-claim-wipe",
        "privacy-delete-offline-device-awaits-receipt-or-local-erasure",
    }:
        expected = selected[case_id]["expected"]
        assert expected["server_claims_local_purge"] is False
        assert expected["remote_wipe_guaranteed"] is False
    restore = selected["privacy-restore-reapplies-active-deletion-tombstone"]["expected"]
    assert restore["production_routing_before_redelete"] is False
    assert restore["zero_owner_rows_verified"] is True
    assert selected["truncated-export-remains-open"]["expected"]["request_status"] == "OPEN"


def test_privacy_delete_order_matches_reference_fk_restrict_dependencies() -> None:
    ddl = (ROOT / "server" / "migrations" / "reference_v1.sql").read_text(encoding="utf-8")
    listening_request_fk = (
        "REFERENCES ml.recommendation_request(recommendation_request_id) ON DELETE RESTRICT"
    )
    assert listening_request_fk in ddl
    assert "REFERENCES sync.sync_event(event_id) ON DELETE RESTRICT" in ddl
    order = load(POLICY)["privacy"]["owner_delete"]["server_transaction_order"]
    assert order.index("LIBRARY_INTERACTION_LISTENING_AND_PREFERENCE_ROWS") < order.index(
        "R1_AND_P11_RECOMMENDATION_REQUESTS"
    )
    assert order.index("SYNC_TOMBSTONE_ROWS") < order.index("SYNC_EVENT_AND_DEVICE_INBOX_ROWS")


def test_owner_export_manifest_is_contiguous_unique_bounded_and_hash_linked() -> None:
    page = copy.deepcopy(examples()[5]["instance"])
    manifest = copy.deepcopy(examples()[6]["instance"])
    page_without_self_hash = {key: value for key, value in page.items() if key != "export_sha256"}
    page["export_sha256"] = canonical_sha256(page_without_self_hash)
    manifest["ordered_page_sha256"] = [page["export_sha256"]]
    assert not export_bundle_error_codes(manifest, [page])

    for case in load(FIXTURES / "export-bundle-cases.json")["cases"]:
        invalid_pages = [copy.deepcopy(page) for _ in case["page_indexes"]]
        for invalid_page, page_index in zip(invalid_pages, case["page_indexes"], strict=True):
            invalid_page["page"]["page_index"] = page_index
        errors = export_bundle_error_codes(
            manifest,
            invalid_pages,
            observed_page_bytes=case.get("observed_page_bytes"),
        )
        assert case["expected_error_code"] in errors, case["case_id"]


def test_offline_delta_and_evaluation_keep_p11_safety_boundaries() -> None:
    policy = load(POLICY)
    offline = policy["offline_delta"]
    assert offline["parent_pack_must_be_verified"] is True
    assert offline["source_rank_mutation_allowed"] is False
    assert offline["mandatory_filters_preserved"] is True
    assert offline["actual_presentation_creates_one_stable_impression"] is True
    assert offline["later_sync_rewrites_prior_impression"] is False
    assert offline["maximum_lifetime_ms"] == 604800000
    assert offline["expires_no_later_than_parent_pack"] is True
    assert offline["parent_pack_and_item_hashes_required"] is True

    delta = examples()[4]["instance"]
    assert "DELTA_EXPIRED" not in semantic_error_codes(
        "offline-temporal-delta.schema.json",
        delta,
        evaluation_at_ms=delta["expires_at_ms"] - 1,
    )
    assert "DELTA_EXPIRED" in semantic_error_codes(
        "offline-temporal-delta.schema.json",
        delta,
        evaluation_at_ms=delta["expires_at_ms"],
    )
    key_material = load(FIXTURES / "impression-key-vectors.json")["vectors"][0]["material"]
    assert key_material == {
        "owner_user_id": delta["owner_user_id"],
        "device_id": delta["device_id"],
        "recommendation_request_id": delta["recommendation_request_id"],
        "recording_id": delta["adjustments"][0]["recording_id"],
        "source_rank": delta["adjustments"][0]["source_rank"],
    }
    assert delta["adjustments"][0]["impression_key_sha256"] == canonical_sha256(key_material)

    evaluation = policy["evaluation"]
    assert evaluation["required_scenario_direction_pass_rate"] == 1.0
    assert evaluation["mandatory_filter_violation_count"] == 0
    assert evaluation["owner_isolation_violation_count"] == 0
    assert evaluation["shadow_impression_count"] == 0
    assert evaluation["algorithmic_replay_mismatch_count"] == 0
    assert evaluation["activation_requires_r1b_pass"] is True
    assert evaluation["activation_requires_explicit_user_decision"] is True


def test_r1b_storage_room_and_api_effects_are_proposals_only() -> None:
    proposed = load(POLICY)["proposed_not_implemented"]
    assert proposed["postgresql"]
    assert proposed["room"]
    assert proposed["api"] == ["no public DTO change; optional owner export projection only"]
