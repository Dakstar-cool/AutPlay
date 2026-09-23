"""Golden and rejection tests for the exact Sona process identity."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from autplay.domain.sona_execution_profile import (
    SONA_EXECUTION_PROFILE_DOMAIN,
    freeze_sona_execution_profile,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/ml/sona-execution-profile-v1.json"
GOLDEN_SHA256 = "56519bb2e37938b730a6912afb15eb9a4d5bde5ef70aba74012ad3e1d484a283"


def _document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_golden_profile_hashes_exact_domain_separated_jcs_bytes() -> None:
    source = _document()
    profile = freeze_sona_execution_profile(source)
    assert profile.profile_sha256 == GOLDEN_SHA256
    assert (
        sha256(SONA_EXECUTION_PROFILE_DOMAIN + profile.canonical_bytes).hexdigest() == GOLDEN_SHA256
    )
    assert len(profile.canonical_bytes) == 1446
    assert profile.canonical_bytes.startswith(b'{"compute_capability":"8.9"')
    source["python_version"] = "mutated"
    profile.document["python_version"] = "also mutated"
    assert profile.document["python_version"] == "3.13.7"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nvidia_driver_version", "581.00"),
        ("gpu_device_uuid", "GPU-other"),
        ("precision", "FP16"),
        ("runtime_adapter_sha256", "f" * 64),
        ("postprocessor_sha256", "f" * 64),
        ("source_commit", "f" * 40),
        ("onnxruntime_version", "1.23.0"),
    ],
)
def test_output_affecting_changes_create_successor_profile(field: str, value: str) -> None:
    document = _document()
    document[field] = value
    successor = freeze_sona_execution_profile(document)
    assert successor.profile_sha256 != GOLDEN_SHA256


def test_object_key_order_does_not_change_identity() -> None:
    document = _document()
    assert (
        freeze_sona_execution_profile(dict(reversed(list(document.items())))).profile_sha256
        == GOLDEN_SHA256
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_fallback_allowed", True),
        ("execution_provider", "CPUExecutionProvider"),
        ("gpu_image_digest", "sha256:" + "A" * 64),
        ("source_commit", "not-a-commit"),
        ("precision", "INT8"),
        ("thread_limits", {"intra_op": 0, "inter_op": 1, "max_concurrent_requests": 1}),
        ("provider_options", {"device_id": -1}),
        ("tensor_schemas", {"inputs": [], "outputs": []}),
        ("determinism", {"enabled": False, "seed": 17, "flags": []}),
    ],
)
def test_missing_or_invalid_execution_authority_fails_closed(field: str, value: object) -> None:
    document = _document()
    document[field] = value
    with pytest.raises(ValueError):
        freeze_sona_execution_profile(document)


def test_nested_schema_and_unknown_field_fail_closed() -> None:
    document = _document()
    document["unreviewed_field"] = "x"
    with pytest.raises(ValueError):
        freeze_sona_execution_profile(document)
    document = _document()
    del document["cuda_version"]
    with pytest.raises(ValueError):
        freeze_sona_execution_profile(document)
    document = deepcopy(_document())
    document["tensor_schemas"]["inputs"][0]["shape"][0] = 0
    with pytest.raises(ValueError):
        freeze_sona_execution_profile(document)
