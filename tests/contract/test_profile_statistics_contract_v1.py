"""Executable S2 friend-visible profile statistics privacy contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "profile-statistics" / "v1"
FIXTURES = ROOT / "tests" / "fixtures" / "profile-statistics" / "v1"
OPENAPI = ROOT / "contracts" / "openapi" / "v1" / "autplay-profile-statistics.openapi.json"
EXPECTED_SCHEMAS = {
    "profile-statistics.schema.json",
    "settings-command.schema.json",
    "settings-receipt.schema.json",
    "settings-view.schema.json",
}


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_s2_schemas_and_examples_are_strict() -> None:
    paths = list(SCHEMAS.glob("*.schema.json"))
    assert {path.name for path in paths} == EXPECTED_SCHEMAS
    examples = load(FIXTURES / "examples.json")["examples"]
    assert {item["schema"] for item in examples} == EXPECTED_SCHEMAS
    for path in paths:
        schema = load(path)
        assert schema["$id"] == (
            f"https://autplay.local/contracts/profile-statistics/v1/{path.name}"
        )
        assert schema["x-autplay-implementation-status"] == "IMPLEMENTED_S2"
        Draft202012Validator.check_schema(schema)
    for item in examples:
        validator = Draft202012Validator(
            load(SCHEMAS / item["schema"]), format_checker=FormatChecker()
        )
        assert not list(validator.iter_errors(item["instance"])), item["schema"]


def test_profile_statistics_rejects_extra_media_or_activity_fields_and_wrong_windows() -> None:
    example = copy.deepcopy(load(FIXTURES / "examples.json")["examples"][3]["instance"])
    schema = load(SCHEMAS / "profile-statistics.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    forbidden = (
        "account_id",
        "recording_id",
        "track_title",
        "artist_name",
        "current_track",
        "last_played_at",
        "device_id",
        "session_id",
        "context",
        "event_origin",
    )
    for field in forbidden:
        candidate = copy.deepcopy(example)
        candidate[field] = "private"
        assert list(validator.iter_errors(candidate)), field
    wrong_order = copy.deepcopy(example)
    wrong_order["windows"][0]["window"] = "LAST_30_COMPLETE_DAYS"
    assert list(validator.iter_errors(wrong_order))
    missing_window = copy.deepcopy(example)
    missing_window["windows"].pop()
    assert list(validator.iter_errors(missing_window))
    current_day = copy.deepcopy(example)
    current_day["windows"].append(
        {
            "window": "CURRENT_DAY",
            "play_session_count": 1,
            "listened_ms": 1,
            "unique_track_count": 1,
        }
    )
    assert list(validator.iter_errors(current_day))


def test_settings_commands_are_revisioned_exact_and_private_by_default() -> None:
    examples = load(FIXTURES / "examples.json")["examples"]
    command = copy.deepcopy(examples[0]["instance"])
    command_schema = Draft202012Validator(
        load(SCHEMAS / "settings-command.schema.json"), format_checker=FormatChecker()
    )
    command["expected_revision"] = -1
    assert list(command_schema.iter_errors(command))
    view = examples[1]["instance"]
    assert view == {
        "schema_version": 1,
        "friends_can_view_statistics": False,
        "revision": 0,
    }


def test_openapi_freezes_friend_only_no_store_surface() -> None:
    api = load(OPENAPI)
    validate(api, base_uri=OPENAPI.as_uri())
    assert api["x-autplay-implementation-status"] == "IMPLEMENTED_S2"
    assert api["security"] == [{"bearerAuth": []}]
    operations = {
        operation["operationId"]: operation
        for path in api["paths"].values()
        for operation in path.values()
    }
    assert set(operations) == {
        "getProfileStatisticsSettings",
        "setProfileStatisticsSettings",
        "getFriendProfileStatistics",
    }
    assert operations["setProfileStatisticsSettings"]["x-autplay-rate-limit"] == "10/account/15m"
    friend = operations["getFriendProfileStatistics"]
    assert friend["x-autplay-authorization"] == (
        "ACTIVE_SESSION_ACCEPTED_FRIEND_NO_BLOCK_TARGET_OPT_IN"
    )
    assert friend["x-autplay-rate-limit"] == "30/viewer/15m; 10/pair/15m"
    for operation in operations.values():
        for response in operation["responses"].values():
            resolved = api["components"]["responses"]["Error"] if "$ref" in response else response
            headers = resolved["headers"]
            assert set(headers) == {"Cache-Control", "Pragma", "Vary"}


def test_accepted_s2_documents_exist() -> None:
    for path in (
        ROOT / "docs" / "adr" / "ADR-040-s2-profile-statistics-privacy.md",
        ROOT / "docs" / "build-pack" / "prompts" / "POST_MVP_S2_PROFILE_STATISTICS_PRIVACY.md",
    ):
        assert path.is_file()
