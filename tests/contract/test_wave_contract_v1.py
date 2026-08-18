"""Language-neutral P13 Wave contract freeze."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "contracts" / "openapi" / "v1" / "autplay-wave.openapi.json"
EVENT_SCHEMA = ROOT / "contracts" / "events" / "v1" / "wave-envelope.schema.json"


def test_wave_openapi_freezes_authenticated_snapshot_and_strict_start() -> None:
    document = json.loads(OPENAPI.read_text(encoding="utf-8"))
    assert document["openapi"] == "3.1.0"
    assert document["security"] == [{"bearerAuth": []}]
    assert "/wave/rooms/{room_id}/snapshot" in document["paths"]
    assert "/wave/rooms/{room_id}/start" in document["paths"]
    assert "/wave/rooms/{room_id}/availability" in document["paths"]
    assert "PLAY" not in document["components"]["schemas"]["Command"]["properties"]["kind"]["enum"]
    assert (
        document["components"]["schemas"]["CreateRoom"]["properties"]["allow_user_ids"]["maxItems"]
        == 7
    )


def test_wave_live_envelope_requires_version_and_ordered_event_fields() -> None:
    schema = json.loads(EVENT_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(
        {
            "protocol_version": 1,
            "type": "event",
            "epoch": "2",
            "sequence": 9,
            "kind": "PLAY",
            "payload": {"effective_at": "2026-08-17T12:00:00Z"},
            "effective_at": "2026-08-17T12:00:00Z",
        }
    )
    errors = list(validator.iter_errors({"protocol_version": 1, "type": "event"}))
    assert {required for error in errors for required in error.validator_value} >= {
        "epoch",
        "sequence",
        "kind",
    }
