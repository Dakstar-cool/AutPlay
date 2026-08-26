"""Executable Post-MVP A1C automation policy evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "contracts" / "discovery" / "v1" / "automation-policy.json"
SCENARIOS = ROOT / "tests" / "fixtures" / "discovery" / "v1" / "automation-scenarios.json"
PROMPT = (
    ROOT / "docs" / "build-pack" / "prompts" / "POST_MVP_A1C_SCHEDULED_DISCOVERY_AUTO_IMPORT.md"
)
ADR = ROOT / "docs" / "adr" / "ADR-042-a1c-scheduled-discovery-auto-import.md"
COMMAND_SCHEMA = ROOT / "contracts" / "discovery" / "v1" / "automation-command.schema.json"


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _cases() -> dict[str, dict[str, Any]]:
    return {case["case_id"]: case for case in _load(SCENARIOS)["cases"]}


def test_a1c_artifacts_freeze_one_default_off_policy() -> None:
    policy = _load(POLICY)
    assert policy["contract_version"] == "release-discovery-v1"
    assert policy["automation_policy_version"] == 1
    assert policy["operator_gate_default"] is False
    assert policy["safe_product_default"] == ["MANUAL_ONLY", "REVIEW_REQUIRED"]
    assert PROMPT.is_file() and ADR.is_file()
    assert COMMAND_SCHEMA.is_file()
    assert policy["command_schema"] == COMMAND_SCHEMA.name
    assert policy["web_operation_namespace"] == {
        "scope": ["OWNER_USER_ID", "OPERATION_ID"],
        "shared_across_actions": True,
        "divergent_action_result": "operation_conflict",
    }


def test_automation_commands_are_strict_and_hash_exact_validated_documents() -> None:
    validator = Draft202012Validator(_load(COMMAND_SCHEMA), format_checker=FormatChecker())
    operation_id = str(uuid4())
    policy_id = str(uuid4())
    candidate_id = str(uuid4())
    artist_id = str(uuid4())
    commands: tuple[dict[str, Any], ...] = (
        {
            "contract_version": "release-discovery-v1",
            "schema_version": 1,
            "operation_id": operation_id,
            "action": "START_DISCOVERY",
            "policy_id": policy_id,
        },
        {
            "contract_version": "release-discovery-v1",
            "schema_version": 1,
            "operation_id": operation_id,
            "action": "RETRY_CANDIDATE",
            "candidate_id": candidate_id,
        },
        {
            "contract_version": "release-discovery-v1",
            "schema_version": 1,
            "operation_id": operation_id,
            "action": "SET_ARTIST_POLICY",
            "canonical_artist_id": artist_id,
            "provider_artist_id": "20",
            "discovery_mode": "SCHEDULED",
            "import_mode": "AUTO_IMPORT",
            "automation_enabled": True,
            "expected_policy_revision": None,
            "consequence_confirmation": (
                "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
            ),
        },
    )
    for command in commands:
        validator.validate(command)

    expected = hashlib.sha256(rfc8785.dumps(commands[2])).hexdigest()
    assert (
        hashlib.sha256(rfc8785.dumps(dict(reversed(tuple(commands[2].items()))))).hexdigest()
        == expected
    )

    unknown = {**commands[0], "owner_user_id": str(uuid4())}
    wrong_action_field = {**commands[0], "candidate_id": candidate_id}
    assert not validator.is_valid(unknown)
    assert not validator.is_valid(wrong_action_field)


def test_schedule_provider_and_retry_bounds_are_exact() -> None:
    policy = _load(POLICY)
    assert policy["schedule"] == {
        "cadence_seconds": 86400,
        "scheduler_poll_seconds": 300,
        "scheduler_claim_limit": 20,
        "one_active_run_per_policy": True,
        "manual_run_can_bypass_cadence": False,
    }
    assert policy["provider"]["page_size"] == 25
    assert policy["provider"]["max_pages_per_run"] == 2
    assert policy["provider"]["checkpoint_max_bytes"] == 2048
    assert policy["retry"] == {
        "max_attempts": 5,
        "base_seconds": 2,
        "max_seconds": 300,
        "provider_retry_after_max_seconds": 86400,
    }


def test_auto_import_is_separately_confirmed_and_bounded() -> None:
    auto = _load(POLICY)["auto_import"]
    assert auto["confirmation_code"] == (
        "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
    )
    assert auto["max_enqueue_per_run"] == 10
    assert auto["max_enqueue_per_owner_rolling_24h"] == 50
    assert auto["review_required_enqueues_acquisition"] is False

    cases = _cases()
    assert cases["auto-import-run-cap"]["acquisition_enqueue_count"] == 10
    assert cases["auto-import-owner-rolling-cap"]["acquisition_enqueue_count"] == 3
    assert cases["review-required-discovers-only"]["acquisition_enqueue_count"] == 0


def test_policy_revision_is_rechecked_at_every_automatic_boundary() -> None:
    boundaries = _load(POLICY)["authorization"]["recheck_boundaries"]
    assert boundaries == [
        "SCHEDULER_RESERVATION",
        "RUN_CLAIM",
        "BEFORE_PROVIDER_IO",
        "PAGE_COMMIT",
        "AUTO_SELECTION",
        "ACQUISITION_CLAIM",
        "BEFORE_ACQUIRE",
        "PRE_PUBLISH",
        "PRE_MATERIALIZE",
    ]
    cases = _cases()
    assert cases["policy-revoked-before-provider-io"]["provider_call_count"] == 0
    assert cases["policy-revoked-after-provider-response"]["auto_selection_count"] == 0
    assert cases["policy-revoked-before-vault-publish"]["ready_count"] == 0
    assert cases["policy-revoked-before-materialize"]["ready_count"] == 0


def test_manual_authority_ready_media_and_audience_remain_independent() -> None:
    cases = _cases()
    assert cases["auto-cancel-does-not-revoke-manual"]["manual_authorization_active"]
    retry = cases["manual-after-cancelled-auto-new-attempt"]
    assert retry == {
        "case_id": "manual-after-cancelled-auto-new-attempt",
        "auto_attempt_state": "CANCELLED",
        "manual_attempt_state": "QUEUED",
    }
    assert cases["disable-keeps-ready-media"]["library_delete_count"] == 0
    exposure = cases["automation-creates-no-exposure"]
    assert exposure["impression_count"] == 0
    assert exposure["global_insert_count"] == 0
    assert exposure["cross_owner_write_count"] == 0


def test_fixture_is_bounded_redacted_and_complete() -> None:
    cases = _cases()
    assert len(cases) == 16
    fixture = SCENARIOS.read_text(encoding="utf-8").lower()
    for forbidden in ("bearer ", "access_token", "refresh_token", "source_url", "c:\\users\\"):
        assert forbidden not in fixture
