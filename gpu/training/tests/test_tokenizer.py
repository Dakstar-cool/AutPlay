"""Learned Semantic-ID tokenizer determinism and bounds."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import numpy as np
import pytest
import rfc8785
from autplay.domain.recommendations import JsonValue
from autplay_sona_training.tokenizer import (
    SONA_MAX_EMBEDDING_DIMENSIONS,
    SONA_MAX_TOKENIZER_MANIFEST_BYTES,
    compute_source_embeddings_sha256,
    fit_residual_tokenizer,
    load_sona_tokenizer,
    materialize_sona_tokenizer,
)

TRACKS = tuple(UUID(int=value) for value in (4, 1, 3, 2))
EMBEDDINGS = np.asarray(
    (
        (1.0, 0.0, 0.2),
        (0.9, 0.1, 0.0),
        (0.0, 1.0, 0.2),
        (0.1, 0.9, 0.0),
    ),
    dtype=np.float32,
)


def test_residual_tokenizer_is_learned_deterministic_and_order_independent() -> None:
    source_sha256 = compute_source_embeddings_sha256(TRACKS, EMBEDDINGS)
    first = fit_residual_tokenizer(
        TRACKS,
        EMBEDDINGS,
        source_embeddings_sha256=source_sha256,
        codebook_size=4,
        iterations=10,
        seed=7,
    )
    order = (2, 0, 3, 1)
    second = fit_residual_tokenizer(
        tuple(TRACKS[index] for index in order),
        EMBEDDINGS[np.asarray(order)],
        source_embeddings_sha256=source_sha256,
        codebook_size=4,
        iterations=10,
        seed=7,
    )

    assert first.recording_ids == tuple(sorted(TRACKS, key=lambda value: value.hex))
    assert first.semantic_ids == second.semantic_ids
    assert first.manifest_sha256 == second.manifest_sha256
    assert np.array_equal(first.centroids, second.centroids)
    assert all(all(code > 0 for code in semantic_id.values) for semantic_id in first.semantic_ids)


def test_residual_tokenizer_rejects_duplicate_or_non_finite_inputs() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        fit_residual_tokenizer(
            (TRACKS[0], TRACKS[0]),
            EMBEDDINGS[:2],
            source_embeddings_sha256="a" * 64,
        )
    invalid = EMBEDDINGS.copy()
    invalid[0, 0] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        fit_residual_tokenizer(
            TRACKS,
            invalid,
            source_embeddings_sha256="a" * 64,
        )


def test_residual_tokenizer_rejects_false_source_hash_and_unbounded_dimensions() -> None:
    with pytest.raises(ValueError, match="canonical embedding bytes"):
        fit_residual_tokenizer(
            TRACKS,
            EMBEDDINGS,
            source_embeddings_sha256="a" * 64,
            codebook_size=4,
        )
    oversized = np.zeros((1, SONA_MAX_EMBEDDING_DIMENSIONS + 1), dtype=np.float32)
    with pytest.raises(ValueError, match="outside bounds"):
        compute_source_embeddings_sha256((TRACKS[0],), oversized)


def test_tokenizer_loader_rejects_invalid_fit_parameters_and_uncovered_codes(
    tmp_path: Path,
) -> None:
    fit = fit_residual_tokenizer(
        TRACKS,
        EMBEDDINGS,
        source_embeddings_sha256=compute_source_embeddings_sha256(TRACKS, EMBEDDINGS),
        codebook_size=4,
        iterations=10,
        seed=7,
    )
    parameters_directory = tmp_path / "invalid-parameters"
    materialize_sona_tokenizer(fit, parameters_directory)
    envelope = _load_json_object(parameters_directory / "manifest.json")
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest["iterations"] = 0
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    (parameters_directory / "manifest.json").write_bytes(rfc8785.dumps(envelope))
    with pytest.raises(ValueError, match="outside canonical bounds"):
        load_sona_tokenizer(parameters_directory)

    mapping_directory = tmp_path / "invalid-mapping"
    materialize_sona_tokenizer(fit, mapping_directory)
    mapping_path = mapping_directory / "mapping.json"
    mapping = cast(list[JsonValue], json.loads(mapping_path.read_bytes()))
    first_entry = cast(dict[str, JsonValue], mapping[0])
    semantic_id = cast(list[JsonValue], first_entry["semantic_id"])
    semantic_id[0] = fit.centroids.shape[1] + 1
    mapping_path.write_bytes(rfc8785.dumps(mapping))
    envelope = _load_json_object(mapping_directory / "manifest.json")
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest["mapping_sha256"] = sha256(mapping_path.read_bytes()).hexdigest()
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    (mapping_directory / "manifest.json").write_bytes(rfc8785.dumps(envelope))
    with pytest.raises(ValueError, match="without a centroid"):
        load_sona_tokenizer(mapping_directory)

    oversized_directory = tmp_path / "oversized-manifest"
    materialize_sona_tokenizer(fit, oversized_directory)
    (oversized_directory / "manifest.json").write_bytes(
        b"x" * (SONA_MAX_TOKENIZER_MANIFEST_BYTES + 1)
    )
    with pytest.raises(ValueError, match="manifest exceeds"):
        load_sona_tokenizer(oversized_directory)


def _load_json_object(path: Path) -> dict[str, JsonValue]:
    value = cast(JsonValue, json.loads(path.read_bytes()))
    if not isinstance(value, dict):
        raise AssertionError("test fixture JSON must be an object")
    return value
