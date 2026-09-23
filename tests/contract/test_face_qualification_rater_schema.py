"""Rater rows carry fixed axes and no participant or identity-map fields."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "contracts/face/v2/qualification-rater-submission.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = Draft202012Validator(SCHEMA)
AXES = (
    "POSITIVE_MELANCHOLIC",
    "CALM_ENERGETIC",
    "SOFT_AGGRESSIVE",
    "LIGHT_DARK",
    "RELAXED_TENSE",
    "DIRECT_ATMOSPHERIC",
)


def _valid() -> dict[str, Any]:
    axes = {name: 0 for name in AXES}
    return {
        "schema_version": 1,
        "study_id": "00000000-0000-4000-8000-000000000001",
        "fixture_manifest_sha256": "a" * 64,
        "rubric_version": "FACE_QUALIFICATION_RUBRIC_V1",
        "rater_pseudonym": "00000000-0000-4000-8000-000000000002",
        "consent_receipt_sha256": "b" * 64,
        "recording_sha256": "c" * 64,
        "segments": [
            {"segment_id": f"{index:064x}", "axes": axes, "confidence": 2} for index in range(1, 13)
        ],
        "track_summary": axes,
        "transition_sample_indices": [100, 200],
    }


def test_exact_twelve_segment_rater_schema() -> None:
    VALIDATOR.validate(_valid())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update({"participant_user_id": "00000000-0000-4000-8000-000000000003"}),
        lambda row: row["segments"].pop(),
        lambda row: row["segments"][0]["axes"].update({"CALM_ENERGETIC": True}),
        lambda row: row["segments"][0]["axes"].pop("CALM_ENERGETIC"),
        lambda row: row["transition_sample_indices"].append(-1),
    ],
)
def test_rater_schema_rejects_extra_identity_missing_axes_and_bad_scores(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    row = deepcopy(_valid())
    mutate(row)
    with pytest.raises(ValidationError):
        VALIDATOR.validate(row)
