from __future__ import annotations

import hashlib
import struct

import pytest
from autplay.application.identity_evidence import (
    IdentityDocumentError,
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_gate_metadata,
    canonical_query_snapshot,
    validate_candidate_evidence_total,
)


def test_query_snapshot_uses_rfc8785_and_sha256() -> None:
    document = canonical_query_snapshot(
        {
            "normalized_title": "track",
            "normalized_artists": ["исполнитель"],
            "duration_ms": 123_456,
            "evidence_ids": ["opaque:1"],
        }
    )

    assert document.canonical_bytes.startswith(b'{"duration_ms":123456')
    assert document.sha256 == hashlib.sha256(document.canonical_bytes).digest()
    assert document.byte_size == len(document.canonical_bytes)


@pytest.mark.parametrize(
    "value",
    [
        {"token": "hidden"},
        {"normalized_title": "x", "nested": {"password": "hidden"}},
        {"normalized_title": "x", "evidence_ids": [{"private_url": "hidden"}]},
        {"normalized_title": "x", "evidence_ids": [{"privateUrl": "hidden"}]},
        {"normalized_title": "x", "evidence_ids": [{"raw-path": "hidden"}]},
        {"unknown_key": "not allowed"},
    ],
)
def test_query_snapshot_rejects_sensitive_or_unknown_fields(value: dict[str, object]) -> None:
    with pytest.raises(IdentityDocumentError):
        canonical_query_snapshot(value)  # type: ignore[arg-type]


def test_candidate_evidence_preserves_unknown_feature_keys() -> None:
    document = canonical_candidate_evidence(
        {
            "recording_id": "00000000-0000-0000-0000-000000000001",
            "raw_score": 0.5,
            "confidence": 0.7,
            "evidence_tier": "T2",
            "feature_scores": [{"feature": "future_feature", "present": True, "value": 0.5}],
            "hard_conflicts": [],
            "candidate_origins": [{"generator": "opaque", "rank": 1}],
            "extractor_versions": {"future_feature": "feature/99"},
        }
    )

    feature_scores = document.value["feature_scores"]
    assert isinstance(feature_scores, list)
    first_feature = feature_scores[0]
    assert isinstance(first_feature, dict)
    assert first_feature["feature"] == "future_feature"


def test_gate_metadata_v1_is_explicitly_empty_and_privacy_safe() -> None:
    document = canonical_gate_metadata({})
    assert document.canonical_bytes == b"{}"

    for value in ({"providerPayload": "secret"}, {"unversioned_key": "value"}):
        with pytest.raises(IdentityDocumentError):
            canonical_gate_metadata(value)


def test_candidate_aggregate_matches_postgresql_byte_contract() -> None:
    first = bytes(range(32))
    second = bytes(reversed(range(32)))
    digest, size = candidate_aggregate_sha256([(1, first), (2, second)])
    expected = struct.pack("!i", 1) + first + struct.pack("!i", 2) + second

    assert digest == hashlib.sha256(expected).digest()
    assert size == 72


def test_candidate_aggregate_rejects_rank_gap() -> None:
    with pytest.raises(IdentityDocumentError, match="contiguous"):
        candidate_aggregate_sha256([(2, bytes(32))])


@pytest.mark.parametrize(
    ("delta", "sizes"),
    (
        (-1, [131_072] * 31 + [65_536, 65_535]),
        (0, [131_072] * 31 + [65_536, 65_536]),
        (1, [131_072] * 31 + [65_536, 65_537]),
    ),
)
def test_candidate_evidence_total_uses_exact_four_mib_boundary(
    delta: int, sizes: list[int]
) -> None:
    if delta <= 0:
        assert validate_candidate_evidence_total(sizes) == 4 * 1024 * 1024 + delta
    else:
        with pytest.raises(IdentityDocumentError, match="exceeds 4 MiB"):
            validate_candidate_evidence_total(sizes)
